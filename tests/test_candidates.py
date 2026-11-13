from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import pytest
from frames import day, to_frame

from causelens.analytics import (
    DataValidationError,
    detect_change_point,
    match_event_candidates,
)


@pytest.fixture(scope="module")
def change(reference):
    metrics, events = reference
    return detect_change_point(metrics, events)


@pytest.fixture(scope="module")
def matched(change, reference):
    return match_event_candidates(change, reference[1])


def test_events_in_or_just_before_the_plausible_range_are_candidates(matched, change, ground_truth):
    names = {c.name for c in matched.candidates}
    planted = ground_truth["planted_cause"]["event"]
    decoy = ground_truth["decoys"][0]["event"]
    assert matched.status == "ok" and {planted, decoy} <= names
    # Unrelated events, months away, are not candidates.
    assert names.isdisjoint({"Search ranking tweak v3.9", "Labor Day email blast", "Analytics tag deploy"})
    assert matched.plausible_range == change.plausible_range and matched.n_events_considered == 5


def test_match_kind_follows_the_computed_dates_not_the_event_names(matched, change):
    lo, hi = change.plausible_range
    for c in matched.candidates:
        if c.date in change.plausible_dates:
            assert c.match == "in_plausible_set"
        elif lo <= c.date <= hi:
            assert c.match == "in_plausible_range"
        else:
            assert c.match == "precedes_range_within_lead" and 0 < (lo - c.date).days <= matched.lead_days
        assert c.days_from_best_date == (c.date - change.best_date).days


def test_candidates_are_ordered_in_set_first_then_by_closeness(matched):
    order = {"in_plausible_set": 0, "in_plausible_range": 1, "precedes_range_within_lead": 2}
    keys = [(order[c.match], abs(c.days_from_best_date)) for c in matched.candidates]
    assert keys == sorted(keys)


def test_candidates_are_date_coincidences_not_causes(matched):
    assert "not causes" in matched.statement
    for c in matched.candidates:
        assert "not evidence that it caused" in c.note and "still has to be tested" in c.note
        for banned in ("caused the", "is the cause", "because of"):
            assert banned not in c.note.lower().replace("not evidence that it caused", "")


def test_lead_days_controls_the_look_back_window(change, reference):
    events = reference[1]
    counts = [len(match_event_candidates(change, events, lead_days=n).candidates) for n in (0, 3, 30, 90)]
    assert counts == sorted(counts) and counts[0] < counts[1] < counts[-1]
    wide = {c.name for c in match_event_candidates(change, events, lead_days=90).candidates}
    assert {"Labor Day email blast", "Analytics tag deploy"} <= wide


def test_events_after_the_plausible_range_are_never_candidates(change, reference):
    events = pd.concat(
        [
            reference[1],
            pd.DataFrame(
                {
                    "date": [(change.plausible_range[1] + pd.Timedelta(days=2)).isoformat()],
                    "type": ["release"],
                    "name": ["Later release"],
                    "scope": ["all"],
                }
            ),
        ],
        ignore_index=True,
    )
    assert "Later release" not in {c.name for c in match_event_candidates(change, events, lead_days=90).candidates}


def test_no_candidate_is_reported_explicitly(change):
    far = pd.DataFrame({"date": ["2026-08-05"], "type": ["release"], "name": ["Early"], "scope": ["all"]})
    m = match_event_candidates(change, far)
    assert m.status == "ok" and m.candidates == [] and "no event-log candidate" in m.statement


def test_no_supported_change_means_no_matching(reference):
    metrics, events = reference
    flat = detect_change_point(metrics, events, search_window=("2026-08-01", "2026-10-10"))
    m = match_event_candidates(flat, events)
    assert m.status == "no_change_point" and m.candidates == [] and m.plausible_range is None
    assert "no change point is statistically supported" in m.statement


def test_insufficient_data_means_no_matching(reference):
    metrics, events = reference
    short = detect_change_point(metrics, events, search_window=("2026-09-14", "2026-09-26"))
    m = match_event_candidates(short, events)
    assert m.status == "no_change_point" and "insufficient data" in m.statement


def test_incident_candidate_carries_observed_context_and_the_exclusion(change, reference):
    """Point the matcher at the outage day to test incident handling in isolation."""
    events = reference[1]
    moved = change.model_copy(
        update={
            "plausible_dates": [date(2026, 9, 20)],
            "plausible_range": (date(2026, 9, 20), date(2026, 9, 20)),
            "best_date": date(2026, 9, 20),
        }
    )
    (cand,) = match_event_candidates(moved, events, lead_days=0).candidates
    assert cand.name == "Analytics tag deploy" and cand.type == "incident" and cand.match == "in_plausible_set"
    assert cand.date_excluded_as_flagged
    assert [(c.name, c.overlap_basis) for c in cand.incident_context] == [("Analytics tag deploy", "observed_on_flagged_day")]
    assert "data-collection problem must be ruled out" in cand.note
    assert "excluded from the analysis as a flagged day" in cand.note
    assert "coincides in time" in cand.note and "not proof" in cand.note


def test_historical_only_incident_context_is_marked_uncertain():
    df = to_frame(np.full(60, 1000), np.full(60, 30))
    df.loc[30, ["sessions", "orders"]] = 0
    events = pd.DataFrame(
        {"date": [day(30).isoformat()], "type": ["incident"], "name": ["Collector outage"], "scope": ["device=desktop"]}
    )
    base = detect_change_point(df, events, adjust_weekdays=False)
    forced = base.model_copy(
        update={
            "status": "ok",
            "change_detected": True,
            "best_date": day(30),
            "plausible_dates": [day(30)],
            "plausible_range": (day(30), day(30)),
        }
    )
    (cand,) = match_event_candidates(forced, events).candidates
    assert [c.overlap_basis for c in cand.incident_context] == [("historical_only")]
    assert "Uncertain context" in cand.note and "Do not treat it as an explanation" in cand.note


def test_invalid_inputs_raise(change, reference):
    with pytest.raises(ValueError):
        match_event_candidates(change, reference[1], lead_days=-1)
    with pytest.raises(DataValidationError):
        match_event_candidates(change, pd.DataFrame({"date": ["2026-10-01"], "type": ["bogus"], "name": ["x"], "scope": ["all"]}))


def test_candidate_result_serialises(matched):
    assert "NaN" not in matched.model_dump_json()
