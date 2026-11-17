"""Statistics behind ``decompose_change``: Benjamini-Hochberg and the two-proportion test.

``statsmodels`` is used only as an independent reference here, never inside the analytics.
"""
from __future__ import annotations

import math

import numpy as np
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from statsmodels.stats.multitest import multipletests
from statsmodels.stats.proportion import confint_proportions_2indep, proportions_ztest

from causelens.analytics.decompose import benjamini_hochberg, two_proportion_test

# ------------------------------------------------------------------ Benjamini-Hochberg


def test_bh_matches_a_hand_computed_example():
    # p ascending: .001 .01 .03 .04 .5 ; m=5 -> scaled .005 .025 .05 .05 .5 ; cumulative min from the top
    q = benjamini_hochberg([0.04, 0.001, 0.5, 0.03, 0.01])
    assert q == pytest.approx([0.05, 0.005, 0.5, 0.05, 0.025])


def test_bh_equal_p_values_get_equal_q_values_and_q_is_capped_at_one():
    q = benjamini_hochberg([0.2, 0.2, 0.2, 0.9, 1.0])
    assert q[0] == q[1] == q[2] and max(q) <= 1.0 and q[4] == 1.0


def test_bh_edge_cases():
    assert benjamini_hochberg([]) == []
    assert benjamini_hochberg([0.03]) == [0.03]
    assert benjamini_hochberg([0.0, 0.0]) == [0.0, 0.0]


@pytest.mark.parametrize("bad", [[-0.1], [1.1], [float("nan")], [0.2, float("nan")]])
def test_bh_rejects_invalid_p_values(bad):
    with pytest.raises(ValueError, match=r"\[0, 1\]"):
        benjamini_hochberg(bad)


@settings(max_examples=200, deadline=None, derandomize=True)
@given(st.lists(st.floats(0, 1), min_size=1, max_size=60))
def test_bh_agrees_with_statsmodels_and_has_the_defining_properties(p):
    q = np.array(benjamini_hochberg(p))
    ref = multipletests(p, method="fdr_bh")[1]
    assert np.allclose(q, ref, rtol=1e-12, atol=1e-15)
    arr = np.array(p)
    assert np.all(q >= arr - 1e-15) and np.all(q <= 1.0)  # adjusting never lowers a p-value
    order = np.argsort(arr, kind="stable")
    assert np.all(np.diff(q[order]) >= -1e-15)  # monotone in p
    perm = np.random.default_rng(0).permutation(len(p))
    assert np.allclose(np.array(benjamini_hochberg(arr[perm].tolist())), q[perm])  # order-invariant


def test_bh_controls_the_false_discovery_rate_by_simulation():
    """Mix of 80 true nulls and 20 real effects: the false discovery proportion averages below 0.05."""
    rng = np.random.default_rng(11)
    fdp = []
    for _ in range(400):
        null_p = rng.uniform(size=80)
        alt_p = rng.beta(0.2, 40, size=20)  # tiny p-values
        q = np.array(benjamini_hochberg(np.r_[null_p, alt_p].tolist()))
        rejected = q < 0.05
        fdp.append(rejected[:80].sum() / max(rejected.sum(), 1))
    assert np.mean(fdp) <= 0.05 + 0.01


# ------------------------------------------------------------------ two-proportion test


@pytest.mark.parametrize(
    ("c0", "n0", "c1", "n1"),
    [(120, 4000, 90, 3500), (3, 50, 9, 60), (1000, 10**6, 1100, 10**6), (0, 100, 5, 100), (50, 50, 40, 50)],
)
def test_matches_statsmodels_z_test_and_newcombe_interval(c0, n0, c1, n1):
    r = two_proportion_test(c0, n0, c1, n1)
    pooled = (c0 + c1) / (n0 + n1)
    assert 0 < pooled < 1 or r.z_statistic is None
    z, p = proportions_ztest([c1, c0], [n1, n0])
    assert r.z_statistic == pytest.approx(z, rel=1e-10) and r.p_value_binomial == pytest.approx(p, rel=1e-10)
    assert r.p_value == r.p_value_binomial  # no dispersion supplied
    lo, hi = confint_proportions_2indep(c1, n1, c0, n0, method="newcomb", compare="diff")
    assert r.change_pp.lower == pytest.approx(100 * lo, abs=1e-9) and r.change_pp.upper == pytest.approx(100 * hi, abs=1e-9)
    assert r.change_pp.estimate == pytest.approx(100 * (c1 / n1 - c0 / n0))
    assert r.change_pp.lower <= r.change_pp.estimate <= r.change_pp.upper


def test_identical_rates_give_p_one_and_a_zero_centred_interval():
    r = two_proportion_test(30, 1000, 60, 2000)
    assert r.p_value == pytest.approx(1.0) and r.change_pp.estimate == 0
    assert r.change_pp.lower < 0 < r.change_pp.upper


@pytest.mark.parametrize(("c0", "c1"), [(0, 0), (500, 500)])
def test_zero_variance_pooled_rate_is_p_one_without_nan(c0, c1):
    r = two_proportion_test(c0, 500, c1, 500)
    assert r.z_statistic is None and r.p_value == r.p_value_binomial == 1.0
    assert "nan" not in r.model_dump_json().lower()


def test_dispersion_widens_the_test_and_the_interval_and_never_narrows_it():
    plain = two_proportion_test(120, 4000, 90, 3500)
    wide = two_proportion_test(120, 4000, 90, 3500, dispersion_pre=2.0, dispersion_post=1.5)
    assert wide.p_value > plain.p_value and wide.p_value_binomial == plain.p_value_binomial
    assert (wide.change_pp.upper - wide.change_pp.lower) > (plain.change_pp.upper - plain.change_pp.lower)
    # pooled-variance inflation, recomputed by hand
    pooled = 210 / 7500
    se = math.sqrt(pooled * (1 - pooled) * (2.0 / 4000 + 1.5 / 3500))
    assert wide.z_statistic == pytest.approx((90 / 3500 - 120 / 4000) / se)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"count_pre": 5, "n_pre": 0, "count_post": 1, "n_post": 10},
        {"count_pre": -1, "n_pre": 10, "count_post": 1, "n_post": 10},
        {"count_pre": 11, "n_pre": 10, "count_post": 1, "n_post": 10},
        {"count_pre": 1, "n_pre": 10, "count_post": 1, "n_post": 10, "dispersion_pre": 0.5},
        {"count_pre": 1, "n_pre": 10, "count_post": 1, "n_post": 10, "level": 1.0},
    ],
)
def test_two_proportion_test_rejects_invalid_arguments(kwargs):
    with pytest.raises(ValueError):
        two_proportion_test(**kwargs)


def test_small_sample_flag_marks_unreliable_normal_approximations():
    assert two_proportion_test(2, 100, 3, 100).small_sample
    assert not two_proportion_test(200, 1000, 300, 1000).small_sample


def test_p_values_are_uniform_under_the_null_by_simulation():
    """Equal true rates: about 5% of tests fall below 0.05 and the p-values are roughly uniform."""
    rng = np.random.default_rng(5)
    p = np.array([
        two_proportion_test(int(rng.binomial(5000, 0.03)), 5000, int(rng.binomial(5000, 0.03)), 5000).p_value
        for _ in range(1500)
    ])
    assert 0.03 <= (p < 0.05).mean() <= 0.07
    assert 0.44 <= (p < 0.5).mean() <= 0.56


def test_newcombe_interval_covers_the_true_difference_at_about_its_level():
    rng = np.random.default_rng(6)
    covered = 0
    trials = 1500
    for _ in range(trials):
        c0, c1 = int(rng.binomial(2000, 0.03)), int(rng.binomial(2000, 0.04))
        ci = two_proportion_test(c0, 2000, c1, 2000).change_pp
        covered += ci.lower <= 1.0 <= ci.upper
    assert 0.93 <= covered / trials <= 0.975
