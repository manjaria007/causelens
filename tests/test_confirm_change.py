from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import pytest
from frames import bounded_series, day, to_frame

from causelens.analytics import DataValidationError, confirm_change

PRE = ("2026-08-01", "2026-10-13")
POST = ("2026-10-14", "2026-10-31")


@pytest.fixture(scope="module")
def result(reference):
    metrics, events = reference
    return confirm_change(metrics, PRE, POST, events)


# ------------------------------------------------------------------ correctness on the reference data


def test_before_after_match_an_independent_computation(reference, result):
    metrics, _ = reference
    d = metrics.groupby("date")[["sessions", "orders"]].sum()
    pre = d.loc["2026-08-01":"2026-10-13"].drop("2026-09-20")  # flagged day excluded by default
    post = d.loc["2026-10-14":"2026-10-31"]
    before, after = pre["orders"].sum() / pre["sessions"].sum(), post["orders"].sum() / post["sessions"].sum()
    assert result.pre.conversion.estimate == pytest.approx(before)
    assert result.post.conversion.estimate == pytest.approx(after)
    assert result.abs_change_pp.estimate == pytest.approx(100 * (after - before))
    assert result.rel_change.estimate == pytest.approx(after / before - 1)
    assert (result.pre.sessions, result.pre.orders) == (int(pre["sessions"].sum()), int(pre["orders"].sum()))
    assert (result.pre.n_days_used, result.post.n_days_used) == (len(pre), len(post))


def test_reference_change_is_confirmed_with_effect_size_and_intervals(result):
    assert result.status == "ok" and result.is_real and result.verdict == "real_change"
    assert not result.stop_investigation and result.data_needed is None
    assert result.abs_change_pp.upper < 0  # the interval excludes zero on the negative side
    nt = result.noise_test
    assert nt.welch_significant and nt.bootstrap_excludes_zero and nt.p_value < nt.alpha
    assert nt.effect_size_pre_sd < 0  # measured in the pre-period's own daily sd
    assert nt.pre_daily_sd_pp > 0


def test_every_interval_is_ordered_and_95_percent(result):
    for iv in (result.pre.conversion, result.post.conversion, result.abs_change_pp, result.rel_change):
        assert iv.lower <= iv.estimate <= iv.upper and iv.level == 0.95


def test_summary_reports_sample_sizes_effect_size_and_does_not_claim_a_cause(result):
    s = result.summary
    assert "n=" in s and "95% CI" in s and "Effect size" in s and "Welch p" in s
    assert "does not say why" in s
    for banned in ("caused", "because of", "due to", "led to"):
        assert banned not in s.lower()


def test_segment_change_interval_covers_the_planted_effect(reference, ground_truth):
    """Evaluation-only use of ground truth: the interval is computed from the data alone."""
    metrics, events = reference
    seg = ground_truth["planted_cause"]["affected_segment"]
    r = confirm_change(metrics, PRE, POST, events, segment=seg)
    truth = ground_truth["planted_cause"]["true_effect_in_segment_pp"]
    assert r.segment == seg and r.is_real
    assert r.abs_change_pp.lower <= truth <= r.abs_change_pp.upper


# ------------------------------------------------------------------ exclusions are used and disclosed


def test_default_exclusions_are_applied_and_disclosed(result):
    assert result.pre.n_days_excluded == 1 and result.post.n_days_excluded == 0
    ex = result.exclusions
    assert ex.applied and [(d.date, d.windows) for d in ex.excluded_days] == [(date(2026, 9, 20), ["pre"])]
    assert "2026-09-20 (pre)" in result.summary
    assert ex.excluded_days[0].incident_context[0].name == "Analytics tag deploy"


def test_without_exclusions_the_outage_day_is_included_and_says_so(reference, result):
    metrics, events = reference
    r = confirm_change(metrics, PRE, POST, events, apply_exclusions=False)
    assert r.pre.n_days_used == result.pre.n_days_used + 1 and r.pre.n_days_excluded == 0
    assert r.pre.conversion.estimate < result.pre.conversion.estimate  # the outage depresses 'before'
    assert not r.exclusions.applied and "not excluded" in r.summary.lower()


def test_insufficient_data_after_exclusions_is_reported_explicitly(reference):
    metrics, events = reference
    # 7 requested days, one of them flagged: only 6 usable, below the 7 required.
    r = confirm_change(metrics, ("2026-09-14", "2026-09-20"), ("2026-09-21", "2026-09-27"), events)
    assert r.status == "insufficient_data" and r.verdict == "insufficient_data"
    assert r.stop_investigation and not r.is_real
    assert r.abs_change_pp is None and r.rel_change is None and r.noise_test is None
    assert r.pre.n_days_used == 6 and r.pre.n_days_excluded == 1
    assert "pre window has 6 usable day(s) of 7 requested (1 excluded as flagged" in r.summary
    assert "2026-09-20 (pre)" in r.summary and "No before/after estimate" in r.summary


def test_windows_outside_the_data_are_insufficient_not_an_error(reference):
    metrics, _ = reference
    r = confirm_change(metrics, ("2026-06-01", "2026-06-30"), ("2026-10-14", "2026-10-31"))
    assert r.status == "insufficient_data" and r.pre.n_days_used == 0
    assert r.pre.n_days_missing == 30


def test_uncertain_historical_overlap_is_carried_into_assumptions():
    df = to_frame(np.full(60, 1000), np.full(60, 30))
    df.loc[30, ["sessions", "orders"]] = 0  # collection gap: a zero-volume day
    events = pd.DataFrame(
        {"date": [day(30).isoformat()], "type": ["incident"], "name": ["Collector outage"], "scope": ["device=desktop"]}
    )
    r = confirm_change(df, (day(0), day(30)), (day(31), day(59)), events)
    assert r.exclusions.excluded_days[0].incident_context[0].overlap_basis == "historical_only"
    assert any("uncertain context" in a for a in r.assumptions)


def test_truncated_baseline_exclusions_inside_a_window_are_cautioned():
    rate = np.where(np.arange(40) >= 37, 200, 300)  # persistent drop in the last 3 days
    r = confirm_change(to_frame(np.full(40, 10_000), rate), (day(0), day(29)), (day(30), day(39)))
    assert r.status == "ok" and r.post.n_days_excluded == 3 and r.post.n_days_used == 7
    assert any("truncated baseline" in a for a in r.assumptions)


# ------------------------------------------------------------------ not real / not enough data


def test_stable_period_is_not_distinguishable_and_the_investigation_stops(reference):
    metrics, events = reference
    r = confirm_change(metrics, ("2026-09-01", "2026-09-30"), ("2026-10-01", "2026-10-13"), events)
    assert r.status == "ok" and not r.is_real and r.stop_investigation
    assert r.verdict == "not_distinguishable_from_noise"
    assert r.abs_change_pp.lower <= 0 <= r.abs_change_pp.upper
    assert "NOT distinguishable" in r.summary and "stop" in r.summary
    dn = r.data_needed
    assert dn.minimum_detectable_change_pp > 0
    assert dn.days_per_window_for_observed_change > r.post.n_days_used
    assert dn.additional_days_per_window == dn.days_per_window_for_observed_change - min(r.pre.n_days_used, r.post.n_days_used)
    assert "more per window" in dn.note


def test_disagreeing_tests_are_reported_as_not_distinguishable(reference):
    metrics, events = reference
    r = confirm_change(metrics, PRE, POST, events, alpha=1e-12)  # a threshold the Welch p-value does not reach
    assert r.noise_test.bootstrap_excludes_zero and not r.noise_test.welch_significant
    assert not r.is_real and r.stop_investigation
    assert "disagree" in r.noise_test.note


def test_zero_variability_windows():
    base = to_frame(np.full(28, 1000), np.full(28, 30))
    shifted = to_frame(np.full(28, 1000), np.r_[np.full(14, 30), np.full(14, 20)])
    real = confirm_change(shifted, (day(0), day(13)), (day(14), day(27)))
    assert real.is_real and real.noise_test.p_value == 0.0 and real.noise_test.effect_size_pre_sd is None
    same = confirm_change(base, (day(0), day(13)), (day(14), day(27)))
    assert not same.is_real and same.noise_test.p_value == 1.0
    assert "essentially zero" in same.data_needed.note


def test_weekday_mix_difference_is_reported_and_qualified():
    df = bounded_series(1, 60)
    even = confirm_change(df, (day(0), day(13)), (day(14), day(27)))  # two whole weeks each
    assert even.weekday_mix_difference == 0
    uneven = confirm_change(df, (day(0), day(11)), (day(14), day(27)))
    assert uneven.weekday_mix_difference > 0.1
    assert any("weekday mixes differ" in a for a in uneven.assumptions)


# ------------------------------------------------------------------ determinism and invariances


def test_same_seed_same_result_and_different_seed_changes_only_the_bootstrap(reference):
    metrics, events = reference
    a = confirm_change(metrics, PRE, POST, events, seed=7)
    b = confirm_change(metrics, PRE, POST, events, seed=7)
    c = confirm_change(metrics, PRE, POST, events, seed=8)
    assert a.model_dump() == b.model_dump()
    assert a.abs_change_pp.estimate == c.abs_change_pp.estimate
    assert a.abs_change_pp.lower != c.abs_change_pp.lower
    assert a.noise_test == c.noise_test


def test_row_order_does_not_change_the_result(reference):
    metrics, events = reference
    shuffled = metrics.sample(frac=1.0, random_state=3).reset_index(drop=True)
    assert confirm_change(shuffled, PRE, POST, events).model_dump() == confirm_change(metrics, PRE, POST, events).model_dump()


def test_scaling_post_orders_down_never_increases_the_estimate():
    df = bounded_series(2, 56, step_day=28, step=-0.05)
    pre, post = (day(0), day(27)), (day(28), day(55))
    previous = None
    for factor in (1.0, 0.9, 0.7, 0.5):
        scaled = df.copy()
        late = scaled["date"] >= day(28).isoformat()
        scaled.loc[late, "orders"] = np.floor(scaled.loc[late, "orders"] * factor).astype(int)
        est = confirm_change(scaled, pre, post).abs_change_pp.estimate
        assert previous is None or est <= previous
        previous = est


# ------------------------------------------------------------------ invalid arguments


@pytest.mark.parametrize(
    ("pre", "post", "match"),
    [
        (("2026-09-10", "2026-09-01"), ("2026-10-01", "2026-10-10"), "starts .* after it ends"),
        (("2026-09-01", "2026-09-30"), ("2026-09-30", "2026-10-10"), "must end before"),
        (("2026-09-01", "2026-09-30"), ("2026-09-15", "2026-10-10"), "must end before"),
        (("not-a-date", "2026-09-30"), ("2026-10-01", "2026-10-10"), "must be"),
    ],
)
def test_invalid_windows_are_rejected(reference, pre, post, match):
    with pytest.raises(ValueError, match=match):
        confirm_change(reference[0], pre, post)


@pytest.mark.parametrize("kwargs", [{"n_bootstrap": 10}, {"level": 1.0}, {"alpha": 0.0}, {"alpha": 0.9}, {"min_days_per_window": 1}])
def test_invalid_parameters_are_rejected(reference, kwargs):
    with pytest.raises(ValueError):
        confirm_change(reference[0], PRE, POST, **kwargs)


def test_invalid_metrics_still_raise_the_typed_error():
    bad = to_frame(np.full(20, 100), np.full(20, 5)).assign(orders=-1)
    with pytest.raises(DataValidationError):
        confirm_change(bad, (day(0), day(9)), (day(10), day(19)))


def test_result_serialises_to_json_without_nan(result):
    assert "NaN" not in result.model_dump_json()
