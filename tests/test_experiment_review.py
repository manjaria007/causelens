"""Regression tests for the PR #6 review (five design findings and two power-API edges)."""
from __future__ import annotations

import math
import warnings

import numpy as np
import pytest
from scenarios import CHECKOUT, HERO, POST, run_pipeline, scenario_data
from scipy import stats

from causelens.analytics import (
    check_data_quality,
    design_experiment,
    normal_approx_power,
    power_analysis,
)
from causelens.analytics.confirm import Interval
from causelens.analytics.did import estimate_did

warnings.filterwarnings("ignore")

FOCAL = {"device": "mobile", "browser": "safari"}


@pytest.fixture(scope="module")
def planted():
    metrics, events = scenario_data(42, "planted")
    _, res = run_pipeline(metrics, events)
    return metrics, events, res


@pytest.fixture(scope="module")
def same_day():
    metrics, events = scenario_data(2, "same_day")
    _, res = run_pipeline(metrics, events)
    return metrics, events, res


def cand(res, name):
    return next(c for c in res.candidates if c.event == name)


def with_effect(res, name, lower, upper, ref):
    """Give one candidate a stored differential effect with the given interval (a copy; nothing is mutated)."""
    did = ref.model_copy(update={"effect_pp": Interval(estimate=(lower + upper) / 2, lower=lower, upper=upper), "status": "ok"})
    return res.model_copy(update={"candidates": [c.model_copy(update={"differential_effect": did}) if c.event == name else c for c in res.candidates]})


# ------------------------------------------------------------------ 1. an RCT does not need an observational control


def test_01_planning_does_not_need_comparison_traffic(planted):
    metrics, events, res = planted
    full = design_experiment(metrics, res, POST, events=events, mde_pp=0.4)
    focal_only = metrics[(metrics.device == "mobile") & (metrics.browser == "safari")]
    assert not (focal_only.device != "mobile").any() and len(focal_only) < len(metrics)
    d = design_experiment(focal_only, res, POST, events=events, mde_pp=0.4)
    assert d.status == "ok", d.reason
    assert d.baseline == full.baseline and d.traffic == full.traffic and d.plan == full.plan  # none of it uses the control


def test_01_a_stored_estimate_still_sizes_the_experiment_without_comparison_traffic(planted):
    metrics, events, res = planted
    focal_only = metrics[(metrics.device == "mobile") & (metrics.browser == "safari")]
    d = design_experiment(focal_only, res, POST, events=events)
    assert d.status == "ok" and d.mde.source == "provisional_planning_heuristic" and d.mde.mde_pp == pytest.approx(0.5847848071580427)


def test_01_without_a_control_and_without_a_stored_estimate_the_gap_is_the_mde_not_the_traffic(same_day):
    metrics, events, res = same_day
    focal_only = metrics[(metrics.device == "mobile") & (metrics.browser == "safari")]
    d = design_experiment(focal_only, res, POST, events=events, target_candidate=CHECKOUT)
    assert d.status == "needs_input" and d.needs == ["mde_pp"]
    assert d.baseline is not None and d.baseline.n_days == 18 and d.traffic is not None
    assert design_experiment(focal_only, res, POST, events=events, target_candidate=CHECKOUT, mde_pp=0.4).status == "ok"


def test_01_the_outside_traffic_monitor_says_when_there_is_none(planted):
    metrics, events, res = planted
    focal_only = metrics[(metrics.device == "mobile") & (metrics.browser == "safari")]
    d = design_experiment(focal_only, res, POST, events=events, mde_pp=0.4)
    ext = next(g for g in d.guardrails if g.kind == "external_monitor")
    assert not ext.computable_from_current_data and ext.reference["sessions"] == 0 and "no sessions outside" in ext.detail


def test_01_flagged_days_are_still_removed_without_comparison_traffic(planted):
    metrics, events, res = planted
    quality = check_data_quality(metrics, events)
    focal_only = metrics[(metrics.device == "mobile") & (metrics.browser == "safari")]
    d = design_experiment(focal_only, res, ("2026-09-14", "2026-10-31"), events=events, quality=quality, mde_pp=0.3)
    assert d.status == "ok" and [str(x) for x in d.baseline.excluded_dates] == ["2026-09-20"] and d.baseline.n_days == 47


# ------------------------------------------------------------------ 2. lag-ambiguous alternatives are kept


def test_02_a_lag_limited_alternative_is_offered_while_the_default_stays_two_arm(planted):
    metrics, events, res = planted
    hero = cand(res, HERO)
    assert hero.verdict == "rejected" and {o.lag_days: o.verdict for o in hero.lag_sensitivity}[1] != "rejected"
    d = design_experiment(metrics, res, POST, events=events, mde_pp=0.4)
    assert [a.role for a in d.plan.arms] == ["control", "rollback"] and d.target == [CHECKOUT]
    alt = d.alternative_plan
    assert alt is not None and alt.n_comparisons == 2 and {a.rollback_of for a in alt.arms[1:]} == {CHECKOUT, HERO}
    hero_arm = next(a for a in alt.arms if a.rollback_of == HERO)
    assert "rejected as the sole immediate explanation" in hero_arm.hypothesis_limit and "lag 1" in hero_arm.hypothesis_limit
    assert next(a for a in alt.arms if a.rollback_of == CHECKOUT).hypothesis_limit is None
    assert "lag 1" in d.alternative_note and "per comparison" in d.alternative_note


def test_02_each_candidate_can_be_requested_for_the_reference_case(planted):
    metrics, events, res = planted
    d = design_experiment(metrics, res, POST, events=events, arms="each_candidate", mde_pp=0.4)
    assert d.status == "ok" and d.plan.n_comparisons == 2 and d.target == [HERO, CHECKOUT]
    assert [a.role for a in d.plan.arms] == ["control", "rollback", "rollback"]
    assert d.plan.power.alpha_per_comparison == pytest.approx(0.025)
    assert any("hypothesis-limited" in a for a in d.assumptions)


def test_02_the_hypothesis_limited_arm_is_sized_at_its_own_open_lag(planted):
    metrics, events, res = planted
    d = design_experiment(metrics, res, POST, events=events, arms="each_candidate")
    effects = {m.event: m for m in d.mde.per_arm}
    assert str(effects[HERO].onset_date) == "2026-10-14"  # Hero at lag 1, not its rejected lag-0 onset 2026-10-13
    ref = estimate_did(metrics, FOCAL, "2026-10-14", events=events).effect_pp
    assert effects[HERO].mde_pp == pytest.approx(-ref.upper)


def test_02_a_candidate_rejected_at_every_lag_is_not_offered(planted):
    metrics, events, _ = planted
    ev = events.copy()
    ev.loc[ev.name == HERO, "scope"] = "device=desktop"  # disjoint from the localized segment: rejected on exposure at any lag
    _, res = run_pipeline(metrics, ev)
    hero = cand(res, HERO)
    assert all(o.verdict == "rejected" for o in hero.lag_sensitivity)
    d = design_experiment(metrics, res, POST, events=ev, mde_pp=0.4)
    assert d.alternative_plan is None and d.alternative_note is None
    with pytest.raises(ValueError, match="at least two"):
        design_experiment(metrics, res, POST, events=ev, arms="each_candidate", mde_pp=0.4)


# ------------------------------------------------------------------ 3. mixed-sign heuristic targets


def mixed(same_day):
    metrics, events, res = same_day
    names = [c.event for c in res.candidates]
    ref = estimate_did(metrics, FOCAL, "2026-10-14", events=events)
    r = with_effect(res, names[0], 0.6, 0.9, ref)  # undoing a gain: -0.600 pp
    r = with_effect(r, names[1], -0.9, -0.601, ref)  # undoing a loss: +0.601 pp
    return metrics, events, r, names


def test_03_every_arm_reaches_the_target_power_for_its_own_target(same_day):
    metrics, events, res, _ = mixed(same_day)
    d = design_experiment(metrics, res, POST, events=events, arms="each_candidate")
    assert d.status == "ok"
    targets = {a.rollback_of: a.mde_pp for a in d.plan.arms if a.role == "rollback"}
    assert sorted(round(v, 3) for v in targets.values()) == [-0.6, 0.601]
    p = d.plan.power
    for arm in d.plan.arms[1:]:
        power = normal_approx_power(d.baseline.rate, arm.mde_pp, p.n_control, p.n_treatment, alpha=0.05, n_comparisons=2)
        assert power >= 0.8 and arm.achieved_power == pytest.approx(power, abs=1e-12)


def test_03_the_arm_that_needs_the_most_sessions_sets_the_size(same_day):
    metrics, events, res, _ = mixed(same_day)
    d = design_experiment(metrics, res, POST, events=events, arms="each_candidate")
    own = {a.rollback_of: power_analysis(d.baseline.rate, a.mde_pp, n_comparisons=2).n_control for a in d.plan.arms[1:]}
    assert d.plan.power.n_control == max(own.values()) and len(set(own.values())) == 2
    assert d.mde.mde_pp == pytest.approx(next(a.mde_pp for a in d.plan.arms[1:] if own[a.rollback_of] == max(own.values())))
    assert [m.mde_pp for m in d.mde.per_arm] and {m.event for m in d.mde.per_arm} == set(own)
    assert any("own target" in c for c in d.mde.caveats)


def test_03_a_declared_target_applies_to_every_arm(same_day):
    metrics, events, res = same_day
    d = design_experiment(metrics, res, POST, events=events, arms="each_candidate", mde_pp=0.4)
    assert {a.mde_pp for a in d.plan.arms[1:]} == {0.4} and all(a.achieved_power >= 0.8 for a in d.plan.arms[1:])


# ------------------------------------------------------------------ 4. wording of a null result


def test_04_a_result_that_detects_nothing_is_not_called_absence(planted):
    metrics, events, res = planted
    for kwargs in ({}, {"arms": "each_candidate", "mde_pp": 0.4}):
        d = design_experiment(metrics, res, POST, events=events, **kwargs)
        h = d.hypothesis
        assert "did not detect" in h and "does not show" in h and "equivalence" in h and "confidence interval" in h
        assert "would mean the rollback does not change" not in h and "no effect" not in h.replace("does not show that the rollback has no effect", "")
        assert "can still be missed" in h


# ------------------------------------------------------------------ 5. the all-baseline null cell


def test_05_the_treatment_arms_expected_successes_under_the_all_baseline_null_count():
    r = power_analysis(0.01, 1.8, allocation_ratio=0.5)
    cells = [n * p for n in (r.n_control, r.n_treatment) for p in (0.01, 0.99)]  # all-baseline null
    cells += [r.n_control * 0.028, r.n_control * 0.972, r.n_treatment * 0.028, r.n_treatment * 0.972]  # the alternative
    assert r.n_treatment * 0.01 < 10 <= min(r.n_control * 0.01, r.n_control * 0.028, r.n_treatment * 0.028)
    assert r.min_expected_count == pytest.approx(min(cells)) and r.min_expected_count == pytest.approx(r.n_treatment * 0.01)
    assert not r.approximation_reliable and r.warnings and "unreliable" in r.warnings[0]


def test_05_reliable_designs_stay_reliable():
    for args in ((0.05, 1.0), (0.0199, 0.585), (0.3, 3.0)):
        assert power_analysis(*args).approximation_reliable


# ------------------------------------------------------------------ 6. minimality outside the monotone regime


def brute_first_n(p0, mde_pp, power, ratio, alpha=0.05, limit=400):
    p1 = p0 + mde_pp / 100
    z = stats.norm.isf(alpha / 2)
    for n in range(1, limit + 1):
        nt = max(1, math.ceil(ratio * n - 1e-12))
        pbar = (n * p0 + nt * p1) / (n + nt)
        s0 = math.sqrt(pbar * (1 - pbar) * (1 / n + 1 / nt))
        s1 = math.sqrt(p0 * (1 - p0) / n + p1 * (1 - p1) / nt)
        d = abs(p1 - p0)
        if stats.norm.cdf((d - z * s0) / s1) + stats.norm.cdf((-d - z * s0) / s1) >= power:
            return n, nt
    return None


def test_06_the_reviewed_non_monotone_case_returns_the_smallest_pair():
    r = power_analysis(0.58, 41.5, power=0.17, allocation_ratio=0.09)
    assert (r.n_control, r.n_treatment) == (34, 4) == brute_first_n(0.58, 41.5, 0.17, 0.09)
    assert r.achieved_power >= 0.17 and r.minimality == "exhaustive" and not r.approximation_reliable


def test_06_random_sparse_cases_match_a_brute_force_scan():
    rng = np.random.default_rng(6)
    checked = 0
    for _ in range(400):
        p0 = float(rng.uniform(0.01, 0.95))
        mde = float(rng.uniform(-p0, 0.99 - p0) * 100)
        if abs(mde) < 1:
            continue
        power, ratio = float(rng.choice([0.1, 0.17, 0.3, 0.5, 0.8])), float(rng.choice([0.05, 0.09, 0.25, 1.0, 3.0]))
        expected = brute_first_n(p0, mde, power, ratio)
        if expected is None or expected[0] > 400:
            continue
        r = power_analysis(p0, mde, power=power, allocation_ratio=ratio)
        assert (r.n_control, r.n_treatment) == expected, (p0, mde, power, ratio)
        checked += 1
    assert checked > 100


def test_06_large_designs_say_that_monotonicity_was_assumed():
    assert power_analysis(0.05, 1.0).minimality == "exhaustive"  # 8,158 per arm is within the scanned range
    assert power_analysis(0.03, 0.00008).minimality == "assumed_monotone"


# ------------------------------------------------------------------ 7. the sample-size cap


def test_07_a_size_below_the_cap_is_returned_even_when_doubling_overshoots_it():
    r = power_analysis(0.03, 0.00008)
    assert r.n_control <= 10**12 and r.achieved_power >= 0.8 > r.previous_pair_power
    assert 2 ** 39 < r.n_control <= 10**12  # the doubling sequence jumps from 2^39 to 2^40 > 10^12


def test_07_a_size_above_the_cap_still_raises():
    with pytest.raises(ValueError, match="too small"):
        power_analysis(0.03, 0.00004)
