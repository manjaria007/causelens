from __future__ import annotations

import pandas as pd
import pytest

from causelens.analytics import (
    DataValidationError,
    load_events,
    load_metrics,
    parse_scope,
    validate_events,
    validate_metrics,
)

GOOD = pd.DataFrame(
    {
        "date": ["2026-08-02", "2026-08-01"],
        "device": ["mobile", "desktop"],
        "browser": ["safari", "chrome"],
        "channel": ["paid", "organic"],
        "customer_type": ["new", "returning"],
        "sessions": [100, 200],
        "orders": [3, 10],
    }
)


def _with(**changes) -> pd.DataFrame:
    df = GOOD.copy()
    for col, value in changes.items():
        df[col] = value
    return df


def test_valid_metrics_are_typed_and_sorted():
    out = validate_metrics(GOOD)
    assert str(out["date"].dtype).startswith("datetime64")
    assert out["sessions"].dtype == "int64" and out["orders"].dtype == "int64"
    assert list(out["date"].dt.strftime("%Y-%m-%d")) == ["2026-08-01", "2026-08-02"]


def test_input_frame_is_not_mutated():
    before = GOOD.copy()
    validate_metrics(GOOD)
    pd.testing.assert_frame_equal(GOOD, before)


def test_missing_column_named():
    with pytest.raises(DataValidationError, match="missing required column.*orders"):
        validate_metrics(GOOD.drop(columns="orders"))


def test_negative_count_rejected_with_row_and_column():
    with pytest.raises(DataValidationError, match=r"row index 1: column 'orders' is negative"):
        validate_metrics(_with(orders=[3, -1]))


def test_orders_above_sessions_rejected():
    with pytest.raises(DataValidationError, match=r"row index 0: orders \(300\) exceed sessions \(100\)"):
        validate_metrics(_with(orders=[300, 10]))


def test_non_integer_count_rejected():
    with pytest.raises(DataValidationError, match="'sessions' value .* is not a whole number"):
        validate_metrics(_with(sessions=["abc", "200"]))
    with pytest.raises(DataValidationError, match="not a whole number"):
        validate_metrics(_with(sessions=[100.5, 200]))


def test_bad_date_and_blank_dimension_rejected():
    with pytest.raises(DataValidationError, match="'date' value '2026-13-01'"):
        validate_metrics(_with(date=["2026-13-01", "2026-08-01"]))
    with pytest.raises(DataValidationError, match="column 'device' is empty"):
        validate_metrics(_with(device=["", "desktop"]))


def test_duplicate_segment_day_rejected():
    dup = pd.concat([GOOD, GOOD.iloc[[0]]], ignore_index=True)
    with pytest.raises(DataValidationError, match="duplicate"):
        validate_metrics(dup)


def test_empty_rejected():
    with pytest.raises(DataValidationError, match="no rows"):
        validate_metrics(GOOD.iloc[0:0])


def test_error_lists_problems_and_caps_output():
    bad = pd.concat([GOOD] * 1, ignore_index=True)
    bad = pd.concat([bad.iloc[[0]].assign(orders=-1, date=f"2026-08-{d:02d}") for d in range(1, 21)])
    with pytest.raises(DataValidationError) as exc:
        validate_metrics(bad.reset_index(drop=True))
    assert len(exc.value.problems) <= 10


def test_file_errors_use_file_line_numbers(tmp_path):
    path = tmp_path / "m.csv"
    path.write_text(
        "date,device,browser,channel,customer_type,sessions,orders\n"
        "2026-08-01,desktop,chrome,organic,new,10,1\n"
        "2026-08-02,desktop,chrome,organic,new,10,11\n"
    )
    with pytest.raises(DataValidationError, match="row 3: orders"):
        load_metrics(path)


def test_missing_file_is_typed_error(tmp_path):
    with pytest.raises(DataValidationError, match="file not found"):
        load_metrics(tmp_path / "nope.csv")
    with pytest.raises(DataValidationError, match="file not found"):
        load_events(tmp_path / "nope.csv")


def test_round_trip_reference_files(reference, tmp_path):
    metrics, events = reference
    mpath, epath = tmp_path / "m.csv", tmp_path / "e.csv"
    metrics.to_csv(mpath, index=False)
    events.to_csv(epath, index=False)
    loaded_m, loaded_e = load_metrics(mpath), load_events(epath)
    assert len(loaded_m) == len(metrics)
    assert loaded_m["orders"].sum() == metrics["orders"].sum()
    assert list(loaded_e["name"]) == [
        "Search ranking tweak v3.9",
        "Labor Day email blast",
        "Analytics tag deploy",
        "Homepage hero refresh",
        "Checkout v4.2",
    ]


def test_committed_events_file_loads():
    events = load_events("data/events.csv")
    assert set(events["type"]) == {"release", "campaign", "incident"}


@pytest.mark.parametrize(
    ("scope", "expected"),
    [
        ("all", {}),
        ("device=desktop", {"device": "desktop"}),
        ("device=mobile, browser=safari", {"device": "mobile", "browser": "safari"}),
    ],
)
def test_parse_scope(scope, expected):
    assert parse_scope(scope) == expected


@pytest.mark.parametrize("scope", ["", "device", "device=", "color=red", "device=a,device=b"])
def test_parse_scope_rejects(scope):
    with pytest.raises(ValueError):
        parse_scope(scope)


def test_event_validation_errors():
    ev = pd.DataFrame(
        {"date": ["2026-09-20"], "type": ["outage"], "name": ["x"], "scope": ["color=red"]}
    )
    with pytest.raises(DataValidationError) as exc:
        validate_events(ev)
    msg = str(exc.value)
    assert "'type' value 'outage'" in msg and "column 'scope'" in msg
    ok = validate_events(ev.assign(type="incident", scope="all"))
    assert ok["description"].iloc[0] == ""
