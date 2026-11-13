"""Data-quality checks: missing dates, possible tracking issues, incident linkage.

Method (design.md, ``check_data_quality``): for every top-level segment (each value
of each single dimension, e.g. ``device=desktop``) build the daily conversion series
``orders / sessions`` and compare each day with a robust baseline made of the median
and MAD of the *other* days in a centred rolling window. A day is a possible tracking
issue when conversion is far outside that baseline while sessions are normal.

Parameters and edge-case conventions are documented in ``docs/data-quality.md``.
Everything here is pure and deterministic: no randomness, network or LLM calls.

Wording follows ``.kiro/steering/scientist-rules.md``: a flag is a *possible* tracking
issue, and an incident that coincides with it is a coincidence in time and scope, not
evidence of causation.
"""
from __future__ import annotations

from datetime import date as Date
from typing import Literal

import numpy as np
import pandas as pd
from pydantic import BaseModel, ConfigDict, Field

from causelens.analytics.loaders import (
    DIMENSIONS,
    parse_scope,
    validate_events,
    validate_metrics,
)

FlagKind = Literal[
    "possible_tracking_issue",
    "conversion_shift_with_abnormal_sessions",
    "missing_date",
]

#: Flag kinds whose days are excluded from downstream estimates by default (Req 1.5).
DEFAULT_EXCLUDED_KINDS: tuple[str, ...] = (
    "possible_tracking_issue",
    "conversion_shift_with_abnormal_sessions",
)

_MAD_TO_SIGMA = 1.4826  # makes MAD comparable to a standard deviation for normal data


class DetectorParams(BaseModel):
    """Detector settings. Defaults are documented in ``docs/data-quality.md``."""

    model_config = ConfigDict(frozen=True)

    window_days: int = Field(15, ge=3, description="Centred window length; the day itself is excluded.")
    min_baseline_days: int = Field(7, ge=3, description="Fewest usable baseline days to evaluate a day.")
    k: float = Field(5.0, gt=0, description="Conversion flagged when |x - median| > k * robust scale.")
    rel_scale_floor: float = Field(
        0.01, ge=0, description="Robust scale is at least this fraction of |median| (guards MAD = 0)."
    )
    sampling_noise_floor: bool = Field(
        True,
        description="Also floor the scale at sqrt(p(1-p)/baseline sessions), the binomial sampling "
        "noise of a day's rate, so small segments are not flagged for chance variation.",
    )
    min_relative_deviation: float = Field(
        0.10, ge=0, description="Also require |x - median| / median at least this large."
    )
    sessions_k: float = Field(5.0, gt=0, description="Sessions abnormal when beyond k * robust scale ...")
    sessions_min_relative_deviation: float = Field(
        0.25, ge=0, description="... and at least this relative distance from the median."
    )
    incident_tolerance_days: int = Field(
        0, ge=0, description="Incident links when |flag date - incident date| <= this."
    )


class LinkedEvent(BaseModel):
    """An incident that coincides with a flag in time and scope."""

    model_config = ConfigDict(frozen=True)

    date: Date
    name: str
    scope: str
    scope_overlap: Literal["all_visitors", "same_segment", "overlapping_scope"]
    note: str


class DataQualityFlag(BaseModel):
    model_config = ConfigDict(frozen=True)

    date: Date
    kind: FlagKind
    segment: str = Field(description="'dimension=value', or 'all' for missing dates.")
    reason: str
    observed_rate: float | None = None
    baseline_rate: float | None = None
    relative_deviation: float | None = Field(None, description="(observed - baseline) / baseline")
    robust_z: float | None = None
    sessions: int | None = None
    baseline_sessions: float | None = None
    sessions_normal: bool | None = None
    linked_event: LinkedEvent | None = None


class DataQualityReport(BaseModel):
    model_config = ConfigDict(frozen=True)

    params: DetectorParams
    n_rows: int
    date_range: tuple[Date, Date]
    n_days_checked: int
    missing_dates: list[Date]
    flags: list[DataQualityFlag]
    excluded_dates: list[Date] = Field(description="Dates dropped from downstream estimates by default.")
    exclusion_statement: str

    def flags_for(self, date: Date | str, segment: str | None = None) -> list[DataQualityFlag]:
        day = pd.Timestamp(date).date()
        return [f for f in self.flags if f.date == day and (segment is None or f.segment == segment)]


class ExclusionSummary(BaseModel):
    model_config = ConfigDict(frozen=True)

    granularity: Literal["date", "segment_day"]
    excluded_dates: list[Date]
    rows_before: int
    rows_removed: int
    statement: str


# --------------------------------------------------------------------------- helpers


def _baseline(values: np.ndarray, half: int, min_days: int) -> tuple[np.ndarray, np.ndarray]:
    """Per-day (median, MAD) of the other non-NaN values in ``[i-half, i+half]``.

    NaN where fewer than ``min_days`` usable neighbours exist.
    """
    n = len(values)
    med = np.full(n, np.nan)
    mad = np.full(n, np.nan)
    for i in range(n):
        lo, hi = max(0, i - half), min(n, i + half + 1)
        window = np.concatenate([values[lo:i], values[i + 1 : hi]])
        window = window[~np.isnan(window)]
        if len(window) >= min_days:
            m = float(np.median(window))
            med[i] = m
            mad[i] = float(np.median(np.abs(window - m)))
    return med, mad


def _scale(mad: float, median: float, floor: float) -> float:
    return max(_MAD_TO_SIGMA * mad, floor * abs(median))


def _scope_relation(scope: dict[str, str], dim: str, value: str) -> str | None:
    """How an event scope relates to segment ``dim=value``; ``None`` if disjoint."""
    if not scope:
        return "all_visitors"
    if dim in scope and scope[dim] != value:
        return None
    if scope == {dim: value}:
        return "same_segment"
    return "overlapping_scope"


def _link_incident(
    day: Date, dim: str, value: str, incidents: list[tuple[Date, str, str, dict[str, str]]], tol: int
) -> LinkedEvent | None:
    for ev_date, name, scope_text, scope in incidents:
        if abs((day - ev_date).days) > tol:
            continue
        relation = _scope_relation(scope, dim, value)
        if relation is None:
            continue
        return LinkedEvent(
            date=ev_date,
            name=name,
            scope=scope_text,
            scope_overlap=relation,  # type: ignore[arg-type]
            note=(
                f"Coincides in time and scope with incident '{name}'. This is a "
                "coincidence, not proof that the incident caused the shift; confirm with "
                "the owning team's logs or an independent data source."
            ),
        )
    return None


def _pct(x: float) -> str:
    return f"{100 * x:.2f}%"


# --------------------------------------------------------------------------- main tool


def check_data_quality(
    metrics: pd.DataFrame,
    events: pd.DataFrame | None = None,
    params: DetectorParams | None = None,
) -> DataQualityReport:
    """Flag missing dates and possible tracking issues, linking flags to incidents.

    ``metrics`` and ``events`` may be raw frames or the output of the loaders; both are
    (re)validated, so invalid input raises ``DataValidationError``.
    """
    params = params or DetectorParams()
    data = validate_metrics(metrics)
    incidents: list[tuple[Date, str, str, dict[str, str]]] = []
    if events is not None:
        ev = validate_events(events)
        for row in ev[ev["type"] == "incident"].itertuples(index=False):
            incidents.append((row.date.date(), str(row.name), str(row.scope), parse_scope(row.scope)))

    first, last = data["date"].min(), data["date"].max()
    all_days = pd.date_range(first, last, freq="D")
    present = pd.DatetimeIndex(data["date"].unique())
    missing = [d.date() for d in all_days.difference(present)]

    flags: list[DataQualityFlag] = [
        DataQualityFlag(
            date=d,
            kind="missing_date",
            segment="all",
            reason=f"No rows for {d.isoformat()} between the first and last date in the data.",
        )
        for d in missing
    ]

    half = params.window_days // 2
    for dim in DIMENSIONS:
        grouped = data.groupby(["date", dim])[["sessions", "orders"]].sum()
        for value in sorted(data[dim].unique()):
            daily = grouped.xs(value, level=dim).reindex(all_days)
            sessions = daily["sessions"].to_numpy(dtype=float)
            orders = daily["orders"].to_numpy(dtype=float)
            # Convention: a missing date or a zero-session day has no conversion rate.
            with np.errstate(divide="ignore", invalid="ignore"):
                rate = np.where(sessions > 0, orders / sessions, np.nan)

            r_med, r_mad = _baseline(rate, half, params.min_baseline_days)
            s_med, s_mad = _baseline(sessions, half, params.min_baseline_days)

            for i, day in enumerate(all_days):
                x, m = rate[i], r_med[i]
                # Convention: skip days with no rate or a baseline median of 0.
                if np.isnan(x) or np.isnan(m) or m <= 0:
                    continue
                scale = _scale(r_mad[i], m, params.rel_scale_floor)
                if params.sampling_noise_floor and s_med[i] > 0:
                    scale = max(scale, float(np.sqrt(m * (1 - m) / s_med[i])))
                dev = x - m
                rel = dev / m
                z = dev / scale
                if abs(z) <= params.k or abs(rel) < params.min_relative_deviation:
                    continue

                sm = s_med[i]
                if np.isnan(sm) or sm <= 0:
                    sessions_normal, s_rel = True, 0.0
                else:
                    s_dev = sessions[i] - sm
                    s_rel = s_dev / sm
                    s_z = s_dev / _scale(s_mad[i], sm, params.rel_scale_floor)
                    sessions_normal = not (
                        abs(s_z) > params.sessions_k and abs(s_rel) >= params.sessions_min_relative_deviation
                    )

                direction = "below" if dev < 0 else "above"
                seg = f"{dim}={value}"
                stats = (
                    f"Conversion {_pct(x)} is {_pct(abs(rel))} {direction} its rolling baseline "
                    f"{_pct(m)} (robust z = {z:.1f}, k = {params.k:g})"
                )
                if sessions_normal:
                    kind: FlagKind = "possible_tracking_issue"
                    reason = (
                        f"{stats} while sessions are normal ({int(sessions[i]):,} vs baseline "
                        f"{sm:,.0f}): possible tracking issue, not necessarily a behaviour change."
                    )
                else:
                    kind = "conversion_shift_with_abnormal_sessions"
                    reason = (
                        f"{stats}, but sessions are also abnormal ({int(sessions[i]):,} vs baseline "
                        f"{sm:,.0f}): traffic changed, so this is not classed as a tracking issue."
                    )
                flags.append(
                    DataQualityFlag(
                        date=day.date(),
                        kind=kind,
                        segment=seg,
                        reason=reason,
                        observed_rate=float(x),
                        baseline_rate=float(m),
                        relative_deviation=float(rel),
                        robust_z=float(z),
                        sessions=int(sessions[i]),
                        baseline_sessions=None if np.isnan(sm) else float(sm),
                        sessions_normal=bool(sessions_normal),
                        linked_event=_link_incident(
                            day.date(), dim, value, incidents, params.incident_tolerance_days
                        ),
                    )
                )

    flags.sort(key=lambda f: (f.date, f.segment))
    excluded = sorted({f.date for f in flags if f.kind in DEFAULT_EXCLUDED_KINDS})
    return DataQualityReport(
        params=params,
        n_rows=len(data),
        date_range=(first.date(), last.date()),
        n_days_checked=len(all_days),
        missing_dates=missing,
        flags=flags,
        excluded_dates=excluded,
        exclusion_statement=_statement("date", excluded, rows_removed=None),
    )


def _statement(granularity: str, dates: list[Date], rows_removed: int | None) -> str:
    if not dates:
        return "No days were flagged, so no days are excluded from downstream estimates."
    shown = ", ".join(d.isoformat() for d in dates)
    scope = "all segments on" if granularity == "date" else "only the flagged segments on"
    rows = f" ({rows_removed} metric rows removed)" if rows_removed is not None else ""
    return (
        f"By default, {scope} {len(dates)} flagged day(s) are excluded from downstream "
        f"estimates{rows}: {shown}. Flags mark possible tracking issues, not confirmed ones."
    )


def apply_default_exclusions(
    metrics: pd.DataFrame,
    report: DataQualityReport,
    granularity: Literal["date", "segment_day"] = "date",
) -> tuple[pd.DataFrame, ExclusionSummary]:
    """Drop flagged days from ``metrics`` (Requirement 1.5) and say that it was done.

    ``granularity="date"`` (default) removes every row on a flagged date, so segment
    mix on the remaining days is not distorted by a partially removed day.
    ``"segment_day"`` removes only rows in the flagged segment (``dimension=value``).
    Missing-date flags have no rows to remove. The input frame is not modified.
    """
    data = validate_metrics(metrics)
    flagged = [f for f in report.flags if f.kind in DEFAULT_EXCLUDED_KINDS]
    keep = pd.Series(True, index=data.index)
    if granularity == "date":
        days = {pd.Timestamp(f.date) for f in flagged}
        keep &= ~data["date"].isin(days)
    else:
        for f in flagged:
            dim, _, value = f.segment.partition("=")
            keep &= ~((data["date"] == pd.Timestamp(f.date)) & (data[dim] == value))
    kept = data.loc[keep].reset_index(drop=True)
    dates = sorted({f.date for f in flagged})
    removed = len(data) - len(kept)
    summary = ExclusionSummary(
        granularity=granularity,
        excluded_dates=dates,
        rows_before=len(data),
        rows_removed=removed,
        statement=_statement(granularity, dates, removed),
    )
    return kept, summary
