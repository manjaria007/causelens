"""Typed loaders and validation for metrics and events (Requirement 1.1).

Invalid input raises :class:`DataValidationError` naming the offending column or
row. A partial result is never returned. Validation is positional, so frames with
repeated or non-default index labels are handled; the labels are only used to tell
the user where a problem is.
"""
from __future__ import annotations

import csv
from pathlib import Path

import numpy as np
import pandas as pd

DIMENSIONS: tuple[str, ...] = ("device", "browser", "channel", "customer_type")
METRIC_COLUMNS: tuple[str, ...] = ("date", *DIMENSIONS, "sessions", "orders")
EVENT_TYPES: tuple[str, ...] = ("release", "campaign", "incident")
EVENT_COLUMNS: tuple[str, ...] = ("date", "type", "name", "scope", "description")
_REQUIRED_EVENT_COLUMNS: tuple[str, ...] = ("date", "type", "name", "scope")

#: Largest accepted session/order count. 2**53 - 1 is the largest integer a float64
#: represents exactly, so counts are never silently rounded, and it is far below int64.
MAX_COUNT: int = 2**53 - 1

_MAX_REPORTED = 10


class DataValidationError(ValueError):
    """Input data failed validation. ``problems`` lists the issues found (capped)."""

    def __init__(self, source: str, problems: list[str], total: int | None = None):
        self.source = source
        self.problems = problems
        total = len(problems) if total is None else total
        shown = problems[:_MAX_REPORTED]
        more = f" (+{total - len(shown)} more)" if total > len(shown) else ""
        super().__init__(f"{source}: " + "; ".join(shown) + more)


class _Rows:
    """Maps row positions to user-facing labels (CSV line numbers or frame index labels)."""

    def __init__(self, index: pd.Index, from_file: bool):
        self._labels = list(index)
        self._prefix = "row" if from_file else "row index"

    def label(self, pos: int) -> str:
        return f"{self._prefix} {self._labels[pos]}"


def _positions(mask: object) -> list[int]:
    return [int(p) for p in np.flatnonzero(np.asarray(mask, dtype=bool))]


def _fail(source: str, problems: list[str]) -> None:
    if problems:
        raise DataValidationError(source, problems[:_MAX_REPORTED], total=len(problems))


def _parse_dates(series: pd.Series, rows: _Rows, problems: list[str]) -> pd.Series:
    text = series.astype("string").str.strip()
    parsed = pd.to_datetime(text, format="%Y-%m-%d", errors="coerce")
    raw = series.to_numpy(dtype=object)
    for pos in _positions(parsed.isna()):
        problems.append(
            f"{rows.label(pos)}: column 'date' value {raw[pos]!r} is not a valid YYYY-MM-DD date"
        )
    return parsed


def _to_float(series: pd.Series) -> np.ndarray:
    """Best-effort float64 view of a count column; unparseable or huge values become NaN/inf."""
    try:
        return pd.to_numeric(series, errors="coerce").astype("float64").to_numpy()
    except (OverflowError, ValueError, TypeError):
        out = np.full(len(series), np.nan)
        for i, value in enumerate(series.to_numpy(dtype=object)):
            try:
                out[i] = float(value)
            except (OverflowError, ValueError, TypeError):
                out[i] = np.nan
        return out


def _check_counts(col: str, series: pd.Series, rows: _Rows, problems: list[str]) -> np.ndarray:
    """Validate a count column and return it as float64 (converted to int64 by the caller)."""
    numeric = _to_float(series)
    raw = series.to_numpy(dtype=object)
    with np.errstate(invalid="ignore"):
        not_whole = np.isnan(numeric) | ~np.isfinite(numeric) | (np.mod(numeric, 1) != 0)
        negative = ~not_whole & (numeric < 0)
        # At or above 2**53 a float64 may already have been rounded: reject, never convert.
        too_big = ~not_whole & (numeric >= 2**53)
    for pos in _positions(not_whole):
        problems.append(
            f"{rows.label(pos)}: column '{col}' value {raw[pos]!r} is not a whole number"
        )
    for pos in _positions(negative):
        problems.append(f"{rows.label(pos)}: column '{col}' is negative ({raw[pos]})")
    for pos in _positions(too_big):
        problems.append(
            f"{rows.label(pos)}: column '{col}' value {raw[pos]!r} exceeds the maximum "
            f"supported count ({MAX_COUNT})"
        )
    return numeric


def validate_metrics(
    df: pd.DataFrame, source: str = "metrics", *, _from_file: bool = False
) -> pd.DataFrame:
    """Validate a metrics frame and return a clean, typed copy sorted by date and segment.

    Rejects: missing required columns; unparseable dates; blank dimension values;
    non-integer, missing, negative or out-of-range (> ``MAX_COUNT``) counts;
    ``orders > sessions``; duplicate (date, segment) rows; no rows. Extra columns are
    dropped. The input frame is not modified and its index labels may repeat.
    """
    missing = [c for c in METRIC_COLUMNS if c not in df.columns]
    _fail(source, [f"missing required column(s): {', '.join(missing)}"] if missing else [])
    if df.empty:
        raise DataValidationError(source, ["no rows"])

    rows = _Rows(df.index, _from_file)
    work = df.loc[:, list(METRIC_COLUMNS)].reset_index(drop=True)
    problems: list[str] = []

    work["date"] = _parse_dates(work["date"], rows, problems)

    for col in DIMENSIONS:
        text = work[col].astype("string").str.strip()
        for pos in _positions(text.isna() | (text == "")):
            problems.append(f"{rows.label(pos)}: column '{col}' is empty")
        work[col] = text

    counts = {col: _check_counts(col, work[col], rows, problems) for col in ("sessions", "orders")}
    _fail(source, problems)

    sessions = counts["sessions"].astype("int64")
    orders = counts["orders"].astype("int64")
    work["sessions"], work["orders"] = sessions, orders

    for pos in _positions(orders > sessions):
        problems.append(
            f"{rows.label(pos)}: orders ({orders[pos]}) exceed sessions ({sessions[pos]})"
        )
    for pos in _positions(work.duplicated(subset=["date", *DIMENSIONS], keep="first")):
        problems.append(
            f"{rows.label(pos)}: duplicate (date, segment) row for {work['date'].iloc[pos].date()} "
            + "/".join(str(work[c].iloc[pos]) for c in DIMENSIONS)
        )
    _fail(source, problems)

    return work.sort_values(["date", *DIMENSIONS], kind="mergesort").reset_index(drop=True)


def parse_scope(scope: str) -> dict[str, str]:
    """Parse an event scope: ``all`` -> ``{}``; ``a=x,b=y`` -> ``{"a": "x", "b": "y"}``.

    Raises ``ValueError`` on unknown dimensions, malformed terms or repeated dimensions.
    """
    text = str(scope).strip()
    if text == "all":
        return {}
    if not text:
        raise ValueError("scope is empty (use 'all' or 'dim=value[,dim=value]')")
    out: dict[str, str] = {}
    for term in text.split(","):
        dim, sep, value = term.partition("=")
        dim, value = dim.strip(), value.strip()
        if not sep or not dim or not value:
            raise ValueError(f"malformed scope term {term!r} (expected dim=value)")
        if dim not in DIMENSIONS:
            raise ValueError(f"unknown dimension {dim!r} (expected one of {', '.join(DIMENSIONS)})")
        if dim in out:
            raise ValueError(f"dimension {dim!r} repeated in scope")
        out[dim] = value
    return out


def validate_events(
    df: pd.DataFrame, source: str = "events", *, _from_file: bool = False
) -> pd.DataFrame:
    """Validate an event log and return a typed copy sorted by date.

    ``description`` is optional (filled with an empty string). Event type must be
    ``release``, ``campaign`` or ``incident``; scope must parse via :func:`parse_scope`.
    Repeated index labels are allowed.
    """
    missing = [c for c in _REQUIRED_EVENT_COLUMNS if c not in df.columns]
    _fail(source, [f"missing required column(s): {', '.join(missing)}"] if missing else [])

    rows = _Rows(df.index, _from_file)
    work = df.reset_index(drop=True)
    if "description" not in work.columns:
        work["description"] = ""
    work = work.loc[:, list(EVENT_COLUMNS)].copy()
    problems: list[str] = []

    work["date"] = _parse_dates(work["date"], rows, problems)
    for col in ("type", "name", "scope", "description"):
        work[col] = work[col].astype("string").str.strip().fillna("")

    types, names, scopes = work["type"].tolist(), work["name"].tolist(), work["scope"].tolist()
    for pos in range(len(work)):
        label = rows.label(pos)
        if types[pos] not in EVENT_TYPES:
            problems.append(
                f"{label}: column 'type' value {types[pos]!r} is not one of " + ", ".join(EVENT_TYPES)
            )
        if not names[pos]:
            problems.append(f"{label}: column 'name' is empty")
        try:
            parse_scope(scopes[pos])
        except ValueError as exc:
            problems.append(f"{label}: column 'scope': {exc}")
    _fail(source, problems)

    return work.sort_values(["date", "name"], kind="mergesort").reset_index(drop=True)


def _read_csv_strict(path: Path) -> pd.DataFrame:
    """Read a CSV as strings, rejecting any record whose field count differs from the header.

    ``pandas.read_csv`` treats an extra leading field as an index and silently drops or
    pads fields, so records are checked here. The frame's index holds CSV line numbers.
    Blank lines are skipped.
    """
    source = str(path)
    try:
        with path.open(newline="", encoding="utf-8-sig") as handle:
            reader = csv.reader(handle)
            header = next(reader, None)
            if header is None:
                raise DataValidationError(source, ["file is empty"])
            header = [h.strip() for h in header]
            dupes = sorted({h for h in header if header.count(h) > 1})
            if dupes:
                raise DataValidationError(source, [f"duplicate header column(s): {', '.join(dupes)}"])
            records: list[list[str]] = []
            lines: list[int] = []
            problems: list[str] = []
            while True:
                previous = reader.line_num
                try:
                    record = next(reader)
                except StopIteration:
                    break
                if not record:
                    continue
                line = previous + 1
                if len(record) != len(header):
                    problems.append(
                        f"row {line}: has {len(record)} field(s), expected {len(header)} "
                        f"to match the header"
                    )
                    continue
                records.append(record)
                lines.append(line)
            _fail(source, problems)
    except FileNotFoundError:
        raise DataValidationError(source, ["file not found"]) from None
    except (IsADirectoryError, PermissionError) as exc:
        raise DataValidationError(source, [f"cannot read file: {exc.strerror or exc}"]) from None
    except UnicodeDecodeError as exc:
        raise DataValidationError(source, [f"file is not valid UTF-8 ({exc.reason})"]) from None
    except csv.Error as exc:
        raise DataValidationError(source, [f"cannot parse CSV: {exc}"]) from None
    return pd.DataFrame(records, columns=header, index=pd.Index(lines), dtype="object")


def load_metrics(path: str | Path) -> pd.DataFrame:
    """Load and validate a metrics CSV (see ``data/README.md`` for the schema)."""
    path = Path(path)
    return validate_metrics(_read_csv_strict(path), source=str(path), _from_file=True)


def load_events(path: str | Path) -> pd.DataFrame:
    """Load and validate an events CSV (see ``data/README.md`` for the schema)."""
    path = Path(path)
    return validate_events(_read_csv_strict(path), source=str(path), _from_file=True)
