"""design_experiment (Task 6.2): MDE, baseline, arms, guardrails, duration and the needs_input paths."""
from __future__ import annotations

import json
import math
import warnings

import numpy as np
import pytest
from scenarios import CHECKOUT, HERO, POST, run_pipeline, scenario_data

from causelens.analytics import design_experiment, power_analysis
from causelens.analytics.confirm import Interval

warnings.filterwarnings("ignore")


@pytest.fixture(scope="module")
def planted():
    metrics, events = scenario_data(42, "planted")
    _, res = run_pipeline(metrics, events)
    return metrics, events, res


@pytest.fixture(scope="module")
def strong():
    metrics, events = scenario_data(5, "hero_immediate")
    _, res = run_pipeline(metrics, events)
    return metrics, events, res


@pytest.fixture(scope="module")
def ambiguous():
    metrics, events = scenario_data(2, "same_day")
    _, res = run_pipeline(metrics, events)
    return metrics, events, res


def cand(res, name):
    return next(c for c in res.candidates if c.event == name)


def did_with(c, lower, upper, status="ok"):
    base = c.differential_effect
    assert base is not None
    return base.model_copy(update={"status": status, "effect_pp": None if status != "ok" else Interval(estimate=(lower + upper) / 2, lower=lower, upper=upper)})


def swap_effect(res, name, did):
    cands = [c.model_copy(update={"differential_effect": did}) if c.event == name else c for c in res.candidates]
    return res.model_copy(update={"candidates": cands})


# ------------------------------------------------------------------ the reference design


def test_default_design_is_a_two_arm_rollback_of_the_leading_candidate(planted):
    metrics, events, res = planted
    d = design_experiment(metrics, res, POST, events=events)
    assert d.status == "ok" and d.purpose == "settle" and d.target == [CHECKOUT] and d.evidence_strength == "moderate"
    assert [a.role for a in d.plan.arms] == ["control", "rollback"] and d.plan.n_comparisons == 1
    assert d.plan.arms[1].rollback_of == CHECKOUT
    assert "randomization" not in (d.reason or "") and d.randomization_unit.startswith("session")
    assert d.primary_metric.startswith("conversion rate (orders per session)")


def test_baseline_is_the_usable_post_period_focal_rate_with_its_window_and_counts(planted):
    metrics, events, res = planted
    d = design_experiment(metrics, res, POST, events=events)
    sel = (metrics.device == "mobile") & (metrics.browser == "safari") & (metrics.date >= POST[0]) & (metrics.date <= POST[1])
    orders, sessions = int(metrics.loc[sel, "orders"].sum()), int(metrics.loc[sel, "sessions"].sum())
    b = d.baseline
    assert (b.orders, b.sessions, b.n_days) == (orders, sessions, 18)
    assert b.rate == pytest.approx(orders / sessions, rel=1e-12)
    assert (str(b.first_day), str(b.last_day)) == POST and tuple(str(x) for x in b.requested_window) == POST
    assert b.population == "device=mobile, browser=safari"
    assert d.plan.power.baseline == pytest.approx(b.rate)


def test_the_heuristic_mde_reverses_the_sign_of_the_interval_bound_nearest_zero_and_is_labelled(planted):
    metrics, events, res = planted
    d = design_experiment(metrics, res, POST, events=events)
    eff = cand(res, CHECKOUT).differential_effect.effect_pp
    assert eff.upper < 0  # the release lowered conversion
    assert d.mde.mde_pp == pytest.approx(-eff.upper) and d.mde.mde_pp > 0
    assert d.mde.source == "provisional_planning_heuristic" and "provisional planning heuristic" in d.mde.label
    assert d.mde.observed_effect == eff and d.mde.observed_for == CHECKOUT
    assert any("winner's curse" in c and "selection bias" in c for c in d.mde.caveats)
    assert any("business target" in w for w in d.warnings)


def test_sample_size_comes_from_power_analysis_and_target_rate_uses_percentage_points(planted):
    metrics, events, res = planted
    d = design_experiment(metrics, res, POST, events=events)
    ref = power_analysis(d.baseline.rate, d.mde.mde_pp)
    assert d.plan.power == ref
    assert ref.target_rate == pytest.approx(d.baseline.rate + d.mde.mde_pp / 100)
    assert d.plan.arms[0].n_sessions == ref.n_control and d.plan.arms[1].n_sessions == ref.n_treatment


def test_duration_is_the_slowest_arm_then_whole_weeks(planted):
    metrics, events, res = planted
    d = design_experiment(metrics, res, POST, events=events, mde_pp=-0.05)  # a small target: several weeks
    daily = d.traffic.mean_daily_sessions
    sel = (metrics.device == "mobile") & (metrics.browser == "safari") & (metrics.date >= POST[0]) & (metrics.date <= POST[1])
    assert daily == pytest.approx(metrics.loc[sel].groupby("date")["sessions"].sum().mean())
    fill = [a.n_sessions / (daily * a.allocation_share) for a in d.plan.arms]
    assert d.plan.duration_days == math.ceil(max(fill) - 1e-9)
    assert d.plan.duration_weeks == math.ceil(d.plan.duration_days / 7)
    assert d.plan.duration_days_rounded_to_weeks == 7 * d.plan.duration_weeks and d.plan.duration_weeks >= 2
    assert d.plan.slowest_arm == max(d.plan.arms, key=lambda a: a.days_to_fill).name
    assert "continues at the observed rate" in d.traffic.assumption


def test_unequal_allocation_slowest_arm_is_the_one_with_the_smaller_share(planted):
    metrics, events, res = planted
    d = design_experiment(metrics, res, POST, events=events, mde_pp=-0.2, allocation_ratio=0.25)
    control, treat = d.plan.arms
    assert control.allocation_share == pytest.approx(1 / 1.25) and treat.allocation_share == pytest.approx(0.25 / 1.25)
    assert d.plan.power.n_treatment == math.ceil(0.25 * d.plan.power.n_control)
    assert d.plan.slowest_arm == (treat.name if treat.days_to_fill > control.days_to_fill else control.name)
    assert d.plan.duration_days == math.ceil(max(a.days_to_fill for a in d.plan.arms) - 1e-9)


# ------------------------------------------------------------------ a declared business target


def test_a_declared_business_target_is_preferred_over_the_heuristic(planted):
    metrics, events, res = planted
    d = design_experiment(metrics, res, POST, events=events, mde_pp=0.3)
    assert d.mde.source == "user_declared" and d.mde.mde_pp == 0.3 and d.mde.observed_effect is None
    assert "business target" in d.mde.label and "provisional" not in d.mde.label
    assert not any("provisional planning heuristic" in w for w in d.warnings)
    assert d.plan.power == power_analysis(d.baseline.rate, 0.3)


def test_a_declared_negative_target_means_the_rollback_should_lower_conversion(planted):
    metrics, events, res = planted
    d = design_experiment(metrics, res, POST, events=events, mde_pp=-0.3)
    assert d.plan.power.target_rate == pytest.approx(d.baseline.rate - 0.003) and " lower conversion" in d.hypothesis


def test_the_heuristic_works_when_the_observed_effect_was_an_improvement(planted):
    metrics, events, res = planted
    res2 = swap_effect(res, CHECKOUT, did_with(cand(res, CHECKOUT), 0.3, 0.6))
    d = design_experiment(metrics, res2, POST, events=events)
    assert d.mde.mde_pp == pytest.approx(-0.3) and " lower conversion" in d.hypothesis  # undoing a gain lowers conversion


# ------------------------------------------------------------------ needs_input


@pytest.mark.parametrize("kind", ["interval_includes_zero", "estimate_unavailable"])
def test_no_usable_estimate_needs_a_declared_mde_instead_of_inventing_one(planted, kind):
    metrics, events, res = planted
    c = cand(res, CHECKOUT)
    res2 = swap_effect(res, CHECKOUT, did_with(c, -0.2, 0.1) if kind == "interval_includes_zero" else did_with(c, 0, 0, status="insufficient"))
    d = design_experiment(metrics, res2, POST, events=events)
    assert d.status == "needs_input" and d.needs == ["mde_pp"] and d.plan is None and d.mde is None
    assert "Declare a business target with mde_pp" in d.reason
    assert d.baseline is not None and d.traffic is not None  # what could be computed is still reported
    ok = design_experiment(metrics, res2, POST, events=events, mde_pp=0.4)
    assert ok.status == "ok" and ok.mde.source == "user_declared"


def test_no_single_leading_candidate_asks_for_a_choice(ambiguous):
    metrics, events, res = ambiguous
    d = design_experiment(metrics, res, POST, events=events)
    assert d.status == "needs_input" and d.needs == ["target_candidate"]
    assert CHECKOUT in d.reason and HERO in d.reason and "each_candidate" in d.reason


def test_no_candidate_release_needs_the_intervention_to_be_named(planted):
    metrics, events, _ = planted
    ev = events[~events.name.isin([HERO, CHECKOUT])].reset_index(drop=True)
    _, res = run_pipeline(metrics, ev)
    assert res.status == "no_candidates"
    d = design_experiment(metrics, res, POST, events=ev)
    assert d.status == "needs_input" and d.needs == ["candidate_event"] and d.plan is None


def test_too_few_post_period_days_needs_a_longer_window(planted):
    metrics, events, res = planted
    d = design_experiment(metrics, res, ("2026-10-14", "2026-10-18"), events=events)
    assert d.status == "needs_input" and d.needs == ["post_window"] and "5 usable post-period days" in d.reason


def test_a_rate_of_zero_cannot_be_planned_from(planted):
    metrics, events, res = planted
    zero = metrics.assign(orders=0)
    d = design_experiment(zero, res, POST, events=events, mde_pp=0.3)
    assert d.status == "needs_input" and d.needs == ["baseline"]


# ------------------------------------------------------------------ strength decides the purpose; arms


def test_strong_evidence_gets_a_confirmation_rollback(strong):
    metrics, events, res = strong
    assert cand(res, HERO).evidence.strength == "strong"
    d = design_experiment(metrics, res, POST, events=events)
    assert d.purpose == "confirm" and d.target == [HERO] and d.summary.startswith("Confirmation experiment")


def test_below_strong_evidence_gets_a_settling_experiment(planted):
    metrics, events, res = planted
    assert design_experiment(metrics, res, POST, events=events).summary.startswith("Settling experiment")


def test_an_explicit_target_can_be_named_when_nothing_leads(ambiguous):
    metrics, events, res = ambiguous
    d = design_experiment(metrics, res, POST, events=events, target_candidate=CHECKOUT, mde_pp=0.4)
    assert d.status == "ok" and d.target == [CHECKOUT] and d.evidence_strength == "insufficient" and d.purpose == "settle"


def test_an_ambiguous_leading_candidate_also_gets_the_shared_control_option(ambiguous):
    metrics, events, res = ambiguous
    d = design_experiment(metrics, res, POST, events=events, target_candidate=CHECKOUT, mde_pp=0.4)
    alt = d.alternative_plan
    assert alt is not None and alt.n_comparisons == 2 and [a.role for a in alt.arms] == ["control", "rollback", "rollback"]
    assert alt.power.alpha_per_comparison == pytest.approx(0.025) and alt.power.n_control > d.plan.power.n_control
    assert "shared control" in d.alternative_note and "per comparison" in d.alternative_note and "independent rollbacks" in d.alternative_note


def test_no_alternative_is_offered_when_only_one_candidate_is_open(planted):
    metrics, events, res = planted
    assert cand(res, HERO).verdict == "rejected"
    d = design_experiment(metrics, res, POST, events=events)
    assert d.alternative_plan is None and d.alternative_note is None


def test_each_candidate_uses_one_shared_control_counted_once_and_divides_alpha(ambiguous):
    metrics, events, res = ambiguous
    d = design_experiment(metrics, res, POST, events=events, arms="each_candidate", mde_pp=0.4)
    p = d.plan
    assert [a.role for a in p.arms] == ["control", "rollback", "rollback"] and {a.rollback_of for a in p.arms[1:]} == {CHECKOUT, HERO}
    assert p.n_comparisons == 2 and p.power.alpha_per_comparison == pytest.approx(0.025)
    r = p.power.allocation_ratio
    assert p.arms[0].allocation_share == pytest.approx(1 / (1 + 2 * r)) and sum(a.allocation_share for a in p.arms) == pytest.approx(1.0)
    daily = d.traffic.mean_daily_sessions
    assert p.duration_days == math.ceil(max(a.n_sessions / (daily * a.allocation_share) for a in p.arms) - 1e-9)
    assert any("per comparison" in a and "not the probability" in a for a in d.assumptions)
    assert d.purpose == "settle" and d.evidence_strength == "insufficient"
    assert "exactly one of" in d.intervention


def test_each_candidate_without_a_declared_target_sizes_every_arm_by_the_smallest_estimate(ambiguous):
    metrics, events, res = ambiguous
    d = design_experiment(metrics, res, POST, events=events, arms="each_candidate")
    if d.status == "ok":
        assert d.mde.source == "provisional_planning_heuristic" and any("smallest-magnitude" in c for c in d.mde.caveats)
    else:
        assert d.needs == ["mde_pp"]


# ------------------------------------------------------------------ guardrails and wording


def test_guardrails_are_labelled_by_what_can_actually_be_measured(planted):
    metrics, events, res = planted
    d = design_experiment(metrics, res, POST, events=events)
    g = {x.name: x for x in d.guardrails}
    srm = g["Sample-ratio mismatch (SRM)"]
    assert srm.kind == "assignment_diagnostic" and not srm.computable_from_current_data and "assignment logs" in srm.detail
    ext = g["Conversion in traffic outside the experiment"]
    assert ext.kind == "external_monitor" and ext.computable_from_current_data and "outside the experiment" in ext.detail
    sel = ~((metrics.device == "mobile") & (metrics.browser == "safari")) & (metrics.date >= POST[0]) & (metrics.date <= POST[1])
    assert ext.reference["orders"] == int(metrics.loc[sel, "orders"].sum()) and ext.reference["sessions"] == int(metrics.loc[sel, "sessions"].sum())
    assert ext.reference["conversion_rate"] == pytest.approx(ext.reference["orders"] / ext.reference["sessions"])
    instr = [x for x in d.guardrails if x.kind == "requires_instrumentation"]
    assert len(instr) == 2 and all(not x.computable_from_current_data and x.reference is None and "instrumentation" in x.detail for x in instr)


def test_the_hypothesis_is_conditional_and_claims_no_cause(planted):
    metrics, events, res = planted
    d = design_experiment(metrics, res, POST, events=events)
    h = d.hypothesis
    assert h.startswith("If ") and "currently contributes" in h and "cannot establish that it was the sole historical cause" in h
    for banned in ("caused the", "is the cause", "because of", "proves"):
        assert banned not in h and banned not in d.summary
    assert "holding every other release fixed" in d.intervention


def test_assumptions_state_session_randomization_traffic_and_baseline(planted):
    metrics, events, res = planted
    d = design_experiment(metrics, res, POST, events=events)
    text = " ".join(d.assumptions)
    assert "no visitor ids" in text and "carryover" in text and "independent" in text
    assert "continues at the observed rate" in text and "whole weeks" in text and "shared control is counted once" in text
    assert "normal approximation" in text and "current contribution" in text
    assert d.randomization_unit.startswith("session") and "planning assumption" in d.randomization_unit


def test_the_summary_labels_power_as_a_normal_approximation(planted):
    metrics, events, res = planted
    d = design_experiment(metrics, res, POST, events=events)
    assert "achieved power under the normal approximation" in d.summary and "per comparison" in d.summary


def test_flagged_days_inside_the_window_are_removed_and_disclosed(planted):
    metrics, events, res = planted
    d = design_experiment(metrics, res, ("2026-09-14", "2026-10-31"), events=events, mde_pp=0.3)
    assert [str(x) for x in d.baseline.excluded_dates] == ["2026-09-20"] and d.baseline.n_days == 47
    assert any("2026-09-20" in w for w in d.warnings)


def test_a_sparse_planning_target_carries_the_approximation_warning(planted):
    metrics, events, res = planted
    d = design_experiment(metrics, res, POST, events=events, mde_pp=-1.97)
    assert d.status == "ok" and not d.plan.power.approximation_reliable
    assert any("unreliable" in w for w in d.warnings)


# ------------------------------------------------------------------ validation, determinism


@pytest.mark.parametrize(
    "kwargs",
    [{"arms": "all"}, {"arms": "each_candidate", "target_candidate": CHECKOUT}, {"target_candidate": "No such event"},
     {"mde_pp": 0.0}, {"mde_pp": float("nan")}, {"mde_pp": True}, {"alpha": 0.0}, {"alpha": 0.9}, {"power": 1.0},
     {"allocation_ratio": 0.0}, {"allocation_ratio": float("inf")}],
)
def test_invalid_arguments_raise(planted, kwargs):
    metrics, events, res = planted
    with pytest.raises(ValueError):
        design_experiment(metrics, res, POST, events=events, **kwargs)


def test_each_candidate_needs_two_open_candidates_and_windows_must_be_valid(planted):
    metrics, events, res = planted
    with pytest.raises(ValueError, match="at least two"):
        design_experiment(metrics, res, POST, events=events, arms="each_candidate", mde_pp=0.3)
    with pytest.raises(ValueError):
        design_experiment(metrics, res, (POST[1], POST[0]), events=events)
    with pytest.raises(ValueError):
        design_experiment(metrics, res, ("not a date", POST[1]), events=events)


def test_design_is_deterministic_json_safe_row_order_invariant_and_does_not_mutate_inputs(planted):
    metrics, events, res = planted
    before = metrics.copy()
    a = design_experiment(metrics, res, POST, events=events)
    shuffled = metrics.sample(frac=1.0, random_state=1).reset_index(drop=True)
    b = design_experiment(shuffled, res, POST, events=events)
    assert a == b
    text = a.model_dump_json()
    assert "NaN" not in text and "Infinity" not in text
    assert json.loads(text)["plan"]["power"]["n_comparisons"] == 1
    assert metrics.equals(before)


def test_numeric_results_are_finite(planted):
    metrics, events, res = planted
    d = design_experiment(metrics, res, POST, events=events)
    nums = [d.baseline.rate, d.traffic.mean_daily_sessions, d.mde.mde_pp, d.plan.power.achieved_power, *[a.days_to_fill for a in d.plan.arms]]
    assert np.isfinite(nums).all()
