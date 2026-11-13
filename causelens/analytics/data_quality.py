"""Data-quality checks: missing dates, possible tracking issues, volume anomalies.

Method (design.md, ``check_data_quality``): for every top-level segment (each value
of each single dimension, e.g. ``device=desktop``) build the daily conversion series
``orders / sessions`` and the daily sessions series, and compare each day with a robust
baseline made of the median and MAD of the *other* days in a centred rolling window.

- Conversion far outside its baseline while sessions are normal: possible tracking issue.
- Conversion far outside its baseline while sessions are also abnormal: reported, but not
  classed as tracking.
- Sessions far outside their baseline with conversion normal: volume anomaly.

Parameters and edge-case conventions are documented in ``docs/data-quality.md``.
Everything here is pure and deterministic: no randomness, network or LLM calls.

Wording follows ``.kiro/steering/scientist-rules.md``: a flag is a *possible* issue, and an
incident that coincides with it is a coincidence in time and scope, not proof of causation.
"""
from __future__ import annotations

from datetime import date as Date
from typing import Literal

import numpy as np
import pandas as pd
from pydantic import BaseModel, ConfigDict, Field, model_validator

from causelens.analytics.loaders import (
    DIMENSIONS,
    parse_scope,
    validate_events,
    validate_metrics,
)

FlagKind = Literal[
    "possible_tracking_issue",
    "conversion_shift_with_abnormal_sessions",
    "volume_anomaly",
    "missing_date",
]

#: Flag kinds whose days are excluded from downstream estimates by default (Req 1.5).
DEFAULT_EXCLUDED_KINDS: tuple[str, ...] = (
    "possible_tracking_issue",
    "conversion_shift_with_abnormal_sessions",
    "volume_anomaly",
)

_MAD_TO_SIGMA = 1.4826  # makes MAD comparable to a standard deviation for normal data


class DetectorParams(BaseModel):
    """Detector settings. Defaults and relationships are documented in ``docs/data-quality.md``."""

    model_config = ConfigDict(frozen=True)

    window_days: int = Field(
        15, ge=3, description="Centred window length (odd): window_days // 2 days each side."
    )
    min_baseline_days: int = Field(
        7, ge=3, description="Fewest usable baseline days to evaluate a day."
    )
    k: float = Field(5.0, gt=0, description="Flagged when |x - median| > k * robust scale.")
    rel_scale_floor: float = Field(
        0.01, ge=0, description="Robust scale is at least this fraction of |median| (guards MAD = 0)."
    )
    sampling_noise_floor: bool = Field(
        True,
        description="Also floor the conversion scale at sqrt(p(1-p)/baseline sessions), the "
        "binomial sampling noise of a day's rate, so small segments are not flagged by chance.",
    )
    min_relative_deviation: float = Field(
        0.10, ge=0, description="Also require |x - median| / median at least this large."
    )
    sessions_k: float = Field(5.0, gt=0, description="Sessions abnormal when beyond k * robust scale ...")
    sessions_min_relative_deviation: float = Field(
        0.40,
        ge=0,
        description="... and at least this relative distance from the median. Weekday and "
        "day-level traffic swings reach about 30% on the simulator, so smaller changes are "
        "treated as normal.",
    )
    incident_tolerance_days: int = Field(
        0, ge=0, description="Incident links when |flag date - incident date| <= this."
    )

    @model_validator(mode="after")
    def _check_relationships(self) -> DetectorParams:
        if self.window_days % 2 == 0:
            raise ValueError(
                f"window_days must be odd (got {self.window_days}): the window is centred, "
                "with window_days // 2 days on each side"
            )
        neighbours = self.window_days - 1
        if self.min_baseline_days > neighbours:
            raise ValueError(
                f"min_baseline_days ({self.min_baseline_days}) exceeds the {neighbours} neighbouring "
                f"days a window of {self.window_days} can hold, so no baseline could ever form"
            )
        return self

    @property
    def half_window(self) -> int:
        return self.window_days // 2


class LinkedEvent(BaseModel):
    """An incident that coincides with a flag in time and scope."""

    model_config = ConfigDict(frozen=True)

    date: Date
    name: str
    scope: str
    scope_overlap: Literal["all_visitors", "same_segment", "overlapping_scope"]
    overlap_sessions: int = Field(description="Sessions on the flagged day inside both segment and scope.")
    segment_share_in_scope: float | None = Field(
        description="Share of the flagged segment's sessions that day inside the incident scope."
    )
    note: str


class DataQualityFlag(BaseModel):
    model_config = ConfigDict(frozen=True)

    date: Date
    kind: FlagKind
    segment: str = Field(description="'dimension=value', or 'all' for missing dates.")
    reason: str
    observed_rate: float | None = None
    baseline_rate: float | None = None
    relative_deviation: float | None = Field(
        None, description="(observed - baseline) / baseline, for conversion or (volume_anomaly) sessions."
    )
    robust_z: float | None = Field(None, description="None when the baseline has zero spread.")
    sessions: int | None = None
    baseline_sessions: float | None = None
    sessions_normal: bool | None = None
    baseline_days_before: int | None = None
    baseline_days_after: int | None = None
    baseline_truncated: bool = Field(
        False,
        description="True when fewer than window_days // 2 usable days exist before or after: "
        "a persistent change in the last few days cannot be told apart from a one-day issue.",
    )
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
    truncated_baseline_dates: list[Date] = Field(
        description="Excluded dates where every excluding flag had a truncated baseline."
    )
    exclusion_statement: str

    def flags_for(self, date: Date | str, segment: str | None = None) -> list[DataQualityFlag]:
        day = pd.Timestamp(date).date()
        return [f for f in self.flags if f.date == day and (segment is None or f.segment == segment)]


class ExclusionSummary(BaseModel):
    model_config = ConfigDict(frozen=True)

    granularity: Literal["date", "segment_day"]
    excluded_dates: list[Date]
    truncated_baseline_dates: list[Date]
    rows_before: int
    rows_removed: int
    statement: str


# --------------------------------------------------------------------------- helpers


def _baseline(
    values: np.ndarray, half: int, min_days: int
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Per-day (median, MAD, days_before, days_after) of the other non-NaN values in the window.

    Median and MAD are NaN where fewer than ``min_days`` usable neighbours exist.
    """
    n = len(values)
    med = np.full(n, np.nan)
    mad = np.full(n, np.nan)
    before = np.zeros(n, dtype=int)
    after = np.zeros(n, dtype=int)
    for i in range(n):
        left = values[max(0, i - half) : i]
        right = values[i + 1 : min(n, i + half + 1)]
        left, right = left[~np.isnan(left)], right[~np.isnan(right)]
        before[i], after[i] = len(left), len(right)
        window = np.concatenate([left, right])
        if len(window) >= min_days:
            m = float(np.median(window))
            med[i] = m
            mad[i] = float(np.median(np.abs(window - m)))
    return med, mad, before, after


def _scale(mad: float, median: float, floor: float) -> float:
    return max(_MAD_TO_SIGMA * mad, floor * abs(median))


def _beyond(dev: float, scale: float, k: float) -> bool:
    """True when ``|dev|`` exceeds ``k * scale``. A zero scale means a perfectly flat
    baseline, so any non-zero deviation counts (and a zero deviation never does)."""
    if scale > 0:
        return abs(dev) / scale > k
    return dev != 0


def _z(dev: float, scale: float) -> float | None:
    return dev / scale if scale > 0 else None


def _scope_relation(scope: dict[str, str], dim: str, value: str) -> str:
    if not scope:
        return "all_visitors"
    if scope == {dim: value}:
        return "same_segment"
    return "overlapping_scope"


def _pct(x: float) -> str:
    return f"{100 * x:.2f}%"


def _z_text(z: float | None) -> str:
    return "undefined: the baseline has zero spread" if z is None else f"{z:.1f}"


class _Linker:
    """Links flags to incidents by checking the population they actually share."""

    def __init__(self, data: pd.DataFrame, incidents: list[tuple[Date, str, str, dict[str, str]]], tol: int):
        self._incidents = incidents
        self._tol = tol
        self._positive = data[data["sessions"] > 0]
        self._by_day = {d: g for d, g in data.groupby("date")}
        self._population: dict[tuple, bool] = {}

    @staticmethod
    def _mask(frame: pd.DataFrame, dim: str, value: str, scope: dict[str, str]) -> pd.Series:
        mask = frame[dim] == value
        for sdim, svalue in scope.items():
            mask &= frame[sdim] == svalue
        return mask

    def _shares_population(self, dim: str, value: str, scope: dict[str, str]) -> bool:
        key = (dim, value, tuple(sorted(scope.items())))
        if key not in self._population:
            frame = self._positive
            self._population[key] = bool(self._mask(frame, dim, value, scope).any())
        return self._population[key]

    def link(self, day: Date, dim: str, value: str) -> LinkedEvent | None:
        for ev_date, name, scope_text, scope in self._incidents:
            if abs((day - ev_date).days) > self._tol:
                continue
            # Disjoint or never co-occurring (e.g. mobile-only incident vs browser=edge when the
            # data has no mobile + edge traffic): nothing for the incident to have touched.
            if not self._shares_population(dim, value, scope):
                continue
            rows = self._by_day.get(pd.Timestamp(day))
            overlap, share = 0, None
            if rows is not None:
                in_segment = rows.loc[rows[dim] == value, "sessions"].sum()
                overlap = int(rows.loc[self._mask(rows, dim, value, scope), "sessions"].sum())
                share = float(overlap / in_segment) if in_segment > 0 else None
            return LinkedEvent(
                date=ev_date,
                name=name,
                scope=scope_text,
                scope_overlap=_scope_relation(scope, dim, value),  # type: ignore[arg-type]
                overlap_sessions=overlap,
                segment_share_in_scope=share,
                note=(
                    f"Coincides in time and scope with incident '{name}'. This is a "
                    "coincidence, not proof that the incident caused the shift; confirm with "
                    "the owning team's logs or an independent data source."
                ),
            )
        return None


def _truncation_note(truncated: bool, before: int, after: int) -> str:
    if not truncated:
        return ""
    return (
        f" The baseline is truncated ({before} usable days before, {after} after), so a persistent "
        "behaviour change that began in the last few days would look like this; confirm before "
        "treating it as tracking."
    )


# --------------------------------------------------------------------------- main tool


def check_data_quality(
    metrics: pd.DataFrame,
    events: pd.DataFrame | None = None,
    params: DetectorParams | None = None,
) -> DataQualityReport:
    """Flag missing dates, possible tracking issues and volume anomalies, linking incidents.

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
    linker = _Linker(data, incidents, params.incident_tolerance_days)

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

    half, min_days = params.half_window, params.min_baseline_days
    for dim in DIMENSIONS:
        grouped = data.groupby(["date", dim])[["sessions", "orders"]].sum()
        for value in sorted(data[dim].unique()):
            daily = grouped.xs(value, level=dim).reindex(all_days)
            sessions = daily["sessions"].to_numpy(dtype=float)
            orders = daily["orders"].to_numpy(dtype=float)
            # Convention: a missing date or a zero-session day has no conversion rate.
            with np.errstate(divide="ignore", invalid="ignore"):
                rate = np.where(sessions > 0, orders / sessions, np.nan)

            r_med, r_mad, r_before, r_after = _baseline(rate, half, min_days)
            s_med, s_mad, s_before, s_after = _baseline(sessions, half, min_days)
            seg = f"{dim}={value}"

            for i, day in enumerate(all_days):
                sm, x, m = s_med[i], rate[i], r_med[i]

                # --- sessions: abnormal only if both far in robust units and materially large
                sessions_abnormal = False
                s_rel = s_dev = 0.0
                if not np.isnan(sm) and sm > 0 and not np.isnan(sessions[i]):
                    s_dev = sessions[i] - sm
                    s_rel = s_dev / sm
                    s_scale = _scale(s_mad[i], sm, params.rel_scale_floor)
                    sessions_abnormal = (
                        _beyond(s_dev, s_scale, params.sessions_k)
                        and abs(s_rel) >= params.sessions_min_relative_deviation
                    )

                # --- conversion
                conv_flagged, dev, rel, z = False, 0.0, 0.0, None
                if not np.isnan(x) and not np.isnan(m) and m > 0:
                    scale = _scale(r_mad[i], m, params.rel_scale_floor)
                    if params.sampling_noise_floor and sm > 0:
                        scale = max(scale, float(np.sqrt(max(m * (1 - m), 0.0) / sm)))
                    dev = x - m
                    rel = dev / m
                    z = _z(dev, scale)
                    conv_flagged = (
                        _beyond(dev, scale, params.k) and abs(rel) >= params.min_relative_deviation
                    )

                if not conv_flagged and not sessions_abnormal:
                    continue

                link = linker.link(day.date(), dim, value)
                sess = int(sessions[i])
                base = None if np.isnan(sm) else float(sm)
                if conv_flagged:
                    before, after = int(r_before[i]), int(r_after[i])
                    direction = "below" if dev < 0 else "above"
                    stats = (
                        f"Conversion {_pct(x)} is {_pct(abs(rel))} {direction} its rolling baseline "
                        f"{_pct(m)} (robust z = {_z_text(z)}, k = {params.k:g})"
                    )
                    if not sessions_abnormal:
                        kind: FlagKind = "possible_tracking_issue"
                        reason = (
                            f"{stats} while sessions are normal ({sess:,} vs baseline {sm:,.0f}): "
                            "possible tracking issue, not necessarily a behaviour change."
                        )
                    else:
                        kind = "conversion_shift_with_abnormal_sessions"
                        reason = (
                            f"{stats}, but sessions are also abnormal ({sess:,} vs baseline "
                            f"{sm:,.0f}): traffic changed, so this is not classed as a tracking issue."
                        )
                    rate_fields = {
                        "observed_rate": float(x),
                        "baseline_rate": float(m),
                        "relative_deviation": float(rel),
                        "robust_z": z,
                    }
                else:
                    before, after = int(s_before[i]), int(s_after[i])
                    direction = "below" if s_dev < 0 else "above"
                    kind = "volume_anomaly"
                    reason = (
                        f"Sessions {sess:,} are {_pct(abs(s_rel))} {direction} their rolling baseline "
                        f"{sm:,.0f} while conversion is within its normal range: volume anomaly "
                        "(possible collection gap or genuine traffic change; cause not established)."
                    )
                    rate_fields = {
                        "observed_rate": None if np.isnan(x) else float(x),
                        "baseline_rate": None if np.isnan(m) else float(m),
                        "relative_deviation": float(s_rel),
                        "robust_z": _z(s_dev, _scale(s_mad[i], sm, params.rel_scale_floor)),
                    }
                truncated = before < half or after < half
                flags.append(
                    DataQualityFlag(
                        date=day.date(),
                        kind=kind,
                        segment=seg,
                        reason=reason + _truncation_note(truncated, before, after),
                        sessions=sess,
                        baseline_sessions=base,
                        sessions_normal=not sessions_abnormal,
                        baseline_days_before=before,
                        baseline_days_after=after,
                        baseline_truncated=truncated,
                        linked_event=link,
                        **rate_fields,
                    )
                )

    flags.sort(key=lambda f: (f.date, f.segment))
    excluded, truncated_only = _excluded_dates(flags)
    return DataQualityReport(
        params=params,
        n_rows=len(data),
        date_range=(first.date(), last.date()),
        n_days_checked=len(all_days),
        missing_dates=missing,
        flags=flags,
        excluded_dates=excluded,
        truncated_baseline_dates=truncated_only,
        exclusion_statement=_statement("date", excluded, truncated_only, rows_removed=None),
    )


def _excluded_dates(flags: list[DataQualityFlag]) -> tuple[list[Date], list[Date]]:
    by_day: dict[Date, list[DataQualityFlag]] = {}
    for f in flags:
        if f.kind in DEFAULT_EXCLUDED_KINDS:
            by_day.setdefault(f.date, []).append(f)
    excluded = sorted(by_day)
    truncated_only = [d for d in excluded if all(f.baseline_truncated for f in by_day[d])]
    return excluded, truncated_only


def _statement(
    granularity: str, dates: list[Date], truncated: list[Date], rows_removed: int | None
) -> str:
    if not dates:
        return "No days were flagged, so no days are excluded from downstream estimates."
    shown = ", ".join(d.isoformat() for d in dates)
    scope = "all segments on" if granularity == "date" else "only the flagged segments on"
    rows = f" ({rows_removed} metric rows removed)" if rows_removed is not None else ""
    text = (
        f"By default, {scope} {len(dates)} flagged day(s) are excluded from downstream "
        f"estimates{rows}: {shown}. Flags mark possible data issues, not confirmed ones."
    )
    if truncated:
        text += (
            " Caution: for "
            + ", ".join(d.isoformat() for d in truncated)
            + " every flag rests on a truncated baseline (near the start or end of the data); "
            "a persistent behaviour change could be excluded here by mistake."
        )
    return text


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
    dates, truncated = _excluded_dates(flagged)
    removed = len(data) - len(kept)
    summary = ExclusionSummary(
        granularity=granularity,
        excluded_dates=dates,
        truncated_baseline_dates=truncated,
        rows_before=len(data),
        rows_removed=removed,
        statement=_statement(granularity, dates, truncated, removed),
    )
    return kept, summary
