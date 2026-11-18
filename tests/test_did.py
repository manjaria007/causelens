from __future__ import annotations

import itertools
from datetime import date

import numpy as np
import pandas as pd
import pytest
from frames import day, focal_comparison_frame
from hypothesis import given, settings
from hypothesis import strategies as st
from scenarios import scenario_data, simulate
from statsmodels.stats.multitest import multipletests

from causelens.analytics import DataValidationError
from causelens.analytics.did import (
    DidResult,
    build_panel,
    check_pre_trends,
    did_on_panel,
    estimate_did,
    holm_adjust,
    placebo_dates,
    placebo_on_panel,
    pre_trends_on_panel,
    pretrend_status,
    project_impact,
    run_placebo,
)

FOCAL = {"device": "mobile", "browser": "safari"}
ONSET = "2026-10-14"


@pytest.fixture(scope="module")
def panel(reference):
    metrics, events = reference
    return build_panel(metrics, FOCAL, events=events)


@pytest.fixture(scope="module")
def result(panel):
    return did_on_panel(panel, ONSET)


# ------------------------------------------------------------------ panel


def test_panel_removes_the_flagged_day_for_both_groups_and_discloses_it(panel):
    assert panel.excluded_dates == [date(2026, 9, 20)] and panel.exclusions_applied
    assert not panel.daily.loc["2026-09-20", "usable"]
    assert np.isnan(panel.daily.loc["2026-09-20", "diff_pp"])
    assert panel.disclosure.excluded_days[0].date == date(2026, 9, 20)
    assert len(panel.usable_days) == 91


def test_panel_groups_partition_the_traffic(reference, panel):
    metrics, _ = reference
    d = panel.daily[panel.daily["usable"]]
    total = metrics.assign(date=pd.to_datetime(metrics["date"])).groupby("date")["sessions"].sum()
    assert np.allclose((d["s_f"] + d["s_c"]).to_numpy(), total.loc[d.index].to_numpy())
    focal_rows = metrics[(metrics.device == "mobile") & (metrics.browser == "safari")]
    assert int(d["s_f"].sum()) == int(focal_rows.assign(date=pd.to_datetime(focal_rows["date"])).set_index("date").loc[d.index, "sessions"].sum())


def test_panel_without_exclusions_keeps_the_day(reference):
    metrics, events = reference
    p = build_panel(metrics, FOCAL, events=events, apply_exclusions=False)
    assert p.daily.loc["2026-09-20", "usable"] and not p.exclusions_applied and p.excluded_dates == []


def test_explicit_comparison_uses_only_those_rows(reference):
    metrics, events = reference
    p = build_panel(metrics, FOCAL, comparison={"device": "mobile", "browser": "chrome"}, events=events)
    rows = metrics[(metrics.device == "mobile") & (metrics.browser == "chrome")].assign(date=lambda d: pd.to_datetime(d["date"]))
    day_ = pd.Timestamp("2026-08-05")
    assert p.daily.loc[day_, "s_c"] == rows[rows["date"] == day_]["sessions"].sum()


def test_union_of_focal_segments_is_supported(reference):
    metrics, events = reference
    p = build_panel(metrics, [FOCAL, {"device": "mobile", "browser": "other"}], events=events)
    solo = build_panel(metrics, FOCAL, events=events)
    assert (p.daily["s_f"] >= solo.daily["s_f"]).all() and p.daily["s_f"].sum() > solo.daily["s_f"].sum()


@pytest.mark.parametrize(
    ("focal", "comparison", "match"),
    [({"colour": "red"}, None, "dimensions"), ({}, None, "dimensions"), ([], None, "at least one"),
     ({"device": "fridge"}, None, "no rows match"), (FOCAL, {"device": "mobile"}, "overlap")],
)
def test_panel_rejects_bad_segments(reference, focal, comparison, match):
    with pytest.raises(ValueError, match=match):
        build_panel(reference[0], focal, comparison=comparison)


def test_invalid_metrics_raise_the_typed_error():
    bad = focal_comparison_frame(1).assign(orders=-1)
    with pytest.raises(DataValidationError):
        build_panel(bad, FOCAL)


# ------------------------------------------------------------------ estimate_did


def _independent_wls(panel, onset: str) -> float:
    """Weighted least squares with date fixed effects, written from the formula with numpy only."""
    d = panel.daily[panel.daily["usable"]]
    t = len(d)
    y = np.r_[d["rate_c"].to_numpy(), d["rate_f"].to_numpy()] * 100
    w = np.r_[d["s_c"].to_numpy(), d["s_f"].to_numpy()]
    treated = np.r_[np.zeros(t), np.ones(t)]
    post = np.r_[(d.index >= pd.Timestamp(onset)), (d.index >= pd.Timestamp(onset))].astype(float)
    D = np.zeros((2 * t, t))
    D[np.arange(2 * t), np.r_[np.arange(t), np.arange(t)]] = 1
    X = np.column_stack([treated, treated * post, D])
    sw = np.sqrt(w)
    beta, *_ = np.linalg.lstsq(X * sw[:, None], y * sw, rcond=None)
    return float(beta[1])


def test_estimate_matches_an_independent_numpy_wls(panel, result):
    assert result.status == "ok"
    assert result.effect_pp.estimate == pytest.approx(_independent_wls(panel, ONSET), abs=1e-9)


def test_with_equal_weights_and_balanced_data_it_is_the_classic_did_of_means():
    n = 30
    f_pre, f_post, c_pre, c_post = 5.0, 3.0, 4.0, 4.5
    rng = np.random.default_rng(0)
    rows = []
    for i in range(n):
        for dev, brw, level in (("mobile", "safari", f_post if i >= 20 else f_pre), ("desktop", "chrome", c_post if i >= 20 else c_pre)):
            rate = level / 100 + rng.normal(0, 0.0003)
            rows.append({"date": day(i).isoformat(), "device": dev, "browser": brw, "channel": "organic", "customer_type": "new",
                         "sessions": 100_000, "orders": round(100_000 * rate)})
    df = pd.DataFrame(rows)
    r = estimate_did(df, FOCAL, day(20), weights="none")
    d = build_panel(df, FOCAL).daily
    pre, post = d[d.index < pd.Timestamp(day(20))], d[d.index >= pd.Timestamp(day(20))]
    classic = (post["diff_pp"].mean() - pre["diff_pp"].mean())
    assert r.effect_pp.estimate == pytest.approx(classic, abs=1e-9)
    assert r.effect_pp.estimate == pytest.approx((f_post - f_pre) - (c_post - c_pre), abs=0.05)


def test_reference_effect_covers_the_planted_segment_effect(result, ground_truth):
    """Ground truth is used only to grade."""
    truth = ground_truth["planted_cause"]["true_effect_in_segment_pp"]
    ci = result.effect_pp
    assert ci.lower <= truth <= ci.upper and ci.lower <= ci.estimate <= ci.upper and ci.level == 0.95
    assert (result.n_days_pre, result.n_days_post) == (73, 18) and result.n_obs == 2 * 91
    assert result.cov == "hc1" and result.control_valid and result.degrees_of_freedom == 2 * 91 - (91 + 2)


def test_the_result_states_its_identifying_assumptions(result):
    text = " ".join(result.assumptions).lower()
    assert "parallel trends" in text and "differential effect" in text and "comparison group was not affected" in text
    assert "date coincidence is not a cause" in text


def test_both_variance_options_give_the_same_estimate_and_different_errors(panel):
    a, b = did_on_panel(panel, ONSET, cov="cluster"), did_on_panel(panel, ONSET, cov="hc1")
    assert a.effect_pp.estimate == pytest.approx(b.effect_pp.estimate, abs=1e-12)
    assert a.std_error_pp != b.std_error_pp and a.cov == "cluster" and b.cov == "hc1"
    assert a.degrees_of_freedom == 90 and b.degrees_of_freedom == pytest.approx(2 * 91 - (91 + 2))
    assert a.std_error_pp > b.std_error_pp  # date clustering is the more conservative option here


def test_session_weights_matter_and_can_be_switched_off(panel):
    w, u = did_on_panel(panel, ONSET), did_on_panel(panel, ONSET, weights="none")
    assert w.weights == "sessions" and u.weights == "none" and w.effect_pp.estimate != u.effect_pp.estimate


def test_wider_level_gives_a_wider_interval(panel):
    a, b = did_on_panel(panel, ONSET, level=0.8), did_on_panel(panel, ONSET, level=0.99)
    assert (b.effect_pp.upper - b.effect_pp.lower) > (a.effect_pp.upper - a.effect_pp.lower)
    assert a.effect_pp.estimate == b.effect_pp.estimate


@pytest.mark.parametrize(
    ("onset", "match"),
    [("2026-07-01", "too few usable days"), ("2026-10-31", "too few usable days"), ("2026-08-04", "too few usable days")],
)
def test_too_few_days_is_insufficient_with_a_reason_not_an_estimate(panel, onset, match):
    r = did_on_panel(panel, onset)
    assert r.status == "insufficient" and r.effect_pp is None and match in r.reason
    assert r.p_value is None and "NaN" not in r.model_dump_json()


def test_no_valid_comparison_group_is_insufficient(reference):
    metrics, events = reference
    everything = [{"device": d} for d in ("desktop", "mobile", "tablet")]
    r = estimate_did(metrics, everything, ONSET, events=events)
    assert r.status == "insufficient" and not r.control_valid and "no valid comparison" in r.reason


@pytest.mark.parametrize("kwargs", [{"cov": "robust"}, {"weights": "huber"}, {"level": 1.0}])
def test_invalid_options_are_rejected(panel, kwargs):
    with pytest.raises(ValueError):
        did_on_panel(panel, ONSET, **kwargs)


def test_row_order_and_relabeling_do_not_change_the_result(reference):
    metrics, events = reference
    base = estimate_did(metrics, FOCAL, ONSET, events=events)
    shuffled = metrics.sample(frac=1.0, random_state=4).reset_index(drop=True)
    assert estimate_did(shuffled, FOCAL, ONSET, events=events).model_dump() == base.model_dump()
    ren = metrics.assign(browser=metrics["browser"].replace({"safari": "zz-safari", "chrome": "aa-chrome"}))
    r = estimate_did(ren, {"device": "mobile", "browser": "zz-safari"}, ONSET, events=events)
    assert r.effect_pp.estimate == pytest.approx(base.effect_pp.estimate, abs=1e-12)
    assert r.std_error_pp == pytest.approx(base.std_error_pp, abs=1e-12)


# ------------------------------------------------------------------ design properties 2, 3, 4


@settings(max_examples=40, deadline=None, derandomize=True)
@given(seed=st.integers(0, 2**31 - 1), step=st.floats(-0.4, 0.4), onset=st.integers(25, 50), level=st.sampled_from([0.8, 0.9, 0.95, 0.99]))
def test_property_every_did_interval_is_ordered(seed, step, onset, level):
    df = focal_comparison_frame(seed, 70, {onset: step})
    for cov in ("cluster", "hc1"):
        r = estimate_did(df, FOCAL, day(onset), cov=cov, level=level)
        assert r.status == "ok"
        ci = r.effect_pp
        assert ci.lower <= ci.estimate <= ci.upper and ci.level == level


def test_property_null_calibration_over_50_seeds():
    """Design property 3: with no planted effect the 95% DiD interval covers 0 in about 95% of seeds
    (accepted: 88-100% over 50 seeds). Both variance options pass; over 200 seeds HC1 (the default) covered
    95.5% and date clusters 99.5% (too conservative), which is why HC1 is the default."""
    cover = {"cluster": 0, "hc1": 0}
    for seed in range(1, 51):
        metrics, events = simulate(seed, rel=0.0)
        panel = build_panel(metrics, FOCAL, events=events)
        for cov in cover:
            ci = did_on_panel(panel, ONSET, cov=cov).effect_pp
            cover[cov] += ci.lower <= 0 <= ci.upper
    assert 0.88 <= cover["hc1"] / 50 <= 1.0, cover
    assert 0.88 <= cover["cluster"] / 50 <= 1.0, cover


@settings(max_examples=40, deadline=None, derandomize=True)
@given(
    seed=st.integers(0, 2**31 - 1),
    factors=st.lists(st.floats(0.2, 1.0), min_size=2, max_size=4).map(lambda xs: sorted(xs, reverse=True)),
    cov=st.sampled_from(["cluster", "hc1"]),
    weights=st.sampled_from(["sessions", "none"]),
)
def test_property_scaling_down_treated_post_orders_never_increases_the_estimate(seed, factors, cov, weights):
    df = focal_comparison_frame(seed, 60, {30: -0.1})
    focal_post = (df.device == "mobile") & (pd.to_datetime(df["date"]) >= pd.Timestamp(day(30)))
    estimates = []
    for f in factors:
        scaled = df.copy()
        scaled.loc[focal_post, "orders"] = np.floor(scaled.loc[focal_post, "orders"] * f).astype(int)
        estimates.append(estimate_did(scaled, FOCAL, day(30), cov=cov, weights=weights).effect_pp.estimate)
    assert all(a >= b - 1e-12 for a, b in itertools.pairwise(estimates))


def test_scaling_down_treated_post_orders_lowers_the_planted_estimate(reference):
    metrics, events = reference
    post = (metrics.device == "mobile") & (metrics.browser == "safari") & (pd.to_datetime(metrics["date"]) >= pd.Timestamp(ONSET))
    base = estimate_did(metrics, FOCAL, ONSET, events=events).effect_pp.estimate
    lower = metrics.copy()
    lower.loc[post, "orders"] = (lower.loc[post, "orders"] * 0.8).astype(int)
    assert estimate_did(lower, FOCAL, ONSET, events=events).effect_pp.estimate < base


# ------------------------------------------------------------------ pre-trends


@pytest.mark.parametrize(
    ("p", "status"),
    [(0.9, "pass"), (0.1001, "pass"), (0.10, "borderline"), (0.07, "borderline"), (0.05, "borderline"), (0.0499, "fail"), (0.0, "fail")],
)
def test_pretrend_criterion_boundaries(p, status):
    assert pretrend_status(p) == status


def test_reference_data_passes_the_pre_trend_check(panel):
    r = pre_trends_on_panel(panel, ONSET)
    assert r.status == "pass" and r.p_value > 0.1 and r.n_days == 73 and "pass if p > 0.1" in r.criterion
    assert r.slope_difference_pp_per_day.lower <= 0 <= r.slope_difference_pp_per_day.upper


def test_a_diverging_pre_trend_is_detected_and_flagged():
    df = focal_comparison_frame(3, 80, {50: -0.1}, trend_per_day=-0.004)
    r = check_pre_trends(df, FOCAL, day(50))
    assert r.status == "fail" and r.p_value < 0.05 and r.slope_difference_pp_per_day.upper < 0


def test_pre_trend_check_needs_enough_days():
    r = check_pre_trends(focal_comparison_frame(3, 40, {10: -0.1}), FOCAL, day(10))
    assert r.status == "not_evaluable" and "usable pre-period days" in r.reason and r.p_value is None


def test_pre_trend_null_failure_rate_is_close_to_its_nominal_level():
    """Under no trend about 5% of runs fail by construction: that is the cost of the criterion, measured."""
    fails = sum(check_pre_trends(focal_comparison_frame(s, 70, {45: -0.1}), FOCAL, day(45)).status == "fail" for s in range(100))
    assert 0 <= fails <= 14, fails


# ------------------------------------------------------------------ placebo


def test_holm_matches_statsmodels_and_is_monotone():
    p = [0.001, 0.04, 0.03, 0.5, 0.2]
    assert np.allclose(holm_adjust(p), multipletests(p, method="holm")[1])
    assert holm_adjust([]) == [] and holm_adjust([0.2]) == [0.2]
    assert all(a <= 1.0 for a in holm_adjust([0.9, 0.95, 1.0]))


def test_placebo_dates_are_a_fixed_grid_inside_the_pre_period(panel):
    pre = panel.daily[(panel.daily["usable"]) & (panel.daily.index < pd.Timestamp(ONSET))].index
    dates = placebo_dates(pre)
    assert dates == placebo_dates(pre) and len(dates) == 3 and dates == sorted(dates)
    assert all(pre[14].date() <= d <= pre[-7].date() for d in dates)
    assert placebo_dates(pre[:15]) == []


def test_reference_placebos_pass(panel):
    r = placebo_on_panel(panel, ONSET)
    assert r.status == "pass" and r.n_evaluated == 3 and all(p.p_value_holm >= 0.05 for p in r.placebos)
    assert all(p.fake_date < date.fromisoformat(ONSET) for p in r.placebos)


def test_a_real_looking_effect_at_a_fake_date_fails_the_placebo_check(reference):
    metrics, events = reference
    m = metrics.copy()
    late_pre = (m.device == "mobile") & (m.browser == "safari") & (pd.to_datetime(m["date"]) >= pd.Timestamp("2026-09-10")) & (pd.to_datetime(m["date"]) < pd.Timestamp(ONSET))
    m.loc[late_pre, "orders"] = (m.loc[late_pre, "orders"] * 0.7).astype(int)
    r = run_placebo(m, FOCAL, ONSET, events=events)
    assert r.status == "fail" and any(p.p_value_holm < 0.05 for p in r.placebos)


def test_explicit_placebo_dates_and_short_pre_periods(reference):
    metrics, events = reference
    r = run_placebo(metrics, FOCAL, ONSET, events=events, dates=["2026-09-05"])
    assert r.n_attempted == 1 and r.placebos[0].fake_date == date(2026, 9, 5)
    short = run_placebo(focal_comparison_frame(2, 30, {20: -0.1}), FOCAL, day(20))
    assert short.status == "not_evaluable" and short.placebos == [] and "pre-period too short" in short.reason


def test_placebo_family_false_failure_rate_is_controlled():
    fails = sum(run_placebo(focal_comparison_frame(s, 80, {60: -0.1}), FOCAL, day(60)).status == "fail" for s in range(60))
    assert fails / 60 <= 0.12, fails


# ------------------------------------------------------------------ project_impact


def test_projection_matches_hand_arithmetic(panel, result):
    im = project_impact(result, panel)
    post = panel.daily[(panel.daily["usable"]) & (panel.daily.index >= pd.Timestamp(ONSET))]
    f, total = post["s_f"].sum(), post["s_f"].sum() + post["s_c"].sum()
    e = result.effect_pp
    assert im.status == "ok" and im.focal_share_post == pytest.approx(f / total)
    assert im.overall_pp.estimate == pytest.approx(e.estimate * f / total) and im.overall_pp.lower == pytest.approx(e.lower * f / total)
    assert im.orders_change.estimate == pytest.approx(e.estimate * f / 100) and im.orders_change.upper == pytest.approx(e.upper * f / 100)
    assert im.lost_orders.estimate == pytest.approx(-e.estimate * f / 100)
    assert im.lost_orders.lower <= im.lost_orders.estimate <= im.lost_orders.upper
    assert im.overall_pp.lower <= im.overall_pp.estimate <= im.overall_pp.upper
    assert (im.focal_sessions_post, im.total_sessions_post, im.n_days_post) == (int(f), int(total), 18)


def test_projection_covers_the_planted_overall_effect(panel, result, ground_truth):
    truth = ground_truth["planted_cause"]["true_effect_on_overall_conversion_pp"]
    ci = project_impact(result, panel).overall_pp
    assert ci.lower <= truth <= ci.upper


def test_projection_states_the_unaffected_comparison_assumption(panel, result):
    text = " ".join(project_impact(result, panel).assumptions)
    assert "comparison group was unaffected" in text and "constant over the post period" in text


def test_positive_effects_become_gained_orders(panel):
    base = did_on_panel(panel, ONSET)
    flipped = base.model_copy(update={"effect_pp": base.effect_pp.model_copy(update={"estimate": 0.5, "lower": 0.2, "upper": 0.8})})
    im = project_impact(flipped, panel)
    assert im.orders_change.estimate > 0 and im.lost_orders.estimate < 0 and im.lost_orders.lower <= im.lost_orders.upper


def test_projection_is_unavailable_without_an_estimate(panel):
    insufficient = did_on_panel(panel, "2026-07-01")
    im = project_impact(insufficient, panel)
    assert im.status == "unavailable" and im.overall_pp is None and im.reason
    assert isinstance(insufficient, DidResult)


def test_impact_is_deterministic_and_json_safe(panel, result):
    a, b = project_impact(result, panel), project_impact(result, panel)
    assert a.model_dump() == b.model_dump() and "NaN" not in a.model_dump_json()


def test_scenario_data_helper_does_not_modify_the_simulator(ground_truth):
    import causelens.sim.generate as G

    before = (G.RELEASE_DATE, G.RELEASE_RELATIVE_EFFECT, G.is_treated)
    scenario_data(1, "hero_immediate")
    scenario_data(1, "none")
    assert (G.RELEASE_DATE, G.RELEASE_RELATIVE_EFFECT, G.is_treated) == before
    assert ground_truth["planted_cause"]["relative_effect_in_segment"] == -0.25
