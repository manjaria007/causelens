"""``daily_series``: conversion for each day, with the days Task 2 excluded marked (Task 10, the series panel of the console).

A description, not a test: it returns what was observed each day so a reader can see the series the other tools summarise. It makes no
decision. The tools that estimate (``confirm_change``, ``detect_change_point`` and the rest) are unchanged, and they use the same
exclusions: a day in ``quality.excluded_dates`` is marked ``excluded`` here, and its observed counts are shown (they are what the
tracking recorded) but no estimate uses them.

Conversion is ``orders / sessions`` for that day. Dates with no rows at all are listed in ``missing_dates`` and have no point. Pure and
deterministic.
"""
from __future__ import annotations

from collections.abc import Mapping
from datetime import date as Date
from typing import Literal

import pandas as pd
from pydantic import BaseModel, ConfigDict, Field

from causelens.analytics.data_quality import DataQualityReport, DetectorParams
from causelens.analytics.loaders import validate_metrics
from causelens.analytics.series import ExclusionDisclosure, prepare_daily_series

METRIC = "conversion rate (orders / sessions)"


class DailyPoint(BaseModel):
    model_config = ConfigDict(frozen=True)

    date: Date
    sessions: int
    orders: int
    conversion: float | None = Field(description="orders / sessions; None when the day had no sessions.")
    status: Literal["ok", "excluded", "no_sessions"] = Field(
        description="excluded: flagged as a possible data issue and not used by the estimates (the observed values are still shown)."
    )


class DailySeriesResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    status: Literal["ok", "insufficient_data"]
    metric: str
    segment: dict[str, str]
    first_date: Date
    last_date: Date
    points: list[DailyPoint]
    missing_dates: list[Date] = Field(
        description="Calendar days between first_date and last_date with no point in this series (no rows, for the whole data or the segment)."
    )
    exclusions: ExclusionDisclosure
    assumptions: list[str]
    summary: str


def daily_series(
    metrics: pd.DataFrame,
    events: pd.DataFrame | None = None,
    *,
    segment: Mapping[str, str] | None = None,
    quality: DataQualityReport | None = None,
    apply_exclusions: bool = True,
    detector_params: DetectorParams | None = None,
) -> DailySeriesResult:
    """Daily sessions, orders and conversion for the whole data or one segment, with excluded days marked.

    Data quality is judged on the whole dataset (pass ``quality`` to reuse a report), as in every other tool. Raises ``ValueError`` for an
    unknown segment dimension or a segment with no rows.
    """
    prepared = prepare_daily_series(metrics, events, segment=segment, quality=quality, apply_exclusions=apply_exclusions, detector_params=detector_params)
    data = validate_metrics(metrics)
    frame = data
    for dim, value in prepared.segment.items():
        frame = frame[frame[dim] == value]
    observed = frame.groupby("date")[["sessions", "orders"]].sum()
    status = prepared.daily["status"]
    points: list[DailyPoint] = []
    for ts, row in observed.iterrows():
        sessions, orders = int(row["sessions"]), int(row["orders"])
        excluded = status.loc[ts] == "excluded"
        points.append(DailyPoint(
            date=pd.Timestamp(ts).date(), sessions=sessions, orders=orders, conversion=orders / sessions if sessions > 0 else None,
            status="excluded" if excluded else ("no_sessions" if sessions <= 0 else "ok"),
        ))
    # Every calendar day of the data is a point or listed here. Not derived from the exclusion status, which would hide a day that is both
    # excluded (for the whole data) and absent from this segment.
    observed_days = set(observed.index)
    missing = [pd.Timestamp(d).date() for d in prepared.daily.index if d not in observed_days]
    n_excluded = sum(p.status == "excluded" for p in points)
    return DailySeriesResult(
        status="ok" if len(points) >= 2 else "insufficient_data", metric=METRIC, segment=prepared.segment,
        first_date=pd.Timestamp(prepared.daily.index.min()).date(), last_date=pd.Timestamp(prepared.daily.index.max()).date(),
        points=points, missing_dates=missing, exclusions=prepared.disclosure,
        assumptions=[
            "Each point is the day's orders divided by its sessions, for the whole data or the named segment.",
            "Excluded days show the counts that were recorded; the estimating tools do not use them.",
            "A date with no rows (in the whole data, or in the named segment) has no point and is listed as missing, whether or not it was also excluded.",
        ],
        summary=f"{len(points)} day(s) with data, {n_excluded} marked excluded, {len(missing)} date(s) missing.",
    )
