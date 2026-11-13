from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from causelens.analytics import (
    DetectorParams,
    apply_default_exclusions,
    check_data_quality,
)
from causelens.sim.generate import generate

OUTAGE = date(2026, 9, 20)


@pytest.fixture(scope="module")
def report(reference):
    metrics, events = reference
    return check_data_quality(metrics, events)


def test_outage_desktop_flagged_and_linked_to_tag_deploy(report, ground_truth):
    # Ground truth is used only to say which day the evaluation expects.
    expected = ground_truth["grading"]["must_flag_data_quality"]
    assert expected == [OUTAGE.isoformat()]
    flags = report.flags_for(OUTAGE, "device=desktop")
    assert len(flags) == 1
    flag = flags[0]
    assert flag.kind == "possible_tracking_issue"
    assert flag.sessions_normal is True
    assert flag.relative_deviation < -0.4
    assert flag.linked_event is not None
    assert flag.linked_event.name == "Analytics tag deploy"
    assert flag.linked_event.date == OUTAGE
    assert flag.linked_event.scope_overlap == "same_segment"


def test_incident_link_does_not_claim_causation(report):
    flag = report.flags_for(OUTAGE, "device=desktop")[0]
    text = (flag.reason + " " + flag.linked_event.note).lower()
    assert "possible tracking issue" in text
    assert "not proof" in text
    assert "caused by" not in text and "because of" not in text


def test_only_the_outage_day_is_flagged_on_reference_data(report):
    assert {f.date for f in report.flags} == {OUTAGE}
    assert report.missing_dates == []
    assert report.excluded_dates == [OUTAGE]


def test_planted_cause_is_not_flagged_as_data_quality(report):
    # A persistent behavioural change in mobile Safari must not look like a tracking blip.
    assert not [f for f in report.flags if f.date >= date(2026, 10, 13)]


def test_segments_outside_incident_scope_are_not_linked(report):
    for f in report.flags_for(OUTAGE):
        if f.segment == "device=mobile":  # disjoint from the incident's device=desktop scope
            assert f.linked_event is None


@pytest.mark.parametrize("seed", [1, 2, 3, 7])
def test_other_seeds_flag_outage_without_false_alarms(seed):
    metrics, events, _ = generate(seed)
    rep = check_data_quality(metrics, events)
    assert rep.flags_for(OUTAGE, "device=desktop")
    assert {f.date for f in rep.flags} == {OUTAGE}


def test_no_events_means_no_links(reference):
    metrics, _ = reference
    rep = check_data_quality(metrics)
    assert rep.flags_for(OUTAGE, "device=desktop")
    assert all(f.linked_event is None for f in rep.flags)


def test_non_incident_events_are_not_linked(reference):
    metrics, events = reference
    events = events.assign(type=events["type"].replace({"incident": "release"}))
    rep = check_data_quality(metrics, events)
    assert all(f.linked_event is None for f in rep.flags)


def test_incident_tolerance_controls_linking(reference):
    metrics, events = reference
    events = events.assign(date=events["date"].replace({"2026-09-20": "2026-09-21"}))
    strict = check_data_quality(metrics, events)
    assert strict.flags_for(OUTAGE, "device=desktop")[0].linked_event is None
    lenient = check_data_quality(metrics, events, DetectorParams(incident_tolerance_days=1))
    assert lenient.flags_for(OUTAGE, "device=desktop")[0].linked_event is not None


# ------------------------------------------------------------------ missing dates / edge cases


def test_missing_date_reported(reference):
    metrics, events = reference
    gap = metrics[metrics["date"] != "2026-09-05"]
    rep = check_data_quality(gap, events)
    assert rep.missing_dates == [date(2026, 9, 5)]
    flags = [f for f in rep.flags if f.kind == "missing_date"]
    assert [f.date for f in flags] == [date(2026, 9, 5)]
    assert flags[0].segment == "all"
    assert date(2026, 9, 5) not in rep.excluded_dates  # nothing to exclude


def _flat(days: int = 40, sessions: int = 10_000, orders: int = 300, **overrides) -> pd.DataFrame:
    dates = pd.date_range("2026-08-01", periods=days).strftime("%Y-%m-%d")
    df = pd.DataFrame(
        {
            "date": dates,
            "device": "desktop",
            "browser": "chrome",
            "channel": "organic",
            "customer_type": "new",
            "sessions": sessions,
            "orders": orders,
        }
    )
    for key, (idx, value) in overrides.items():
        df.loc[idx, key] = value
    return df


def test_constant_series_with_drop_flagged_despite_zero_mad():
    df = _flat(orders=300)
    df.loc[20, "orders"] = 100
    rep = check_data_quality(df)
    assert [f.date for f in rep.flags_for("2026-08-21") if f.segment == "device=desktop"]


def test_small_deviation_not_flagged():
    df = _flat()
    df.loc[20, "orders"] = 290  # -3.3%, below min_relative_deviation
    assert check_data_quality(df).flags == []


def test_abnormal_sessions_not_classed_as_tracking_issue():
    df = _flat()
    df.loc[20, ["sessions", "orders"]] = [1_000, 15]  # traffic collapses and conversion halves
    flag = check_data_quality(df).flags_for("2026-08-21", "device=desktop")[0]
    assert flag.kind == "conversion_shift_with_abnormal_sessions"
    assert flag.sessions_normal is False
    assert "traffic changed" in flag.reason


def test_zero_session_day_has_no_rate_and_is_not_flagged():
    df = _flat()
    df.loc[20, ["sessions", "orders"]] = [0, 0]
    assert check_data_quality(df).flags == []


def test_zero_baseline_rate_is_skipped():
    df = _flat(orders=0)
    df.loc[20, "orders"] = 50
    assert check_data_quality(df).flags == []


def test_short_history_is_not_evaluated():
    df = _flat(days=6)
    df.loc[3, "orders"] = 10
    assert check_data_quality(df).flags == []


def test_row_order_does_not_change_report(reference):
    metrics, events = reference
    shuffled = metrics.sample(frac=1.0, random_state=0).reset_index(drop=True)
    a = check_data_quality(metrics, events)
    b = check_data_quality(shuffled, events)
    assert a.model_dump() == b.model_dump()


# ------------------------------------------------------------------ default exclusions


def test_default_exclusion_drops_flagged_dates_and_says_so(reference, report):
    metrics, _ = reference
    kept, summary = apply_default_exclusions(metrics, report)
    assert summary.granularity == "date"
    assert summary.excluded_dates == [OUTAGE]
    assert summary.rows_removed == int((metrics["date"] == "2026-09-20").sum())
    assert len(kept) == len(metrics) - summary.rows_removed
    assert "2026-09-20" not in set(kept["date"].dt.strftime("%Y-%m-%d"))
    assert "excluded" in summary.statement and "2026-09-20" in summary.statement
    assert "excluded" in report.exclusion_statement


def test_segment_day_exclusion_removes_only_flagged_segment_rows(reference, report):
    metrics, _ = reference
    kept, summary = apply_default_exclusions(metrics, report, granularity="segment_day")
    assert summary.granularity == "segment_day"
    day = pd.Timestamp("2026-09-20")
    removed = len(metrics) - len(kept)
    assert removed == summary.rows_removed
    assert not ((kept["date"] == day) & (kept["device"] == "desktop")).any()
    # Rows on other dates are untouched.
    other = metrics[metrics["date"] != "2026-09-20"]
    assert len(kept[kept["date"] != day]) == len(other)
    # With only device=desktop flagged, tablet/mobile rows would survive; here the outage
    # also trips every channel / customer_type segment, so whether any rows survive depends
    # on the flags, not on the granularity argument.
    flagged = {f.segment for f in report.flags_for("2026-09-20")}
    assert "device=desktop" in flagged


def test_segment_day_exclusion_keeps_unflagged_segments():
    df = _flat()
    df.loc[20, "orders"] = 100
    other = _flat().assign(device="mobile", browser="safari", channel="paid", customer_type="returning")
    df = pd.concat([df, other], ignore_index=True)
    rep = check_data_quality(df)
    kept, _ = apply_default_exclusions(df, rep, granularity="segment_day")
    day = kept[kept["date"] == "2026-08-21"]
    assert list(day["device"]) == ["mobile"]


def test_no_flags_means_nothing_excluded():
    df = _flat()
    rep = check_data_quality(df)
    kept, summary = apply_default_exclusions(df, rep)
    assert len(kept) == len(df) and summary.rows_removed == 0
    assert "no days are excluded" in summary.statement.lower()
