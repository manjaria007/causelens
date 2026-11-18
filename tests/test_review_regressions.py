"""Regression tests for the PR #5 review (eleven findings). Each test reproduces the reported case."""
from __future__ import annotations

import importlib.util
import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from scenarios import CHECKOUT, HERO, POST, PRE, run_pipeline, scenario_data

from causelens.analytics import did as did_mod
from causelens.analytics import patterns as patterns_mod
from causelens.analytics import timing as timing_mod
from causelens.analytics.candidates import test_candidates as run_candidates
from causelens.analytics.did import (
    build_panel,
    did_on_panel,
    estimate_did,
    project_impact,
    run_placebo,
)
from causelens.analytics.evidence import assess_evidence
from causelens.analytics.loaders import validate_metrics
from causelens.analytics.patterns import uniform_test
from causelens.analytics.timing import analyze_timing

FOCAL = {"device": "mobile", "browser": "safari"}


def frame(rows):
    return validate_metrics(pd.DataFrame(rows))


def base_row(day, dev, brw, sessions, orders, channel="organic"):
    return {"date": day.strftime("%Y-%m-%d"), "device": dev, "browser": brw, "channel": channel,
            "customer_type": "new", "sessions": int(sessions), "orders": int(orders)}


# ------------------------------------------------------------------ 1. sparse segments in the uniform test


def sparse_uniform_frame(seed: int) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    rows = []
    for i in range(30):
        d = pd.Timestamp("2026-08-01") + pd.Timedelta(days=i)
        for dev in ("a", "b"):
            s = int(rng.poisson(4000))
            rows.append(base_row(d, dev, "chrome", s, rng.binomial(s, 0.04)))
        if i in (5, 25):  # a segment seen on one day in each window; the true rate never changes
            rows.append(base_row(d, "rare", "chrome", 4000, rng.binomial(4000, 0.04)))
    return frame(rows)


def test_01_a_segment_seen_on_one_day_per_window_does_not_falsely_reject_uniformity():
    pre, post = (date(2026, 8, 1), date(2026, 8, 20)), (date(2026, 8, 21), date(2026, 8, 30))
    rejected = 0
    for seed in range(40):
        df = sparse_uniform_frame(seed)
        days = pd.DatetimeIndex(sorted(df["date"].unique()))
        r = uniform_test(df, days, pre, post)
        rejected += r.status == "contradicted"
        part = next(p for p in r.partitions if p.dimension == "device")
        assert "rare" in part.excluded and "active days" in part.excluded["rare"]
    assert rejected <= 5, rejected  # nominal 5% of 40; 37/40 before the fix


# ------------------------------------------------------------------ 2. a p-value of exactly zero


def test_02_a_holm_p_value_of_zero_still_contradicts_the_pattern(monkeypatch):
    rows = []
    rng = np.random.default_rng(0)
    for i in range(30):
        d = pd.Timestamp("2026-08-01") + pd.Timedelta(days=i)
        for dev, drop in (("a", 0.0), ("b", -0.9)):
            level = 0.05 * (1 + (drop if i >= 20 else 0.0))
            s = int(rng.poisson(5000))
            rows.append(base_row(d, dev, "chrome", s, rng.binomial(s, level)))
    df = frame(rows)
    days = pd.DatetimeIndex(sorted(df["date"].unique()))
    args = (df, days, (date(2026, 8, 1), date(2026, 8, 20)), (date(2026, 8, 21), date(2026, 8, 30)))
    assert uniform_test(*args).status == "contradicted"
    monkeypatch.setattr(patterns_mod, "holm_adjust", lambda ps: [0.0 for _ in ps])
    r = uniform_test(*args)
    assert r.status == "contradicted" and "p = 0" in r.detail


# ------------------------------------------------------------------ 3. floating-point noise as a DiD effect


def constant_frame(n=40, other=None):
    rows = []
    for i in range(n):
        d = pd.Timestamp("2026-08-01") + pd.Timedelta(days=i)
        rows.append(base_row(d, "mobile", "safari", 1000, 50))
        rows.append(base_row(d, "desktop", "chrome", 1000, 50))
    return frame(rows)


def test_03_exactly_constant_groups_give_no_did_effect():
    df = constant_frame()
    r = estimate_did(df, FOCAL, "2026-08-25")
    assert r.status == "insufficient" and "degenerate" in r.reason
    assert r.effect_pp is None and r.p_value is None


def test_03_exactly_constant_groups_make_pretrend_and_placebo_not_evaluable():
    df = constant_frame(n=70)
    panel = build_panel(df, FOCAL)
    assert did_mod.pre_trends_on_panel(panel, "2026-10-01").status == "not_evaluable"
    assert did_mod.placebo_on_panel(panel, "2026-10-01").status == "not_evaluable"


# ------------------------------------------------------------------ 4. incomplete placebo checks


def test_04_a_placebo_date_that_cannot_be_evaluated_leaves_the_check_incomplete():
    metrics, events = scenario_data(42, "planted")
    ok = run_placebo(metrics, FOCAL, "2026-10-14", events=events, dates=["2026-09-05"])
    assert ok.status == "pass"
    r = run_placebo(metrics, FOCAL, "2026-10-14", events=events, dates=["2026-08-05", "2026-09-05"])
    assert r.status == "not_evaluable" and r.n_attempted == 2 and r.n_evaluated == 1
    assert "1 of 2" in r.reason
    from causelens.analytics.did import PreTrendResult
    pre = PreTrendResult(status="pass", p_value=0.5, n_days=60, onset=date(2026, 10, 14))
    did = estimate_did(metrics, FOCAL, "2026-10-14", events=events)
    ev = assess_evidence(did=did, pre_trends=pre, placebo=r, timing_assumptions="pass", exposure_consistent=True,
                         pattern_consistent=None, attribution_ambiguous=False)
    assert ev.strength == "weak" and {c.name: c.outcome for c in ev.checks}["placebo"] == "not_run"


def test_04_a_failing_evaluated_placebo_still_fails_when_another_date_is_missing(monkeypatch):
    metrics, events = scenario_data(42, "planted")
    panel = build_panel(metrics, FOCAL, events=events)
    # a step at 2026-09-05 in the focal series: placebo there must fail even though 2026-08-05 cannot be evaluated
    p = panel.daily.copy()
    after = p.index >= pd.Timestamp("2026-09-05")
    p.loc[after, "o_f"] = (p.loc[after, "o_f"] * 0.6).round()
    p["rate_f"] = (p["o_f"] / p["s_f"]).where(p["usable"])
    p["diff_pp"] = 100 * (p["rate_f"] - p["rate_c"])
    r = did_mod.placebo_on_panel(panel.model_copy(update={"daily": p}), "2026-10-14", dates=["2026-08-05", "2026-09-05"])
    assert r.status == "fail"


# ------------------------------------------------------------------ 5. impact denominator with explicit controls


def test_05_overall_impact_uses_all_traffic_not_just_focal_plus_control():
    rng = np.random.default_rng(1)
    rows = []
    for i in range(40):
        d = pd.Timestamp("2026-08-01") + pd.Timedelta(days=i)
        post = i >= 25
        rows.append(base_row(d, "mobile", "safari", 1000, rng.binomial(1000, 0.04 if post else 0.05)))
        rows.append(base_row(d, "desktop", "chrome", 1000, rng.binomial(1000, 0.05)))
        rows.append(base_row(d, "mobile", "chrome", 8000, rng.binomial(8000, 0.05)))
    df = frame(rows)
    panel = build_panel(df, FOCAL, comparison={"device": "desktop", "browser": "chrome"})
    onset = "2026-08-26"
    did = did_on_panel(panel, onset)
    imp = project_impact(did, panel)
    assert imp.focal_share_post == pytest.approx(0.1) and imp.total_sessions_post == 10000 * 15
    assert imp.overall_pp.estimate == pytest.approx(did.effect_pp.estimate * 0.1, rel=1e-9)
    # default comparison (everything else) gives the same denominator
    p2 = build_panel(df, FOCAL)
    assert project_impact(did_on_panel(p2, onset), p2).focal_share_post == pytest.approx(0.1)


# ------------------------------------------------------------------ 6. the mandatory one-day sensitivity


def test_06_the_one_day_sensitivity_cannot_be_switched_off():
    metrics, events = scenario_data(42, "planted")
    _, default = run_pipeline(metrics, events)
    _, only0 = run_pipeline(metrics, events, sensitivity_lags=(0,))
    _, empty_ok = run_pipeline(metrics, events, sensitivity_lags=(1, 2))
    c0 = {c.event: c for c in only0.candidates}[CHECKOUT]
    cd = {c.event: c for c in default.candidates}[CHECKOUT]
    assert [o.lag_days for o in c0.lag_sensitivity] == [0, 1]
    assert c0.attribution.ambiguous and c0.evidence.strength == cd.evidence.strength == "moderate"
    assert [o.lag_days for o in {c.event: c for c in empty_ok.candidates}[CHECKOUT].lag_sensitivity] == [0, 1, 2]


# ------------------------------------------------------------------ 7. lag validation


@pytest.mark.parametrize("bad", [0.9, -0.9, 1.5, True, False, "1", None, float("nan")])
def test_07_lags_must_be_integers(bad):
    metrics, events = scenario_data(42, "planted")
    with pytest.raises(ValueError, match="integer"):
        run_candidates(metrics, events, PRE, POST, candidates=[CHECKOUT], lag_days={CHECKOUT: bad})
    with pytest.raises(ValueError, match="integer"):
        run_candidates(metrics, events, PRE, POST, candidates=[CHECKOUT], sensitivity_lags=[bad])


def test_07_numpy_integers_are_accepted():
    metrics, events = scenario_data(42, "planted")
    res = run_candidates(metrics, events, PRE, POST, candidates=[CHECKOUT], lag_days={CHECKOUT: np.int64(0)}, sensitivity_lags=[np.int64(1)])
    assert res.candidates[0].lag_days == 0


# ------------------------------------------------------------------ 8. segment values containing commas


def test_08_segment_values_with_commas_are_carried_structurally():
    metrics, events = scenario_data(42, "planted")
    renamed = metrics.assign(browser=metrics["browser"].replace({"safari": "safari, secure"}))
    stage, res = run_pipeline(renamed, events)
    assert stage == "tested" and res.status == "ok"
    assert res.focal.segments == [{"device": "mobile", "browser": "safari, secure"}]
    _, base = run_pipeline(metrics, events)
    assert [(c.event, c.verdict) for c in res.candidates] == [(c.event, c.verdict) for c in base.candidates]


# ------------------------------------------------------------------ 9. an exactly zero step coefficient


def test_09_a_zero_step_has_finite_bounds(monkeypatch):
    days = pd.date_range("2026-08-01", periods=40)
    y = np.tile(np.arange(20, dtype=float), 2)  # pre and post halves identical: step is 0 (to rounding)
    onsets = {"A": date(2026, 8, 21)}
    ta = analyze_timing(days, y, onsets)
    c = ta.candidates["A"]
    assert c.status == "ok" and np.isfinite([c.step_pp.lower, c.step_pp.upper, c.step_std_error_pp]).all()
    assert c.step_pp.lower <= 0 <= c.step_pp.upper

    # force t to exactly 0, as an exactly zero coefficient gives
    real = timing_mod._contrast

    def zero_t(fit, R):
        out = real(fit, R)
        return None if out is None else (out[0], 0.0, *out[2:])

    monkeypatch.setattr(timing_mod, "_contrast", zero_t)
    c = analyze_timing(days, y, onsets).candidates["A"]
    assert np.isfinite([c.step_pp.lower, c.step_pp.upper, c.step_std_error_pp]).all() and c.step_std_error_pp > 0


# ------------------------------------------------------------------ 10. pre-trend slope on calendar days


def test_10_pre_trend_slope_is_per_calendar_day_when_days_are_missing():
    rng = np.random.default_rng(3)
    slope = 0.01  # pp per calendar day
    rows = []
    for i in range(120):
        if i % 3:  # two of every three days are absent
            continue
        d = pd.Timestamp("2026-08-01") + pd.Timedelta(days=i)
        rate_f = 0.05 + slope * i / 100
        rows.append(base_row(d, "mobile", "safari", 200_000, rng.binomial(200_000, rate_f)))
        rows.append(base_row(d, "desktop", "chrome", 200_000, rng.binomial(200_000, 0.05)))
    panel = build_panel(frame(rows), FOCAL)
    r = did_mod.pre_trends_on_panel(panel, "2026-12-15")
    assert r.status in ("fail", "borderline", "pass") and r.n_days == 40
    assert r.slope_difference_pp_per_day.estimate == pytest.approx(slope, abs=0.003)


# ------------------------------------------------------------------ 11. calibration scoring


def load_runner():
    path = Path(__file__).parent / "calibration" / "run_attribution.py"
    spec = importlib.util.spec_from_file_location("run_attribution", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["run_attribution"] = mod
    spec.loader.exec_module(mod)
    return mod


def rec(verdict, ambiguous, stage="tested"):
    return {"stage": stage, "verdict": verdict, "ambiguous": ambiguous, "lag1": {}, "strength": {}, "step_sig": {}}


def test_11_a_rejected_true_cause_that_was_flagged_ambiguous_is_flagged():
    g = load_runner().grade
    flagged = rec({CHECKOUT: "rejected", HERO: "inconclusive"}, {CHECKOUT: True, HERO: True})
    unflagged = rec({CHECKOUT: "rejected", HERO: "inconclusive"}, {CHECKOUT: False, HERO: False})
    assert g("planted", flagged) == "incorrect_flagged"
    assert g("planted", unflagged) == "incorrect_unflagged"


def test_11_gradual_hero_separates_event_recovery_from_the_step_hypothesis():
    g = load_runner().grade
    r = rec({HERO: "supported", CHECKOUT: "rejected"}, {HERO: False, CHECKOUT: False})
    assert g("gradual_hero", r) == "event_recovered_step_misspecified"
    assert g("gradual_hero", rec({HERO: "inconclusive"}, {HERO: False})) == "unmet"
    assert g("gradual_hero", rec({HERO: "inconclusive", CHECKOUT: "supported"}, {HERO: False, CHECKOUT: False})) == "incorrect_unflagged"
