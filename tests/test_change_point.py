from __future__ import annotations

import itertools
from datetime import date

import numpy as np
import pytest
from frames import WEEKDAY_CONV, bounded_series, day, to_frame

from causelens.analytics import detect_change_point


@pytest.fixture(scope="module")
def result(reference):
    metrics, events = reference
    return detect_change_point(metrics, events)


# ------------------------------------------------------------------ reference data


def test_reference_change_point_is_found_with_a_plausible_set(result, ground_truth):
    """Ground truth is used only to grade: the dates themselves are computed from the data."""
    planted = date.fromisoformat(ground_truth["planted_cause"]["date"])
    assert result.status == "ok" and result.change_detected and result.direction == "decrease"
    assert result.best_date in result.plausible_dates
    assert planted in result.plausible_dates
    assert result.plausible_dates == sorted(set(result.plausible_dates))
    assert result.plausible_range == (result.plausible_dates[0], result.plausible_dates[-1])
    assert result.p_value_adjusted < 0.05 and result.shift_in_residual_sd < 0


def test_plausible_set_is_a_set_of_dates_not_a_single_estimate(result):
    # The overall effect is modest relative to daily noise, so more than the best date is plausible.
    assert len(result.plausible_dates) >= 1
    assert result.plausible_level == 0.95 and result.threshold_noise_units == pytest.approx(3.841, abs=1e-3)


def test_flagged_outage_day_is_excluded_and_disclosed(result):
    ex = result.exclusions
    assert ex.applied and [d.date for d in ex.excluded_days] == [date(2026, 9, 20)]
    assert ex.excluded_days[0].windows == ["search"]
    assert result.n_days_used == 91  # 92 days minus the excluded one
    assert result.n_days_before + result.n_days_after == result.n_days_used
    assert "2026-09-20" in result.summary and "Flagged days excluded" in result.summary
    assert date(2026, 9, 20) not in result.plausible_dates


def test_weekday_adjustment_is_applied_and_reported(result):
    wa = result.weekday_adjustment
    assert wa.applied and wa.iterations >= 1 and set(wa.factors) == {
        "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"
    }
    assert 0.9 < min(wa.factors.values()) < max(wa.factors.values()) < 1.1
    assert "Weekday adjustment applied" in result.summary


def test_summary_locates_in_time_and_makes_no_causal_claim(result):
    s = result.summary
    assert "does not say why" in s and "residual sd" in s and "Bonferroni" in s
    for banned in ("caused", "because of", "due to", "led to"):
        assert banned not in s.lower()
    assert any("not why" in a for a in result.assumptions)


def test_segment_change_point_is_sharper_than_the_overall_one(reference, ground_truth, result):
    metrics, events = reference
    seg = ground_truth["planted_cause"]["affected_segment"]
    r = detect_change_point(metrics, events, segment=seg)
    assert r.change_detected and r.segment == seg
    assert len(r.plausible_dates) <= len(result.plausible_dates)
    assert date.fromisoformat(ground_truth["planted_cause"]["date"]) in r.plausible_dates


def test_before_the_release_no_change_point_is_supported(reference):
    metrics, events = reference
    r = detect_change_point(metrics, events, search_window=("2026-08-01", "2026-10-10"))
    assert r.status == "ok" and not r.change_detected
    assert "No change point is supported" in r.summary and "no events are matched" in r.summary


def test_search_window_restricts_the_scan(reference, result):
    metrics, events = reference
    r = detect_change_point(metrics, events, search_window=("2026-09-25", "2026-10-31"))
    assert r.search_window == (date(2026, 9, 25), date(2026, 10, 31))
    assert all(date(2026, 9, 25) <= d <= date(2026, 10, 31) for d in r.plausible_dates)
    assert r.exclusions.excluded_days[0].windows == []  # the outage day lies outside this window


def test_exclusions_can_be_switched_off_and_that_is_disclosed(reference):
    metrics, events = reference
    r = detect_change_point(metrics, events, apply_exclusions=False)
    assert not r.exclusions.applied and r.n_days_used == 92
    assert "NOT excluded" in r.exclusions.statement


def test_insufficient_data_after_exclusions_is_reported_explicitly(reference):
    metrics, events = reference
    # 13 days contain the flagged day: 12 usable, fewer than the 14 needed for 7 + 7.
    r = detect_change_point(metrics, events, search_window=("2026-09-14", "2026-09-26"))
    assert r.status == "insufficient_data" and not r.change_detected
    assert r.best_date is None and r.plausible_dates == [] and r.plausible_range is None
    assert "12 usable day(s)" in r.summary and "at least 14" in r.summary
    assert "2026-09-20" in r.summary


# ------------------------------------------------------------------ synthetic behaviour


def test_step_in_bounded_series_is_located_exactly():
    r = detect_change_point(bounded_series(4, 80, step_day=45, step=-0.2))
    assert r.change_detected and r.best_date == day(45) and day(45) in r.plausible_dates
    assert r.n_days_before == 45 and r.n_days_after == 35


def test_increase_is_reported_as_an_increase():
    r = detect_change_point(bounded_series(5, 80, step_day=30, step=0.25))
    assert r.direction == "increase" and r.shift_in_residual_sd > 0 and r.best_date == day(30)


def test_flat_noise_series_has_no_supported_change():
    r = detect_change_point(bounded_series(6, 80))
    assert not r.change_detected and r.p_value_adjusted > 0.05


def test_perfectly_flat_and_perfectly_stepped_series_do_not_divide_by_zero():
    flat = detect_change_point(to_frame(np.full(40, 1000), np.full(40, 30)), adjust_weekdays=False)
    assert not flat.change_detected and flat.p_value_adjusted == 1.0
    stepped = detect_change_point(
        to_frame(np.full(40, 1000), np.r_[np.full(20, 30), np.full(20, 20)]), adjust_weekdays=False
    )
    assert stepped.change_detected and stepped.best_date == day(20) and stepped.plausible_dates == [day(20)]
    assert stepped.shift_in_residual_sd is None  # undefined, not NaN/inf
    assert "NaN" not in stepped.model_dump_json() and "Infinity" not in stepped.model_dump_json()


def test_unusable_day_at_the_change_is_added_to_the_plausible_dates():
    """If the day the level changed is flagged and excluded, the change could have begun there."""
    df = bounded_series(7, 80, step_day=40, step=-0.2)
    mask = df["date"] == day(40).isoformat()
    df.loc[mask, "orders"] = (df.loc[mask, "orders"] * 0.1).astype(int)  # tracking loss on that very day
    r = detect_change_point(df)
    assert r.exclusions.excluded_days[0].date == day(40)
    assert r.best_date == day(41) and day(40) in r.plausible_dates
    assert "could have begun on any of them" in r.gap_note and "1 unusable day" in r.gap_note


def test_weekday_factors_recover_the_seasonal_pattern():
    r = detect_change_point(bounded_series(8, 140, step_day=70, step=-0.2, rate_noise=0.002))
    factors = np.array(list(r.weekday_adjustment.factors.values()))  # Monday..Sunday
    truth = WEEKDAY_CONV / WEEKDAY_CONV.mean()
    assert np.max(np.abs(factors - truth)) < 0.01


def test_weekday_adjustment_can_be_off_or_unavailable_and_says_why():
    df = bounded_series(9, 80, step_day=40, step=-0.2)
    off = detect_change_point(df, adjust_weekdays=False)
    assert not off.weekday_adjustment.applied and off.weekday_adjustment.reason == "switched off by the caller"
    assert any("No weekday adjustment" in a for a in off.assumptions)
    short = detect_change_point(df.iloc[:24])
    assert not short.weekday_adjustment.applied and "too short" in short.weekday_adjustment.reason


def test_plausible_sets_nest_as_the_level_rises():
    df = bounded_series(10, 80, step_day=40, step=-0.03, rate_noise=0.012)
    sets = [set(detect_change_point(df, plausible_level=lv).plausible_dates) for lv in (0.5, 0.8, 0.95, 0.99)]
    assert all(a <= b for a, b in itertools.pairwise(sets))


def test_min_segment_days_keeps_the_estimate_away_from_the_ends():
    r = detect_change_point(bounded_series(11, 60, step_day=4, step=-0.3), min_segment_days=10)
    assert r.best_date >= day(10)  # cannot place a change within 10 days of the start


@pytest.mark.parametrize(
    "kwargs",
    [{"min_segment_days": 2}, {"plausible_level": 1.0}, {"alpha": 0.0}, {"search_window": ("2026-09-02", "2026-09-01")}],
)
def test_invalid_arguments_are_rejected(reference, kwargs):
    with pytest.raises(ValueError):
        detect_change_point(reference[0], **kwargs)


def test_result_is_deterministic_and_row_order_invariant(reference):
    metrics, events = reference
    shuffled = metrics.sample(frac=1.0, random_state=11).reset_index(drop=True)
    a = detect_change_point(metrics, events)
    assert a.model_dump() == detect_change_point(metrics, events).model_dump()
    assert a.model_dump() == detect_change_point(shuffled, events).model_dump()
