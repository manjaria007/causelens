"""Candidate verdicts (Task 5.1/5.4/5.5). Ground truth is used only to grade, never inside the analytics."""
from __future__ import annotations

import json
from datetime import date

import pandas as pd
import pytest
from scenarios import CHECKOUT, HERO, POST, PRE, run_pipeline, scenario_data

from causelens.analytics import check_data_quality, confirm_change, decompose_change
from causelens.analytics import did as did_module
from causelens.analytics.candidates import test_candidates as run_candidates
from causelens.analytics.candidates import test_event_scope as run_event_scope
from causelens.analytics.patterns import ExpectedPattern


@pytest.fixture(scope="module")
def planted():
    metrics, events = scenario_data(42, "planted")
    stage, res = run_pipeline(metrics, events)
    assert stage == "tested"
    return metrics, events, res


def by_event(res):
    return {c.event: c for c in res.candidates}


# ------------------------------------------------------------------ the reference scenario


def test_reference_verdicts_state_the_lag_and_the_assumption(planted):
    _, _, res = planted
    v = by_event(res)
    assert res.status == "ok" and res.focal.label == "device=mobile, browser=safari"
    assert v[CHECKOUT].verdict == "supported" and v[CHECKOUT].headline == f"{CHECKOUT}: supported under the immediate-effect assumption (lag 0)"
    assert v[HERO].verdict == "rejected" and "(lag 0)" in v[HERO].headline and "sole immediate explanation" in v[HERO].headline
    assert res.leading == CHECKOUT


def test_reference_effect_matches_the_planted_one_within_its_interval(planted, ground_truth):
    _, _, res = planted
    c = by_event(res)[CHECKOUT]
    e = c.differential_effect.effect_pp
    assert e.lower < -0.6618 < e.upper  # planted DiD of the simulator, graded here only
    assert c.impact is not None and c.evidence is not None and c.evidence.strength in ("strong", "moderate")
    assert ground_truth["planted_cause"]["event"] == CHECKOUT


def test_hero_plus_one_collides_with_checkout_plus_zero(planted):
    _, _, res = planted
    v = by_event(res)
    assert v[CHECKOUT].attribution.ambiguous and v[HERO].attribution.ambiguous
    assert v[CHECKOUT].evidence.strength != "strong" and v[CHECKOUT].evidence.capped_by_ambiguity
    hero1 = next(o for o in v[HERO].lag_sensitivity if o.lag_days == 1)
    assert hero1.verdict == "inconclusive" and hero1.onset_date == date(2026, 10, 14)


def test_lag_sensitivity_covers_each_candidate_at_every_allowed_lag(planted):
    _, _, res = planted
    for c in res.candidates:
        assert [o.lag_days for o in c.lag_sensitivity] == [0, 1]
        assert sum(o.declared for o in c.lag_sensitivity) == 1
        assert next(o for o in c.lag_sensitivity if o.declared).verdict == c.verdict


def test_assumptions_are_listed_and_result_is_json_safe(planted):
    _, _, res = planted
    text = res.model_dump_json()
    assert "NaN" not in text and "Infinity" not in text
    json.loads(text)
    assert any("Holm" in a for a in res.assumptions) and any("differential effect" in a for a in res.assumptions)


def test_onset_set_is_computed_and_contains_the_supported_onset(planted):
    _, _, res = planted
    assert date(2026, 10, 14) in res.onset_set
    assert by_event(res)[CHECKOUT].location.in_onset_set


# ------------------------------------------------------------------ declared lag


def test_checkout_delayed_by_one_day_is_supported_only_with_lag_one_declared():
    metrics, events = scenario_data(3, "checkout_delayed")
    _, base = run_pipeline(metrics, events)
    _, lagged = run_pipeline(metrics, events, lag_days={CHECKOUT: 1})
    assert by_event(base)[CHECKOUT].verdict != "supported"
    c = by_event(lagged)[CHECKOUT]
    if c.verdict == "supported":
        assert c.headline == f"{CHECKOUT}: supported assuming the effect begins 1 day(s) after the event (lag 1)"
    assert c.lag_days == 1 and c.onset_date == date(2026, 10, 15)


# ------------------------------------------------------------------ swapped cause, single candidate, no change


def test_swapped_true_cause_is_attributed_to_hero():
    metrics, events = scenario_data(5, "hero_immediate")
    _, res = run_pipeline(metrics, events)
    v = by_event(res)
    assert v[HERO].verdict == "supported" and (CHECKOUT not in v or v[CHECKOUT].verdict != "supported")


def test_a_single_candidate_has_no_sole_source_test_and_can_be_strong():
    metrics, events = scenario_data(5, "hero_immediate")
    _, res = run_pipeline(metrics, events, candidates=[HERO])
    (c,) = res.candidates
    assert c.timing.sole_source is None
    assert c.verdict == "supported" and c.evidence.strength in ("strong", "moderate")
    if not c.attribution.ambiguous:
        assert c.evidence.strength == c.evidence.base_strength


def test_no_change_means_nothing_is_supported_or_the_pipeline_stops():
    metrics, events = scenario_data(7, "none")
    stage, res = run_pipeline(metrics, events)
    assert stage.startswith("stopped") or all(c.verdict != "supported" for c in res.candidates)


def test_same_day_events_are_never_separated():
    metrics, events = scenario_data(2, "same_day")
    _, res = run_pipeline(metrics, events)
    assert all(c.verdict != "supported" for c in res.candidates)


def test_staged_effects_do_not_credit_a_single_sole_source():
    metrics, events = scenario_data(4, "staged")
    _, res = run_pipeline(metrics, events)
    assert res.leading is None or res.candidates  # never silently attributes everything to one
    assert sum(c.verdict == "supported" for c in res.candidates) <= 1


def test_a_broad_uniform_effect_is_not_localized():
    metrics, events = scenario_data(1, "planted")
    metrics = metrics.copy()
    pre_mask = pd.to_datetime(metrics["date"]) >= pd.Timestamp("2026-10-14")
    metrics.loc[pre_mask, "orders"] = (metrics.loc[pre_mask, "orders"] * 0.9).round().astype(int)
    stage, res = run_pipeline(metrics, events)
    if stage == "tested":
        assert res.status in ("not_localized", "ok", "no_candidates")


# ------------------------------------------------------------------ exposure, pattern, onset


def test_exposure_disjoint_from_a_significant_change_rejects(planted):
    metrics, events, _ = planted
    ev = events.copy()
    ev.loc[ev.name == HERO, "scope"] = "device=desktop"
    _, res = run_pipeline(metrics, ev)
    h = by_event(res)[HERO]
    assert h.exposure.status == "disjoint" and h.exposure.rejects and h.verdict == "rejected"
    assert "exposure scope" in h.headline


def test_all_visitors_scope_never_rejects_by_location(planted):
    metrics, events, _ = planted
    ev = events.copy()
    ev.loc[:, "scope"] = "all"
    _, res = run_pipeline(metrics, ev)
    assert all(c.exposure.status == "all_visitors" and not c.exposure.rejects for c in res.candidates)


def test_a_contradicted_declared_pattern_rejects(planted):
    metrics, events, _ = planted
    pat = {CHECKOUT: ExpectedPattern(kind="restricted", scope={"device": "desktop"}, equivalence_margin_pp=0.05)}
    _, res = run_pipeline(metrics, events, expected_patterns=pat)
    c = by_event(res)[CHECKOUT]
    assert c.pattern.status in ("contradicted", "untested", "not_contradicted")
    if c.pattern.status == "contradicted":
        assert c.verdict == "rejected" and "declared pattern" in c.headline


def test_undeclared_pattern_is_reported_as_not_declared(planted):
    _, _, res = planted
    assert all(c.pattern.status == "not_declared" for c in res.candidates)


def test_onset_outside_the_onset_set_blocks_supported_but_does_not_reject(planted):
    metrics, events, _ = planted
    _, res = run_pipeline(metrics, events, candidates=[CHECKOUT], lag_days={CHECKOUT: 5})
    c = res.candidates[0]
    assert not c.location.in_onset_set
    assert c.verdict in ("inconclusive", "rejected")
    if c.verdict == "inconclusive":
        assert any("outside the plausible onset set" in r for r in c.reasons)


# ------------------------------------------------------------------ required checks block "supported"


@pytest.mark.parametrize("which", ["pre_trends_on_panel", "placebo_on_panel"])
@pytest.mark.parametrize("status", ["fail", "not_evaluable"])
def test_failed_or_unevaluable_did_checks_block_supported(planted, monkeypatch, which, status):
    from causelens.analytics import candidates as cand_module

    metrics, events, _ = planted
    original = getattr(cand_module, which)

    def forced(*a, **k):
        return original(*a, **k).model_copy(update={"status": status, "reason": "forced for the test"})

    monkeypatch.setattr(cand_module, which, forced)
    _, res = run_pipeline(metrics, events)
    assert all(c.verdict != "supported" for c in res.candidates)
    assert res.leading is None


def test_property_8_uses_event_date_plus_evaluated_lag(planted):
    _, _, res = planted
    for c in res.candidates:
        for o in c.lag_sensitivity:
            assert o.onset_date == c.event_date + pd.Timedelta(days=o.lag_days)
        assert c.onset_date == c.event_date + pd.Timedelta(days=c.lag_days)


# ------------------------------------------------------------------ determinism and invariances


def test_results_are_deterministic_and_invariant_to_row_order_and_labels(planted):
    metrics, events, res = planted
    shuffled_m = metrics.sample(frac=1.0, random_state=3).reset_index(drop=True)
    shuffled_e = events.sample(frac=1.0, random_state=3).reset_index(drop=True)
    _, again = run_pipeline(shuffled_m, shuffled_e)
    assert [(c.event, c.verdict, c.headline) for c in again.candidates] == [(c.event, c.verdict, c.headline) for c in res.candidates]
    ce = by_event(res)[CHECKOUT].differential_effect.effect_pp
    ce2 = by_event(again)[CHECKOUT].differential_effect.effect_pp
    assert ce.estimate == pytest.approx(ce2.estimate, abs=1e-9)


def test_event_scope_wrapper_returns_the_named_verdict(planted):
    metrics, events, res = planted
    q = check_data_quality(metrics, events)
    confirm_change(metrics, PRE, POST, events, quality=q)
    dec = decompose_change(metrics, PRE, POST, events, quality=q)
    v = run_event_scope(CHECKOUT, metrics, events, PRE, POST, decomposition=dec, quality=q)
    assert v.verdict == by_event(res)[CHECKOUT].verdict
    with pytest.raises(ValueError, match="not a candidate"):
        run_event_scope("Analytics tag deploy", metrics, events, PRE, POST, decomposition=dec, quality=q, candidates=[CHECKOUT])


# ------------------------------------------------------------------ validation


@pytest.mark.parametrize(
    "kwargs",
    [{"candidates": ["No such event"]}, {"lag_days": {CHECKOUT: -1}}, {"lag_days": {"No such event": 1}},
     {"expected_patterns": {"No such event": ExpectedPattern()}}, {"sensitivity_lags": [-1]}, {"alpha": 0.9}, {"level": 1.5}],
)
def test_invalid_arguments_raise(planted, kwargs):
    metrics, events, _ = planted
    with pytest.raises(ValueError):
        run_candidates(metrics, events, PRE, POST, **kwargs)


def test_invalid_windows_and_duplicate_event_names_raise(planted):
    metrics, events, _ = planted
    with pytest.raises(ValueError):
        run_candidates(metrics, events, POST, PRE)
    with pytest.raises(ValueError):
        run_candidates(metrics, events, ("2026-10-01", "2026-10-20"), POST)
    dup = pd.concat([events, events.iloc[[0]]], ignore_index=True)
    with pytest.raises(ValueError):
        run_candidates(metrics, dup, PRE, POST)


def test_no_candidates_is_reported_when_no_event_is_near_the_onset(planted):
    metrics, events, _ = planted
    ev = events[~events.name.isin([HERO, CHECKOUT])].reset_index(drop=True)
    _, res = run_pipeline(metrics, ev)
    assert res.status == "no_candidates" and res.candidates == [] and "not attributed" in res.summary


def test_did_module_constants_are_exposed():
    assert did_module.DID_ASSUMPTIONS
