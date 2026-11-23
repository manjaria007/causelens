"""``daily_series`` (Task 10): the daily values behind the series the console draws. A description, with property tests of its invariants."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from frames import binomial_series, to_frame
from hypothesis import given, settings
from hypothesis import strategies as st

import causelens.analytics as A
from causelens.analytics.daily import METRIC, DailySeriesResult

DIMS = ["device", "browser", "channel", "customer_type"]


@st.composite
def frames(draw):
    n = draw(st.integers(3, 25))
    sessions = np.array(draw(st.lists(st.integers(0, 3000), min_size=n, max_size=n)))
    frac = np.array(draw(st.lists(st.floats(0, 0.2), min_size=n, max_size=n)))
    orders = np.floor(sessions * frac).astype(int)
    frame = to_frame(sessions, orders)
    drop = draw(st.lists(st.integers(1, n - 2), max_size=3, unique=True)) if n > 3 else []
    return frame.drop(index=[i for i in drop if i < len(frame)]).reset_index(drop=True)


@settings(max_examples=60, deadline=None)
@given(frames())
def test_points_are_the_days_with_data_in_order_and_the_gaps_are_listed(frame):
    r = A.daily_series(frame)
    dates = [p.date for p in r.points]
    assert dates == sorted(set(dates)) and set(map(str, dates)) == set(frame["date"])
    calendar = pd.date_range(frame["date"].min(), frame["date"].max()).date
    assert sorted(set(calendar) - set(dates)) == r.missing_dates
    assert (r.first_date, r.last_date) == (min(calendar), max(calendar))


@settings(max_examples=60, deadline=None)
@given(frames())
def test_conversion_is_orders_over_sessions_and_totals_are_preserved(frame):
    r = A.daily_series(frame)
    by_day = frame.set_index("date")
    for p in r.points:
        row = by_day.loc[p.date.isoformat()]
        assert (p.sessions, p.orders) == (row["sessions"], row["orders"])
        assert p.conversion == (None if p.sessions == 0 else p.orders / p.sessions)
        assert (p.status == "no_sessions") == (p.sessions == 0 and p.status != "excluded")
    assert sum(p.sessions for p in r.points) == frame["sessions"].sum() and sum(p.orders for p in r.points) == frame["orders"].sum()


@settings(max_examples=40, deadline=None)
@given(frames(), st.randoms(use_true_random=False))
def test_row_order_does_not_change_the_result(frame, rnd):
    shuffled = frame.sample(frac=1.0, random_state=rnd.randint(0, 10**6)).reset_index(drop=True)
    assert A.daily_series(shuffled) == A.daily_series(frame)


def test_a_segment_is_the_sum_of_its_rows_and_bad_segments_are_refused():
    a, b = to_frame([100, 200, 300, 400], [10, 20, 30, 40]), to_frame([50, 50, 50, 50], [1, 1, 1, 1])
    b["device"] = "mobile"
    both = pd.concat([a, b], ignore_index=True)
    whole, desktop = A.daily_series(both), A.daily_series(both, segment={"device": "desktop"})
    assert [p.sessions for p in whole.points] == [150, 250, 350, 450] and [p.sessions for p in desktop.points] == [100, 200, 300, 400]
    assert desktop.segment == {"device": "desktop"} and whole.segment == {}
    with pytest.raises(ValueError, match="unknown segment dimension"):
        A.daily_series(both, segment={"colour": "red"})
    with pytest.raises(ValueError, match="no rows match"):
        A.daily_series(both, segment={"device": "tablet"})


def test_excluded_days_are_marked_and_keep_their_recorded_counts():
    frame = binomial_series(3, n_days=60, base_rate=0.04, base_sessions=20_000)
    frame.loc[30, "orders"] = int(frame.loc[30, "orders"] * 0.3)  # a tracking loss on one day
    q = A.check_data_quality(frame)
    assert q.excluded_dates, "the injected loss should be flagged"
    r = A.daily_series(frame, quality=q)
    marked = [p.date for p in r.points if p.status == "excluded"]
    assert marked == list(q.excluded_dates)
    day = next(p for p in r.points if p.status == "excluded")
    assert day.orders == int(frame.loc[30, "orders"]) and day.conversion == day.orders / day.sessions  # what was recorded, not blanked
    plain = A.daily_series(frame, quality=q, apply_exclusions=False)
    assert all(p.status != "excluded" for p in plain.points)
    assert [(p.sessions, p.orders) for p in plain.points] == [(p.sessions, p.orders) for p in r.points]


def test_the_series_agrees_with_confirm_change_on_the_windows(reference):
    metrics, events = reference
    q = A.check_data_quality(metrics, events)
    pre, post = ("2026-08-01", "2026-10-13"), ("2026-10-14", "2026-10-31")
    series = A.daily_series(metrics, events, quality=q)
    confirm = A.confirm_change(metrics, pre, post, events, quality=q)
    for window, summary in ((pre, confirm.pre), (post, confirm.post)):
        ok = [p for p in series.points if window[0] <= p.date.isoformat() <= window[1] and p.status == "ok"]
        assert len(ok) == summary.n_days_used
        assert sum(p.sessions for p in ok) == summary.sessions and sum(p.orders for p in ok) == summary.orders
        assert sum(p.orders for p in ok) / sum(p.sessions for p in ok) == pytest.approx(summary.conversion.estimate, rel=1e-12)


def test_the_reference_series_has_the_outage_and_the_drop(reference):
    metrics, events = reference
    r = A.daily_series(metrics, events)
    assert r.status == "ok" and r.metric == METRIC and len(r.points) == 92 and r.missing_dates == []
    assert [p.date.isoformat() for p in r.points if p.status == "excluded"] == ["2026-09-20"]
    outage = next(p for p in r.points if p.status == "excluded")
    typical = sorted(p.conversion for p in r.points if p.status == "ok")[len(r.points) // 2]
    assert outage.conversion < 0.8 * typical  # the tracking loss is visible in what was recorded


def test_the_result_is_deterministic_and_round_trips(reference):
    metrics, events = reference
    a, b = A.daily_series(metrics, events), A.daily_series(metrics, events)
    assert a == b and DailySeriesResult.model_validate_json(a.model_dump_json()) == a


def test_a_single_day_is_insufficient():
    r = A.daily_series(to_frame([100], [5]))
    assert r.status == "insufficient_data" and len(r.points) == 1


# --------------------------------------------------------------------------- review of PR #11: missing dates

def _two_device_frame(missing_mobile_days: list[int], n: int = 60) -> pd.DataFrame:
    desktop = binomial_series(11, n_days=n, base_rate=0.04, base_sessions=20_000)
    desktop.loc[30, "orders"] = int(desktop.loc[30, "orders"] * 0.3)  # a desktop tracking loss, flagged for the whole data
    mobile = binomial_series(12, n_days=n, base_rate=0.03, base_sessions=9_000)
    mobile["device"] = "mobile"
    mobile = mobile.drop(index=missing_mobile_days)
    return pd.concat([desktop, mobile], ignore_index=True)


def test_a_segment_with_no_row_on_an_excluded_day_still_reports_that_day_missing():
    frame = _two_device_frame([30])
    q = A.check_data_quality(frame)
    assert pd.Timestamp(frame.loc[30, "date"]).date() in q.excluded_dates  # the desktop outage excludes that date for everyone
    r = A.daily_series(frame, quality=q, segment={"device": "mobile"})
    gap = pd.Timestamp(frame.loc[30, "date"]).date()
    assert len(r.points) == 59 and r.missing_dates == [gap]
    assert gap not in [p.date for p in r.points]
    assert (r.last_date - r.first_date).days + 1 == len(r.points) + len(r.missing_dates)  # every calendar day is accounted for
    whole = A.daily_series(frame, quality=q)  # the whole data has desktop rows on that day
    assert len(whole.points) == 60 and whole.missing_dates == [] and [p.date for p in whole.points if p.status == "excluded"] == [gap]


def test_missing_and_excluded_days_are_reported_independently_of_each_other():
    frame = _two_device_frame([30, 10, 11])
    q = A.check_data_quality(frame)
    r = A.daily_series(frame, quality=q, segment={"device": "mobile"})
    expected = sorted(pd.Timestamp(frame.loc[i, "date"]).date() for i in (10, 11, 30))
    assert r.missing_dates == expected
    for apply in (True, False):
        again = A.daily_series(frame, quality=q, segment={"device": "mobile"}, apply_exclusions=apply)
        assert again.missing_dates == expected and len(again.points) == 57


@st.composite
def sparse_two_segment_frames(draw):
    n = draw(st.integers(4, 30))
    frames_ = []
    for device in ("desktop", "mobile"):
        keep = sorted(draw(st.sets(st.integers(0, n - 1), min_size=2, max_size=n)))
        sessions = np.array(draw(st.lists(st.integers(1, 2000), min_size=len(keep), max_size=len(keep))))
        frame = to_frame(sessions, sessions // 25)
        frame["date"] = (pd.Timestamp("2026-08-01") + pd.to_timedelta(keep, unit="D")).strftime("%Y-%m-%d")
        frame["device"] = device
        frames_.append(frame)
    return pd.concat(frames_, ignore_index=True)


@settings(max_examples=60, deadline=None)
@given(sparse_two_segment_frames(), st.sampled_from(["desktop", "mobile"]), st.booleans())
def test_every_calendar_day_is_a_point_or_listed_missing_never_both_and_never_neither(frame, device, apply):
    r = A.daily_series(frame, segment={"device": device}, apply_exclusions=apply)
    calendar = set(pd.date_range(r.first_date, r.last_date).date)
    points = [p.date for p in r.points]
    assert set(points) | set(r.missing_dates) == calendar and not set(points) & set(r.missing_dates)
    assert r.missing_dates == sorted(r.missing_dates)
    assert set(map(str, points)) == set(frame.loc[frame["device"] == device, "date"])
