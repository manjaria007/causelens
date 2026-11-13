from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from causelens.analytics import (
    DataValidationError,
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


def test_zero_session_day_has_no_rate_and_is_reported_as_volume_anomaly_only():
    df = _flat()
    df.loc[20, ["sessions", "orders"]] = [0, 0]
    flags = check_data_quality(df).flags_for("2026-08-21", "device=desktop")
    assert [f.kind for f in flags] == ["volume_anomaly"]
    assert flags[0].observed_rate is None  # no conversion rate on a zero-session day


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


# ------------------------------------------------------------------ review regressions
# Finding 5: incident overlap must reflect a real shared population.


def _with_incident_scope(events: pd.DataFrame, scope: str) -> pd.DataFrame:
    out = events.copy()
    out.loc[out["name"] == "Analytics tag deploy", "scope"] = scope
    return out


def test_incident_is_not_linked_when_scope_and_segment_never_co_occur(reference):
    """Previously a mobile-only incident linked to browser=edge, but no mobile + edge traffic exists."""
    metrics, events = reference
    assert metrics[(metrics["device"] == "mobile") & (metrics["browser"] == "edge")].empty
    rep = check_data_quality(metrics, _with_incident_scope(events, "device=mobile"))
    edge = rep.flags_for(OUTAGE, "browser=edge")
    assert edge, "the outage still trips browser=edge"
    assert edge[0].linked_event is None


def test_incident_links_to_real_partial_overlap_and_reports_its_size(reference):
    metrics, events = reference
    rep = check_data_quality(metrics, _with_incident_scope(events, "device=mobile"))
    chrome = rep.flags_for(OUTAGE, "browser=chrome")[0].linked_event
    assert chrome is not None and chrome.scope_overlap == "overlapping_scope"
    day = metrics[metrics["date"] == "2026-09-20"]
    in_chrome = day[day["browser"] == "chrome"]
    expected = int(in_chrome.loc[in_chrome["device"] == "mobile", "sessions"].sum())
    assert chrome.overlap_sessions == expected > 0
    assert chrome.segment_share_in_scope == pytest.approx(expected / in_chrome["sessions"].sum())
    assert 0 < chrome.segment_share_in_scope < 1


def test_incident_scope_all_and_same_segment_report_full_overlap(reference):
    metrics, events = reference
    same = check_data_quality(metrics, events).flags_for(OUTAGE, "device=desktop")[0].linked_event
    assert same.scope_overlap == "same_segment" and same.segment_share_in_scope == pytest.approx(1.0)
    everyone = check_data_quality(metrics, _with_incident_scope(events, "all"))
    link = everyone.flags_for(OUTAGE, "device=desktop")[0].linked_event
    assert link.scope_overlap == "all_visitors" and link.segment_share_in_scope == pytest.approx(1.0)


# Finding 6: parameter relationships and zero scales.


def test_window_too_small_for_min_baseline_is_rejected():
    """Previously window_days=7 with min_baseline_days=7 silently disabled all detection."""
    with pytest.raises(ValueError, match="no baseline could ever form"):
        DetectorParams(window_days=7)
    ok = DetectorParams(window_days=7, min_baseline_days=6)
    df = _flat()
    df.loc[20, "orders"] = 100
    assert check_data_quality(df, params=ok).flags_for("2026-08-21", "device=desktop")


def test_even_window_is_rejected():
    with pytest.raises(ValueError, match="window_days must be odd"):
        DetectorParams(window_days=14)


def test_zero_scale_settings_do_not_flag_constant_data():
    """Previously 0/0 gave NaN scores and every date was flagged."""
    params = DetectorParams(rel_scale_floor=0, sampling_noise_floor=False, min_relative_deviation=0)
    rep = check_data_quality(_flat(), params=params)
    assert rep.flags == []


def test_zero_scale_flags_any_real_deviation_with_undefined_z():
    params = DetectorParams(rel_scale_floor=0, sampling_noise_floor=False, min_relative_deviation=0)
    df = _flat()
    df.loc[20, "orders"] = 299
    rep = check_data_quality(df, params=params)
    assert {f.date for f in rep.flags} == {date(2026, 8, 21)}
    flag = rep.flags_for("2026-08-21", "device=desktop")[0]
    assert flag.robust_z is None and "zero spread" in flag.reason
    rep.model_dump_json()  # no NaN/inf values in the report


# Finding 7: volume-only anomalies.


@pytest.mark.parametrize(("sessions", "orders"), [(1_000, 30), (0, 0), (25_000, 750)])
def test_volume_only_anomaly_is_reported(sessions, orders):
    """Conversion stays at 3%; only the volume changes."""
    df = _flat()
    df.loc[20, ["sessions", "orders"]] = [sessions, orders]
    rep = check_data_quality(df)
    flags = rep.flags_for("2026-08-21", "device=desktop")
    assert [f.kind for f in flags] == ["volume_anomaly"]
    assert flags[0].sessions_normal is False
    assert "cause not established" in flags[0].reason
    assert date(2026, 8, 21) in rep.excluded_dates  # excluded by default like other flags


def test_normal_weekday_sized_traffic_swing_is_not_a_volume_anomaly():
    df = _flat()
    df.loc[20, ["sessions", "orders"]] = [11_500, 345]  # +15%, same conversion
    assert check_data_quality(df).flags == []


def test_conversion_and_volume_change_together_keep_the_combined_kind():
    df = _flat()
    df.loc[20, ["sessions", "orders"]] = [1_000, 15]
    flags = check_data_quality(df).flags_for("2026-08-21", "device=desktop")
    assert [f.kind for f in flags] == ["conversion_shift_with_abnormal_sessions"]


def test_reference_data_has_no_volume_anomalies(report):
    assert not [f for f in report.flags if f.kind == "volume_anomaly"]


# Persistent changes near the end of the data.


def _persistent_drop(post_days: int) -> pd.DataFrame:
    df = _flat(days=40)
    df.loc[40 - post_days :, "orders"] = 200  # 3.0% -> 2.0%, and it stays there
    return df


def test_persistent_drop_in_final_days_is_flagged_but_marked_truncated():
    """Known limitation: with < 7 post-change days the baseline cannot see the change persist."""
    rep = check_data_quality(_persistent_drop(3))
    flags = [f for f in rep.flags if f.segment == "device=desktop"]
    assert [f.date for f in flags] == [date(2026, 9, 7), date(2026, 9, 8), date(2026, 9, 9)]
    assert all(f.baseline_truncated for f in flags)
    assert all("baseline is truncated" in f.reason for f in flags)
    assert rep.excluded_dates == rep.truncated_baseline_dates == [f.date for f in flags]
    assert "Caution" in rep.exclusion_statement and "persistent behaviour change" in rep.exclusion_statement


def test_exclusion_summary_repeats_the_truncation_caution():
    df = _persistent_drop(3)
    _, summary = apply_default_exclusions(df, check_data_quality(df))
    assert summary.truncated_baseline_dates == summary.excluded_dates
    assert "truncated baseline" in summary.statement


def test_persistent_change_with_enough_post_days_is_not_flagged():
    assert check_data_quality(_persistent_drop(8)).flags == []  # step with 8 days after it
    mid = _flat(days=40)
    mid.loc[20:, "orders"] = 200
    assert check_data_quality(mid).flags == []


def test_outage_with_full_baseline_is_not_marked_truncated(report):
    flag = report.flags_for(OUTAGE, "device=desktop")[0]
    assert (flag.baseline_days_before, flag.baseline_days_after) == (7, 7)
    assert not flag.baseline_truncated
    assert report.truncated_baseline_dates == []


def test_invalid_input_still_raises_typed_error():
    with pytest.raises(DataValidationError):
        check_data_quality(_flat().assign(orders=-1))


# ------------------------------------------------------------------ final review corrections
# Historical overlap must not be presented as same-day overlap.


def _mobile_used_edge_only_early(metrics: pd.DataFrame) -> pd.DataFrame:
    """Add mobile + Edge traffic before 2026-09-10 only (mobile later uses Chrome)."""
    early = metrics[
        (metrics["device"] == "mobile") & (metrics["browser"] == "chrome") & (metrics["date"] < "2026-09-10")
    ].assign(browser="edge")
    return pd.concat([metrics, early], ignore_index=True)


def test_historical_only_overlap_is_distinguished_from_observed_overlap(reference):
    """Mobile used Edge earlier, but on the flagged day no mobile + Edge sessions exist."""
    metrics, events = reference
    rep = check_data_quality(_mobile_used_edge_only_early(metrics), _with_incident_scope(events, "device=mobile"))

    edge = rep.flags_for(OUTAGE, "browser=edge")[0].linked_event
    assert edge is not None
    assert edge.overlap_basis == "historical_only"
    assert edge.overlap_sessions == 0
    assert edge.segment_share_in_scope == 0.0
    assert "Possible overlap only" in edge.note
    assert "inferred from history, not observed" in edge.note
    assert "outside the incident's scope that day" in edge.note
    assert "not proof" in edge.note

    chrome = rep.flags_for(OUTAGE, "browser=chrome")[0].linked_event
    assert chrome.overlap_basis == "observed_on_flagged_day" and chrome.overlap_sessions > 0
    assert "Possible overlap only" not in chrome.note
    assert "coincidence, not proof" in chrome.note


def test_observed_overlap_is_the_basis_for_ordinary_links(report):
    link = report.flags_for(OUTAGE, "device=desktop")[0].linked_event
    assert link.overlap_basis == "observed_on_flagged_day"
    assert link.overlap_sessions > 0 and link.segment_share_in_scope == pytest.approx(1.0)


def test_zero_volume_outage_can_link_on_history_with_hedged_wording():
    df = _flat()
    df.loc[20, ["sessions", "orders"]] = [0, 0]  # the segment vanishes entirely that day
    events = pd.DataFrame(
        {"date": ["2026-08-21"], "type": ["incident"], "name": ["Collector outage"], "scope": ["device=desktop"]}
    )
    flag = check_data_quality(df, events).flags_for("2026-08-21", "device=desktop")[0]
    assert flag.kind == "volume_anomaly"
    link = flag.linked_event
    assert link is not None and link.overlap_basis == "historical_only"
    assert link.overlap_sessions == 0 and link.segment_share_in_scope is None
    assert "zero-volume outage" in link.note and "not observed" in link.note
    assert "not proof" in link.note


def test_historical_link_still_requires_shared_traffic_somewhere(reference):
    metrics, events = reference  # no mobile + Edge traffic at all, ever
    rep = check_data_quality(metrics, _with_incident_scope(events, "device=mobile"))
    assert rep.flags_for(OUTAGE, "browser=edge")[0].linked_event is None


def test_link_models_serialise_the_basis(report):
    dumped = report.flags_for(OUTAGE, "device=desktop")[0].model_dump(mode="json")
    assert dumped["linked_event"]["overlap_basis"] == "observed_on_flagged_day"
