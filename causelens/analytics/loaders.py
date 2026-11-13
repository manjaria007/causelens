"""Typed loaders and validation for metrics and events (Requirement 1.1).

Invalid input raises :class:`DataValidationError` naming the offending column or
row. A partial result is never returned.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

DIMENSIONS: tuple[str, ...] = ("device", "browser", "channel", "customer_type")
METRIC_COLUMNS: tuple[str, ...] = ("date", *DIMENSIONS, "sessions", "orders")
EVENT_TYPES: tuple[str, ...] = ("release", "campaign", "incident")
EVENT_COLUMNS: tuple[str, ...] = ("date", "type", "name", "scope", "description")
_REQUIRED_EVENT_COLUMNS: tuple[str, ...] = ("date", "type", "name", "scope")

_MAX_REPORTED = 10


class DataValidationError(ValueError):
    """Input data failed validation. ``problems`` lists every issue found (capped)."""

    def __init__(self, source: str, problems: list[str], total: int | None = None):
        self.source = source
        self.problems = problems
        total = len(problems) if total is None else total
        shown = problems[:_MAX_REPORTED]
        more = f" (+{total - len(shown)} more)" if total > len(shown) else ""
        super().__init__(f"{source}: " + "; ".join(shown) + more)


def _row_label(index_value: object, from_file: bool) -> str:
    """Spreadsheet-style line number for files (header is line 1), index for frames."""
    if from_file and isinstance(index_value, int):
        return f"row {index_value + 2}"
    return f"row index {index_value}"


def _fail(source: str, problems: list[str]) -> None:
    if problems:
        raise DataValidationError(source, problems)


def _parse_dates(series: pd.Series, source: str, from_file: bool, problems: list[str]) -> pd.Series:
    text = series.astype("string").str.strip()
    parsed = pd.to_datetime(text, format="%Y-%m-%d", errors="coerce")
    bad = parsed.isna()
    for idx in series.index[bad][:_MAX_REPORTED]:
        problems.append(
            f"{_row_label(idx, from_file)}: column 'date' value {series[idx]!r} "
            "is not a valid YYYY-MM-DD date"
        )
    return parsed


def validate_metrics(df: pd.DataFrame, source: str = "metrics", *, _from_file: bool = False) -> pd.DataFrame:
    """Validate a metrics frame and return a clean, typed copy sorted by date and segment.

    Rejects: missing required columns; unparseable dates; blank dimension values;
    non-integer, missing or negative counts; ``orders > sessions``; duplicate
    (date, segment) rows. Extra columns are dropped.
    """
    missing = [c for c in METRIC_COLUMNS if c not in df.columns]
    _fail(source, [f"missing required column(s): {', '.join(missing)}"] if missing else [])
    if df.empty:
        raise DataValidationError(source, ["no rows"])

    work = df.loc[:, list(METRIC_COLUMNS)].copy()
    problems: list[str] = []

    work["date"] = _parse_dates(work["date"], source, _from_file, problems)

    for col in DIMENSIONS:
        text = work[col].astype("string").str.strip()
        blank = text.isna() | (text == "")
        for idx in work.index[blank][:_MAX_REPORTED]:
            problems.append(f"{_row_label(idx, _from_file)}: column '{col}' is empty")
        work[col] = text

    for col in ("sessions", "orders"):
        numeric = pd.to_numeric(work[col], errors="coerce")
        bad = numeric.isna() | (numeric % 1 != 0)
        for idx in work.index[bad][:_MAX_REPORTED]:
            problems.append(
                f"{_row_label(idx, _from_file)}: column '{col}' value {work[col][idx]!r} "
                "is not a whole number"
            )
        neg = ~bad & (numeric < 0)
        for idx in work.index[neg][:_MAX_REPORTED]:
            problems.append(
                f"{_row_label(idx, _from_file)}: column '{col}' is negative ({numeric[idx]:g})"
            )
        work[col] = numeric
    _fail(source, problems)

    work["sessions"] = work["sessions"].astype("int64")
    work["orders"] = work["orders"].astype("int64")

    over = work["orders"] > work["sessions"]
    for idx in work.index[over][:_MAX_REPORTED]:
        problems.append(
            f"{_row_label(idx, _from_file)}: orders ({work['orders'][idx]}) exceed "
            f"sessions ({work['sessions'][idx]})"
        )
    dup = work.duplicated(subset=["date", *DIMENSIONS], keep="first")
    for idx in work.index[dup][:_MAX_REPORTED]:
        problems.append(
            f"{_row_label(idx, _from_file)}: duplicate (date, segment) row for "
            f"{work['date'][idx].date()} "
            + "/".join(str(work[c][idx]) for c in DIMENSIONS)
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


def validate_events(df: pd.DataFrame, source: str = "events", *, _from_file: bool = False) -> pd.DataFrame:
    """Validate an event log and return a typed copy sorted by date.

    ``description`` is optional (filled with an empty string). Event type must be
    ``release``, ``campaign`` or ``incident``; scope must parse via :func:`parse_scope`.
    """
    missing = [c for c in _REQUIRED_EVENT_COLUMNS if c not in df.columns]
    _fail(source, [f"missing required column(s): {', '.join(missing)}"] if missing else [])

    work = df.copy()
    if "description" not in work.columns:
        work["description"] = ""
    work = work.loc[:, list(EVENT_COLUMNS)]
    problems: list[str] = []

    work["date"] = _parse_dates(work["date"], source, _from_file, problems)
    for col in ("type", "name", "scope", "description"):
        work[col] = work[col].astype("string").str.strip().fillna("")

    for idx in work.index:
        label = _row_label(idx, _from_file)
        if work["type"][idx] not in EVENT_TYPES:
            problems.append(
                f"{label}: column 'type' value {work['type'][idx]!r} is not one of "
                + ", ".join(EVENT_TYPES)
            )
        if not work["name"][idx]:
            problems.append(f"{label}: column 'name' is empty")
        try:
            parse_scope(work["scope"][idx])
        except ValueError as exc:
            problems.append(f"{label}: column 'scope': {exc}")
    if problems:
        raise DataValidationError(source, problems[:_MAX_REPORTED], total=len(problems))

    return work.sort_values(["date", "name"], kind="mergesort").reset_index(drop=True)


def load_metrics(path: str | Path) -> pd.DataFrame:
    """Load and validate a metrics CSV (see ``data/README.md`` for the schema)."""
    path = Path(path)
    try:
        raw = pd.read_csv(path, dtype=str, keep_default_na=False)
    except FileNotFoundError:
        raise DataValidationError(str(path), ["file not found"]) from None
    except (pd.errors.ParserError, pd.errors.EmptyDataError) as exc:
        raise DataValidationError(str(path), [f"cannot parse CSV: {exc}"]) from None
    return validate_metrics(raw, source=str(path), _from_file=True)


def load_events(path: str | Path) -> pd.DataFrame:
    """Load and validate an events CSV (see ``data/README.md`` for the schema)."""
    path = Path(path)
    try:
        raw = pd.read_csv(path, dtype=str, keep_default_na=False)
    except FileNotFoundError:
        raise DataValidationError(str(path), ["file not found"]) from None
    except (pd.errors.ParserError, pd.errors.EmptyDataError) as exc:
        raise DataValidationError(str(path), [f"cannot parse CSV: {exc}"]) from None
    return validate_events(raw, source=str(path), _from_file=True)
