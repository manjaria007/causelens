from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import pytest
from frames import day, to_frame

from causelens.analytics import check_data_quality, prepare_daily_series
from causelens.analytics.series import build_disclosure

OUTAGE = date(2026, 9, 20)


@pytest.fixture(scope="module")
def prepared(reference):
    metrics, events = reference
    return prepare_daily_series(metrics, events)


def test_flagged_day_is_excluded_from_the_series_and_disclosed(prepared):
    row = prepared.daily.loc["2026-09-20"]
    assert row["status"] == "excluded" and np.isnan(row["rate"]) and np.isnan(row["sessions"])
    ok = prepared.daily[prepared.daily["status"] == "ok"]
    assert len(ok) == len(prepared.daily) - 1
    d = prepared.disclosure
    assert d.applied and [x.date for x in d.excluded_days] == [OUTAGE]
    day_info = d.excluded_days[0]
    assert "device=desktop" in day_info.segments
    assert "possible_tracking_issue" in day_info.kinds and day_info.n_flags > 1
    assert "excluded" in d.statement and "2026-09-20" in d.statement


def test_rate_is_orders_over_sessions_on_usable_days(reference, prepared):
    metrics, _ = reference
    manual = metrics.groupby("date")[["sessions", "orders"]].sum()
    manual["rate"] = manual["orders"] / manual["sessions"]
    got = prepared.daily[prepared.daily["status"] == "ok"]
    assert np.allclose(got["rate"].to_numpy(), manual.drop("2026-09-20")["rate"].to_numpy())


def test_incident_context_is_labelled_as_coincidence_not_cause(prepared):
    ctx = prepared.disclosure.excluded_days[0].incident_context
    assert [c.name for c in ctx] == ["Analytics tag deploy"]
    assert ctx[0].overlap_basis == "observed_on_flagged_day"
    assert ctx[0].certainty == "coincides_in_time_and_traffic"
    assert "not proof" in ctx[0].note
    assert prepared.disclosure.uncertain_context_note is None


def test_exclusions_can_be_switched_off_and_that_is_disclosed(reference):
    metrics, events = reference
    p = prepare_daily_series(metrics, events, apply_exclusions=False)
    assert p.daily.loc["2026-09-20", "status"] == "ok"
    assert p.disclosure.applied is False
    assert "NOT excluded" in p.disclosure.statement and "2026-09-20" in p.disclosure.statement


def test_segment_series_filters_rows_but_uses_whole_data_quality(reference):
    metrics, events = reference
    seg = {"device": "mobile", "browser": "safari"}
    p = prepare_daily_series(metrics, events, segment=seg)
    sub = metrics[(metrics["device"] == "mobile") & (metrics["browser"] == "safari")]
    manual = sub.groupby("date")["sessions"].sum()
    got = p.daily[p.daily["status"] == "ok"]["sessions"]
    assert (got.to_numpy() == manual.drop("2026-09-20").to_numpy()).all()
    assert p.daily.loc["2026-09-20", "status"] == "excluded"  # flagged on the whole dataset
    assert p.segment == seg


def test_segment_errors(reference):
    metrics, _ = reference
    with pytest.raises(ValueError, match="unknown segment dimension"):
        prepare_daily_series(metrics, segment={"colour": "red"})
    with pytest.raises(ValueError, match="no rows match segment"):
        prepare_daily_series(metrics, segment={"device": "fridge"})


def test_missing_and_zero_session_days_are_marked_not_dropped():
    df = to_frame(np.full(30, 1000), np.full(30, 30))
    df = df[df["date"] != "2026-08-10"]
    df.loc[df["date"] == "2026-08-20", ["sessions", "orders"]] = 0
    p = prepare_daily_series(df, apply_exclusions=False)
    assert p.daily.loc["2026-08-10", "status"] == "missing"
    assert p.daily.loc["2026-08-20", "status"] == "no_sessions"
    assert len(p.daily) == 30 and p.daily["rate"].notna().sum() == 28


def test_supplied_quality_report_is_reused(reference):
    metrics, events = reference
    q = check_data_quality(metrics, events)
    assert prepare_daily_series(metrics, events, quality=q).quality is q


def _zero_volume_frame():
    df = to_frame(np.full(60, 1000), np.full(60, 30))
    df.loc[30, ["sessions", "orders"]] = 0
    events = pd.DataFrame(
        {"date": [day(30).isoformat()], "type": ["incident"], "name": ["Collector outage"], "scope": ["device=desktop"]}
    )
    return df, events


def test_historical_only_incident_links_are_disclosed_as_uncertain():
    df, events = _zero_volume_frame()
    d = prepare_daily_series(df, events).disclosure
    ctx = d.excluded_days[0].incident_context
    assert [c.overlap_basis for c in ctx] == ["historical_only"]
    assert ctx[0].certainty == "uncertain_historical_overlap_only"
    assert "Do not treat it as an explanation" in ctx[0].note
    assert d.uncertain_context_note and "uncertain context" in d.uncertain_context_note


def test_windows_label_which_analysis_windows_contain_excluded_days(reference):
    metrics, events = reference
    q = check_data_quality(metrics, events)
    d = build_disclosure(q, True, {"pre": (date(2026, 9, 1), date(2026, 9, 30)), "post": (date(2026, 10, 1), date(2026, 10, 31))})
    assert d.excluded_days[0].windows == ["pre"]
