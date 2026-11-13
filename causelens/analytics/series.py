"""Daily series preparation with Task 2's default exclusions, disclosed.

Shared by ``confirm_change`` and ``detect_change_point``. Pure and deterministic.

Flagged days (possible tracking issues, conversion shifts with abnormal sessions, volume
anomalies) are excluded from the series by default (Requirement 1.5) and every exclusion is
reported: which dates, why, which windows they fall in, and what incident context exists.
Incident context is *context*, never an explanation:

* ``observed_on_flagged_day`` links coincide with the flagged day in time and traffic;
* ``historical_only`` links are inferred from earlier traffic and are labelled **uncertain**.

Neither is evidence that the incident caused anything.
"""
from __future__ import annotations

from collections.abc import Mapping
from datetime import date as Date
from typing import Literal

import pandas as pd
from pydantic import BaseModel, ConfigDict, Field

from causelens.analytics.data_quality import (
    DEFAULT_EXCLUDED_KINDS,
    DataQualityFlag,
    DataQualityReport,
    DetectorParams,
    check_data_quality,
)
from causelens.analytics.loaders import DIMENSIONS, validate_events, validate_metrics

DayStatus = Literal["ok", "missing", "excluded", "no_sessions"]


class IncidentContext(BaseModel):
    """An incident that Task 2 linked to a flag on an excluded day."""

    model_config = ConfigDict(frozen=True)

    name: str
    date: Date
    overlap_basis: Literal["observed_on_flagged_day", "historical_only"]
    certainty: Literal["coincides_in_time_and_traffic", "uncertain_historical_overlap_only"]
    note: str


class ExcludedDay(BaseModel):
    model_config = ConfigDict(frozen=True)

    date: Date
    kinds: list[str]
    n_flags: int
    segments: list[str]
    most_deviant_segment: str = Field(
        description="Flagged segment with the largest relative deviation. Overlapping segments "
        "(e.g. device=desktop and browser=edge) can tie, so this is not a localization."
    )
    baseline_truncated: bool = Field(
        description="True when every flag that excluded this day rests on a truncated baseline."
    )
    incident_context: list[IncidentContext]
    windows: list[str] = Field(default_factory=list, description="Analysis windows containing this day.")


class ExclusionDisclosure(BaseModel):
    """What was removed before analysis, and why."""

    model_config = ConfigDict(frozen=True)

    applied: bool
    statement: str
    excluded_days: list[ExcludedDay]
    missing_dates: list[Date]
    truncated_baseline_dates: list[Date]
    uncertain_context_note: str | None = Field(
        None, description="Present when any incident link is historical-only."
    )
    truncation_caution: str | None = Field(
        None, description="Present when an excluded day rests only on a truncated baseline."
    )


class PreparedSeries(BaseModel):
    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    daily: pd.DataFrame = Field(
        description="Index: every date from the data's first to last. Columns: sessions, orders, "
        "rate, status. Unusable days keep NaN counts."
    )
    disclosure: ExclusionDisclosure
    quality: DataQualityReport
    segment: dict[str, str]


def _incident_contexts(flags: list[DataQualityFlag]) -> list[IncidentContext]:
    best: dict[tuple[str, Date], str] = {}
    for f in flags:
        if f.linked_event is None:
            continue
        key = (f.linked_event.name, f.linked_event.date)
        if best.get(key) != "observed_on_flagged_day":
            best[key] = f.linked_event.overlap_basis
    out = []
    for (name, ev_date), basis in sorted(best.items(), key=lambda kv: (kv[0][1], kv[0][0])):
        if basis == "observed_on_flagged_day":
            out.append(
                IncidentContext(
                    name=name,
                    date=ev_date,
                    overlap_basis="observed_on_flagged_day",
                    certainty="coincides_in_time_and_traffic",
                    note=(
                        f"Incident '{name}' coincides in time with traffic in the flagged segment. "
                        "This is context, not proof that it caused the flagged shift."
                    ),
                )
            )
        else:
            out.append(
                IncidentContext(
                    name=name,
                    date=ev_date,
                    overlap_basis="historical_only",
                    certainty="uncertain_historical_overlap_only",
                    note=(
                        f"Uncertain context: incident '{name}' shared traffic with a flagged segment "
                        "only earlier in the data, with none recorded on this day. Do not treat it as "
                        "an explanation."
                    ),
                )
            )
    return out


def build_disclosure(
    quality: DataQualityReport,
    applied: bool,
    windows: Mapping[str, tuple[Date, Date]] | None = None,
) -> ExclusionDisclosure:
    """Describe the exclusions in ``quality``; ``windows`` labels which analysis windows each hits."""
    by_day: dict[Date, list[DataQualityFlag]] = {}
    for f in quality.flags:
        if f.kind in DEFAULT_EXCLUDED_KINDS:
            by_day.setdefault(f.date, []).append(f)

    days: list[ExcludedDay] = []
    for day in sorted(by_day):
        flags = by_day[day]
        strongest = max(flags, key=lambda f: abs(f.relative_deviation or 0.0))
        days.append(
            ExcludedDay(
                date=day,
                kinds=sorted({f.kind for f in flags}),
                n_flags=len(flags),
                segments=sorted({f.segment for f in flags}),
                most_deviant_segment=strongest.segment,
                baseline_truncated=all(f.baseline_truncated for f in flags),
                incident_context=_incident_contexts(flags),
                windows=[
                    name for name, (lo, hi) in (windows or {}).items() if lo <= day <= hi
                ],
            )
        )

    uncertain = any(c.overlap_basis == "historical_only" for d in days for c in d.incident_context)
    truncated = [d.date for d in days if d.baseline_truncated]
    if applied:
        statement = quality.exclusion_statement
    elif days:
        statement = (
            f"{len(days)} flagged day(s) were NOT excluded because exclusions were switched off; "
            "results may include possible tracking issues: " + ", ".join(d.date.isoformat() for d in days) + "."
        )
    else:
        statement = "No days were flagged, so nothing would be excluded."
    return ExclusionDisclosure(
        applied=applied,
        statement=statement,
        excluded_days=days,
        missing_dates=list(quality.missing_dates),
        truncated_baseline_dates=truncated,
        uncertain_context_note=(
            "Some incident links are historical-only: the incident's scope and the flagged segment "
            "shared traffic earlier in the data but not on the flagged day. They are uncertain context "
            "and are not used as explanations."
            if uncertain
            else None
        ),
        truncation_caution=(
            "For " + ", ".join(d.isoformat() for d in truncated) + " every flag rests on a truncated "
            "baseline (near the start or end of the data); a persistent behaviour change that began "
            "there could have been excluded by mistake."
            if truncated
            else None
        ),
    )


def prepare_daily_series(
    metrics: pd.DataFrame,
    events: pd.DataFrame | None = None,
    *,
    segment: Mapping[str, str] | None = None,
    quality: DataQualityReport | None = None,
    apply_exclusions: bool = True,
    detector_params: DetectorParams | None = None,
) -> PreparedSeries:
    """Daily sessions/orders/rate for the whole data (or one segment), with exclusions applied.

    Data quality is always judged on the *whole* dataset, then the flagged dates are removed from
    the (possibly segment-filtered) series. Pass ``quality`` to reuse an existing Task 2 report.
    Raises ``ValueError`` for an unknown segment dimension or a segment with no rows.
    """
    data = validate_metrics(metrics)
    if quality is None:
        ev = validate_events(events) if events is not None else None
        quality = check_data_quality(data, ev, detector_params)

    seg = dict(segment or {})
    bad = [d for d in seg if d not in DIMENSIONS]
    if bad:
        raise ValueError(f"unknown segment dimension(s) {bad}; expected among {list(DIMENSIONS)}")
    frame = data
    for dim, value in seg.items():
        frame = frame[frame[dim] == value]
    if frame.empty:
        raise ValueError(f"no rows match segment {seg}")

    all_days = pd.date_range(data["date"].min(), data["date"].max(), freq="D")
    daily = frame.groupby("date")[["sessions", "orders"]].sum().reindex(all_days).astype(float)
    daily.index.name = "date"
    status = pd.Series("ok", index=all_days, dtype="object")
    status[daily["sessions"].isna()] = "missing"
    excluded = {pd.Timestamp(d) for d in quality.excluded_dates} if apply_exclusions else set()
    if excluded:
        mask = status.index.isin(list(excluded))
        status[mask] = "excluded"
        daily.loc[mask, ["sessions", "orders"]] = float("nan")
    zero = (status == "ok") & (daily["sessions"] <= 0)
    status[zero] = "no_sessions"
    daily["rate"] = (daily["orders"] / daily["sessions"]).where(status == "ok")
    daily["status"] = status

    return PreparedSeries(
        daily=daily,
        disclosure=build_disclosure(quality, apply_exclusions),
        quality=quality,
        segment=seg,
    )
