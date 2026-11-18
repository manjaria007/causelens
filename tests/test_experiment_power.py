"""power_analysis (Task 6.1) and its monotonicity properties (Task 6.3). Fast tests only; Monte Carlo lives in
tests/calibration/run_power_mc.py."""
from __future__ import annotations

import json
import math

import numpy as np
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from scipy import stats
from statsmodels.stats.proportion import power_proportions_2indep

from causelens.analytics import normal_approx_power, power_analysis


def sm_power(baseline: float, mde_pp: float, n_control: int, n_treatment: int, alpha: float) -> float:
    """statsmodels power for a treatment-minus-control difference. Sample 1 is the treatment arm, so
    nobs1 = n_treatment and ratio = nobs2 / nobs1 = n_control / n_treatment."""
    return float(power_proportions_2indep(
        diff=mde_pp / 100.0, prop2=baseline, nobs1=n_treatment, ratio=n_control / n_treatment, alpha=alpha, return_results=False
    ))


def own_power(p0: float, p1: float, nc: int, nt: int, alpha: float) -> float:
    """An independent restatement of the pooled-null / unpooled-alternative two-sided normal power."""
    d = abs(p1 - p0)
    z = stats.norm.ppf(1 - alpha / 2)
    pbar = (nc * p0 + nt * p1) / (nc + nt)
    s0 = math.sqrt(pbar * (1 - pbar) * (1 / nc + 1 / nt))
    s1 = math.sqrt(p0 * (1 - p0) / nc + p1 * (1 - p1) / nt)
    return stats.norm.cdf((d - z * s0) / s1) + stats.norm.cdf((-d - z * s0) / s1)


# ------------------------------------------------------------------ units and the reported quantities


def test_units_baseline_is_a_fraction_and_mde_is_signed_percentage_points():
    r = power_analysis(0.05, 0.5)
    assert r.baseline == 0.05 and r.mde_pp == 0.5 and r.target_rate == pytest.approx(0.055)
    down = power_analysis(0.05, -0.5)
    assert down.target_rate == pytest.approx(0.045) and down.mde_pp == -0.5


def test_a_one_point_lift_on_five_percent_needs_8158_per_arm():
    r = power_analysis(0.05, 1.0)
    assert (r.n_control, r.n_treatment, r.n_total_per_comparison) == (8158, 8158, 16316)
    assert r.achieved_power >= 0.8 > r.previous_pair_power
    assert r.approximation_reliable and r.warnings == []


def test_achieved_power_is_labelled_as_a_normal_approximation_not_exact_power():
    r = power_analysis(0.05, 1.0)
    assert r.achieved_power_label == "achieved power under the normal approximation"
    fields = " ".join(type(r).model_fields)
    assert "exact" not in fields and "achieved_power" in fields
    assert "normal approximation" in r.method


def test_the_result_is_json_safe_and_deterministic():
    a, b = power_analysis(0.04, -0.6, allocation_ratio=2.0, n_comparisons=2), power_analysis(0.04, -0.6, allocation_ratio=2.0, n_comparisons=2)
    assert a == b
    text = a.model_dump_json()
    assert "NaN" not in text and "Infinity" not in text
    assert json.loads(text)["n_comparisons"] == 2


# ------------------------------------------------------------------ agreement with statsmodels and an independent formula


CASES = [(0.05, 1.0, 0.05, 0.8, 1.0, 1), (0.05, -1.0, 0.05, 0.8, 1.0, 1), (0.01, 0.2, 0.05, 0.9, 1.0, 1), (0.30, 3.0, 0.01, 0.8, 1.0, 1),
         (0.04, -0.6, 0.05, 0.8, 2.0, 1), (0.04, -0.6, 0.05, 0.8, 0.5, 1), (0.04, 0.6, 0.05, 0.8, 1.0, 3), (0.2, 5.0, 0.10, 0.95, 1.5, 2),
         (0.5, 2.0, 0.05, 0.8, 1.0, 1), (0.9, -2.0, 0.05, 0.8, 3.0, 1), (0.0405, -0.66, 0.05, 0.8, 1.0, 1)]


@pytest.mark.parametrize("baseline, mde, alpha, pw, ratio, k", CASES)
def test_power_matches_statsmodels_and_an_independent_formula_at_the_integer_sizes(baseline, mde, alpha, pw, ratio, k):
    r = power_analysis(baseline, mde, alpha=alpha, power=pw, allocation_ratio=ratio, n_comparisons=k)
    a = alpha / k
    assert r.n_treatment == math.ceil(ratio * r.n_control - 1e-12)  # explicit integer allocation
    for n_c, n_t in ((r.n_control, r.n_treatment), (r.n_control * 2 + 1, math.ceil(ratio * (r.n_control * 2 + 1)))):
        sm = sm_power(baseline, mde, n_c, n_t, a)
        assert own_power(baseline, baseline + mde / 100, n_c, n_t, a) == pytest.approx(sm, abs=1e-12)
        assert normal_approx_power(baseline, mde, n_c, n_t, alpha=alpha, n_comparisons=k) == pytest.approx(sm, abs=1e-12)
    assert r.achieved_power == pytest.approx(sm_power(baseline, mde, r.n_control, r.n_treatment, a), abs=1e-12)


@pytest.mark.parametrize("baseline, mde, alpha, pw, ratio, k", CASES)
def test_the_pair_is_minimal_against_the_previous_feasible_pair_and_a_scan_below(baseline, mde, alpha, pw, ratio, k):
    r = power_analysis(baseline, mde, alpha=alpha, power=pw, allocation_ratio=ratio, n_comparisons=k)
    a = alpha / k
    assert sm_power(baseline, mde, r.n_control, r.n_treatment, a) >= pw
    previous = (r.n_control - 1, max(1, math.ceil(ratio * (r.n_control - 1) - 1e-12)))
    assert sm_power(baseline, mde, *previous, a) < pw
    for n in range(max(1, r.n_control - 40), r.n_control):  # nothing smaller reaches the target
        assert sm_power(baseline, mde, n, max(1, math.ceil(ratio * n - 1e-12)), a) < pw, n
    assert r.previous_pair_power == pytest.approx(sm_power(baseline, mde, *previous, a), abs=1e-12)


def test_unequal_allocation_maps_the_arm_ratio_the_right_way_round():
    """Treatment gets twice the control sessions; swapping the mapping would give a different power."""
    r = power_analysis(0.04, -0.6, allocation_ratio=2.0)
    right = sm_power(0.04, -0.6, r.n_control, r.n_treatment, 0.05)
    swapped = float(power_proportions_2indep(diff=-0.006, prop2=0.04, nobs1=r.n_control, ratio=r.n_treatment / r.n_control, return_results=False))
    assert r.n_treatment == 2 * r.n_control and right == pytest.approx(r.achieved_power, abs=1e-12)
    assert abs(right - swapped) > 1e-4


def test_several_primary_comparisons_divide_alpha_and_label_power_per_comparison():
    one, three = power_analysis(0.04, -0.6), power_analysis(0.04, -0.6, n_comparisons=3)
    assert three.alpha_per_comparison == pytest.approx(0.05 / 3) and three.n_comparisons == 3
    assert three.n_control > one.n_control
    assert "per comparison" in type(three).model_fields["target_power"].description.lower()
    assert any("per comparison" in a and "not the probability" in a for a in three.assumptions)
    assert any("Bonferroni" in a for a in three.assumptions)


def test_a_sparse_design_is_flagged_as_unreliable_for_the_normal_approximation():
    r = power_analysis(0.001, -0.09)
    assert not r.approximation_reliable and r.min_expected_count < 10
    assert r.warnings and "unreliable" in r.warnings[0]
    assert power_analysis(0.05, 1.0).approximation_reliable


def test_an_effect_too_small_to_design_for_raises():
    with pytest.raises(ValueError, match="too small"):
        power_analysis(0.05, 1e-7)


# ------------------------------------------------------------------ validation


@pytest.mark.parametrize(
    "kwargs, match",
    [
        ({"baseline": 0.0}, "baseline"), ({"baseline": 1.0}, "baseline"), ({"baseline": -0.1}, "baseline"),
        ({"baseline": 5.0}, "fraction"), ({"baseline": float("nan")}, "finite"), ({"baseline": float("inf")}, "finite"),
        ({"baseline": True}, "real number"), ({"baseline": "0.05"}, "real number"), ({"baseline": None}, "real number"),
        ({"mde_pp": 0.0}, "non-zero"), ({"mde_pp": float("nan")}, "finite"), ({"mde_pp": float("inf")}, "finite"),
        ({"mde_pp": -5.0}, "target_rate"), ({"mde_pp": 96.0}, "target_rate"), ({"mde_pp": True}, "real number"),
        ({"alpha": 0.0}, "alpha"), ({"alpha": -0.05}, "alpha"), ({"alpha": 0.6}, "alpha"), ({"alpha": float("nan")}, "finite"),
        ({"power": 0.0}, "power"), ({"power": 1.0}, "power"), ({"power": 0.04}, "power"), ({"power": float("nan")}, "finite"),
        ({"allocation_ratio": 0.0}, "allocation_ratio"), ({"allocation_ratio": -1.0}, "allocation_ratio"),
        ({"allocation_ratio": 1000.0}, "allocation_ratio"), ({"allocation_ratio": float("nan")}, "finite"),
        ({"n_comparisons": 0}, "at least 1"), ({"n_comparisons": 1.5}, "integer"), ({"n_comparisons": True}, "integer"),
        ({"alpha": 0.05, "power": 0.004, "n_comparisons": 10}, "power"),
    ],
)
def test_invalid_inputs_raise_naming_the_problem(kwargs, match):
    args = {"baseline": 0.05, "mde_pp": 1.0, "alpha": 0.05, "power": 0.8, "allocation_ratio": 1.0, "n_comparisons": 1} | kwargs
    with pytest.raises(ValueError, match=match):
        power_analysis(args.pop("baseline"), args.pop("mde_pp"), **args)


def test_the_resulting_target_rate_error_explains_the_unit():
    with pytest.raises(ValueError, match="percentage points, not a fraction"):
        power_analysis(0.05, -5.0)


def test_numpy_scalars_are_accepted_but_booleans_are_not():
    assert power_analysis(np.float64(0.05), np.float32(1.0), n_comparisons=np.int64(1)).n_control == power_analysis(0.05, 1.0).n_control
    with pytest.raises(ValueError):
        power_analysis(0.05, 1.0, n_comparisons=np.bool_(True))


def test_normal_approx_power_validates_sizes():
    for bad in (0, -1, 1.5, True):
        with pytest.raises(ValueError):
            normal_approx_power(0.05, 1.0, bad, 100)


# ------------------------------------------------------------------ monotonicity (Task 6.3)


@settings(max_examples=120, deadline=None)
@given(p0=st.floats(0.01, 0.6), d1=st.floats(0.05, 3.0), extra=st.floats(0.01, 3.0), up=st.booleans(),
       power=st.sampled_from([0.7, 0.8, 0.9]), ratio=st.sampled_from([0.5, 1.0, 2.0]))
def test_property_required_n_does_not_increase_as_the_effect_grows(p0, d1, extra, up, power, ratio):
    sign = 1 if up else -1
    d2 = d1 + extra
    if not (0 < p0 + sign * d2 / 100 < 1):
        return
    small = power_analysis(p0, sign * d1, power=power, allocation_ratio=ratio)
    big = power_analysis(p0, sign * d2, power=power, allocation_ratio=ratio)
    assert big.n_control <= small.n_control


@settings(max_examples=120, deadline=None)
@given(p0=st.floats(0.01, 0.6), d=st.floats(0.1, 3.0), up=st.booleans(), p1=st.floats(0.55, 0.9), extra=st.floats(0.01, 0.09),
       alpha=st.sampled_from([0.01, 0.05, 0.1]), ratio=st.sampled_from([0.5, 1.0, 2.0]))
def test_property_required_n_does_not_decrease_as_power_rises(p0, d, up, p1, extra, alpha, ratio):
    mde = d if up else -d
    if not 0 < p0 + mde / 100 < 1:
        return
    lower = power_analysis(p0, mde, alpha=alpha, power=p1, allocation_ratio=ratio)
    higher = power_analysis(p0, mde, alpha=alpha, power=min(0.99, p1 + extra), allocation_ratio=ratio)
    assert higher.n_control >= lower.n_control
    assert higher.achieved_power >= higher.target_power and lower.achieved_power >= lower.target_power


@settings(max_examples=80, deadline=None)
@given(p0=st.floats(0.01, 0.6), d=st.floats(0.1, 3.0), up=st.booleans(), k=st.integers(1, 6), ratio=st.sampled_from([0.5, 1.0, 2.0]))
def test_property_a_stricter_alpha_never_needs_fewer_sessions_and_the_pair_is_minimal(p0, d, up, k, ratio):
    mde = d if up else -d
    if not 0 < p0 + mde / 100 < 1:
        return
    base = power_analysis(p0, mde, allocation_ratio=ratio)
    strict = power_analysis(p0, mde, allocation_ratio=ratio, n_comparisons=k)
    assert strict.n_control >= base.n_control
    assert strict.achieved_power >= strict.target_power
    if strict.n_control > 1:
        assert strict.previous_pair_power < strict.target_power
