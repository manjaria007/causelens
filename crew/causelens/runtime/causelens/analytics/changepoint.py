"""``detect_change_point`` and event-candidate matching (Requirement 3).

Method (design.md): weekday-adjust the daily conversion series, then find the single
mean-shift change point that minimises the two-segment sum of squared errors (an exhaustive
binary split, equivalent to a CUSUM scan for one change). The **plausible set** is every start
date whose cost is within a likelihood-ratio threshold of the best:

    cost(tau) - cost(best)  <=  chi2_ppf(level, 1) * sigma_hat^2

so uncertainty comes from the data's own noise, not from a fixed number of days.

* **Weekday adjustment** (Requirement 3.2): multiplicative weekday factors are estimated jointly
  with the change point (alternate: fit the shift, estimate factors from the residual ratio to the
  fitted level, divide them out, refit) so a shift cannot masquerade as weekday seasonality and
  vice versa. Skipped, with the reason reported, when the series is too short.
* **Is there a change at all?** The best split's t-statistic is compared with a Bonferroni
  correction over all admissible splits (conservative; neighbouring splits are correlated). If no
  change is supported, the plausible set is wide and candidates are *not* matched.
* **Gaps.** Excluded or missing days are not used. If days just before the best date were
  unusable, the change could have begun on any of them, and they are added to the plausible dates.
* Task 2's default exclusions are applied and disclosed (see ``series.py``).

``match_event_candidates`` lists event-log entries that fall in the plausible range, plus entries
shortly *before* it (``lead_days``), because a release can precede its visible effect through
rollout and propagation lag. A candidate is a date coincidence only, never evidence of cause.
"""
from __future__ import annotations

import calendar
from collections.abc import Mapping
from datetime import date as Date
from datetime import timedelta
from typing import Literal

import numpy as np
import pandas as pd
from pydantic import BaseModel, ConfigDict, Field
from scipy import stats

from causelens.analytics.data_quality import DataQualityReport, DetectorParams
from causelens.analytics.loaders import validate_events
from causelens.analytics.series import (
    ExclusionDisclosure,
    IncidentContext,
    build_disclosure,
    prepare_daily_series,
)

_WEEKDAYS = list(calendar.day_name)  # Monday .. Sunday


class WeekdayAdjustment(BaseModel):
    model_config = ConfigDict(frozen=True)

    applied: bool
    reason: str
    factors: dict[str, float] | None = Field(
        None, description="Multiplicative weekday factors (1.0 = average day); the series is divided by them."
    )
    iterations: int = 0


class ChangePointResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    status: Literal["ok", "insufficient_data"]
    metric: str = "conversion"
    segment: dict[str, str]
    change_detected: bool
    direction: Literal["decrease", "increase"] | None
    best_date: Date | None = Field(description="First usable day of the new level at the best split.")
    plausible_dates: list[Date]
    plausible_range: tuple[Date, Date] | None
    plausible_level: float
    threshold_noise_units: float | None = Field(
        description="Likelihood-ratio threshold on the increase in squared error, in units of residual variance."
    )
    shift_in_residual_sd: float | None = Field(
        description="Level shift at the best split divided by the residual sd (signed)."
    )
    p_value_adjusted: float | None = Field(
        description="Best-split p-value, Bonferroni-adjusted over all admissible splits."
    )
    n_splits_scanned: int
    n_days_used: int
    n_days_before: int | None
    n_days_after: int | None
    search_window: tuple[Date, Date] | None
    weekday_adjustment: WeekdayAdjustment
    gap_note: str | None
    exclusions: ExclusionDisclosure
    assumptions: list[str]
    summary: str


class EventCandidate(BaseModel):
    model_config = ConfigDict(frozen=True)

    date: Date
    type: str
    name: str
    scope: str
    description: str
    match: Literal["in_plausible_set", "in_plausible_range", "precedes_range_within_lead"]
    days_from_best_date: int = Field(description="Event date minus best change date (negative = before).")
    date_excluded_as_flagged: bool
    incident_context: list[IncidentContext]
    note: str


class CandidateMatchResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    status: Literal["ok", "no_change_point"]
    plausible_range: tuple[Date, Date] | None
    lead_days: int
    candidates: list[EventCandidate]
    n_events_considered: int
    statement: str


# --------------------------------------------------------------------------- core search


#: Relative scale below which values are treated as equal. A series whose spread is under
#: ``REL_TOL`` times its magnitude is constant for every purpose here; this is far above float64
#: rounding error (~1e-16) and far below any real day-to-day variation in a conversion rate.
REL_TOL = 1e-9


def _split_costs(y: np.ndarray, min_seg: int) -> tuple[np.ndarray, np.ndarray]:
    """Two-segment SSE for each split ``k`` (left = y[:k], right = y[k:]), k in [min_seg, n-min_seg].

    The series is centred first, so rounding error scales with the *variation* in ``y`` rather than
    with its level: ``sum(y**2) - sum(y)**2 / k`` on a series of identical values near 0.03 would
    otherwise leave residue far larger than the true cost of zero.
    """
    n = len(y)
    c = y - y.mean()
    s, q = np.cumsum(c), np.cumsum(c * c)
    ks = np.arange(min_seg, n - min_seg + 1)
    left = q[ks - 1] - s[ks - 1] ** 2 / ks
    right = (q[-1] - q[ks - 1]) - (s[-1] - s[ks - 1]) ** 2 / (n - ks)
    return ks, np.maximum(left + right, 0.0)


def _tol_cost(x: np.ndarray) -> float:
    """Squared-error level below which a fit is exact: ``n * (REL_TOL * scale)**2``."""
    return float(len(x) * (REL_TOL * max(float(np.abs(x).max()), 1e-300)) ** 2)


def _sse(y: np.ndarray) -> float:
    return float(np.sum((y - y.mean()) ** 2))


def _fit(
    x: np.ndarray, wd: np.ndarray, min_seg: int, adjust: bool, max_iter: int
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray | None, int, str | None]:
    """Jointly fit weekday factors and one mean shift.

    Returns ``(y, ks, costs, factors, iterations, fallback_reason)``. Multiplicative factors are
    undefined when a fitted level or a weekday's average ratio is zero (for example a series of
    zero-order days); the fit then falls back to no adjustment and says why.
    """
    idx = np.arange(len(x))
    if adjust:
        factors = np.ones(7)
        reason = None
        for iteration in range(1, max_iter + 1):
            y = x / factors[wd]
            ks, costs = _split_costs(y, min_seg)
            k = int(ks[int(np.argmin(costs))])
            level = np.where(idx < k, y[:k].mean(), y[k:].mean())
            if not np.all(level > 0):
                reason = "a fitted conversion level is zero, so multiplicative weekday factors are undefined"
                break
            ratio = x / level
            new = np.array([ratio[wd == w].mean() for w in range(7)])
            if not (np.all(np.isfinite(new)) and np.all(new > 0)):
                reason = "a weekday's average conversion is zero, so its multiplicative factor is undefined"
                break
            new = new / float(np.mean(new[wd]))
            done = float(np.max(np.abs(new - factors))) < 1e-12
            factors = new
            if done:
                break
        if reason is None:
            y = x / factors[wd]
            ks, costs = _split_costs(y, min_seg)
            return y, ks, costs, factors, iteration, None
        ks, costs = _split_costs(x, min_seg)
        return x, ks, costs, None, 0, reason
    ks, costs = _split_costs(x, min_seg)
    return x, ks, costs, None, 0, None


def _empty_result(
    reason: str,
    prepared_segment: dict[str, str],
    disclosure: ExclusionDisclosure,
    n_used: int,
    window: tuple[Date, Date] | None,
    level: float,
    wk_reason: str,
) -> ChangePointResult:
    return ChangePointResult(
        status="insufficient_data",
        segment=prepared_segment,
        change_detected=False,
        direction=None,
        best_date=None,
        plausible_dates=[],
        plausible_range=None,
        plausible_level=level,
        threshold_noise_units=None,
        shift_in_residual_sd=None,
        p_value_adjusted=None,
        n_splits_scanned=0,
        n_days_used=n_used,
        n_days_before=None,
        n_days_after=None,
        search_window=window,
        weekday_adjustment=WeekdayAdjustment(applied=False, reason=wk_reason),
        gap_note=None,
        exclusions=disclosure,
        assumptions=[],
        summary=reason,
    )


def _constant_result(
    segment: dict[str, str],
    disclosure: ExclusionDisclosure,
    n_used: int,
    window: tuple[Date, Date] | None,
    level: float,
    n_splits: int,
    adjusted: bool = False,
) -> ChangePointResult:
    """The usable series has no variation (to within ``REL_TOL``): there is nothing to locate."""
    what = "after removing the weekday pattern " if adjusted else ""
    return ChangePointResult(
        status="ok",
        segment=segment,
        change_detected=False,
        direction=None,
        best_date=None,
        plausible_dates=[],
        plausible_range=None,
        plausible_level=level,
        threshold_noise_units=None,
        shift_in_residual_sd=None,
        p_value_adjusted=1.0,
        n_splits_scanned=n_splits,
        n_days_used=n_used,
        n_days_before=None,
        n_days_after=None,
        search_window=window,
        weekday_adjustment=WeekdayAdjustment(
            applied=False, reason="not needed: the series has no variation to adjust"
        ),
        gap_note=None,
        exclusions=disclosure,
        assumptions=[
            "A single sustained shift in the conversion level is assumed (one change point).",
            "A date being plausible says only when the level shifted, not why.",
        ],
        summary=(
            f"No change point exists: the conversion series {what}is constant across the {n_used} usable "
            "days, so there is no shift to locate and no events are matched."
        ),
    )


def detect_change_point(
    metrics: pd.DataFrame,
    events: pd.DataFrame | None = None,
    *,
    segment: Mapping[str, str] | None = None,
    search_window: tuple[Date | str, Date | str] | None = None,
    quality: DataQualityReport | None = None,
    apply_exclusions: bool = True,
    detector_params: DetectorParams | None = None,
    adjust_weekdays: bool = True,
    min_segment_days: int = 7,
    plausible_level: float = 0.95,
    alpha: float = 0.05,
    min_days_for_weekday_adjustment: int = 28,
    max_iterations: int = 50,
) -> ChangePointResult:
    """Estimate when the conversion level shifted, with a plausible set of start dates.

    ``search_window`` restricts the scan to ``(start, end)`` (inclusive). Raises ``ValueError``
    for invalid arguments; too little usable data yields ``status="insufficient_data"``.
    """
    if min_segment_days < 3:
        raise ValueError("min_segment_days must be at least 3")
    if not 0 < plausible_level < 1:
        raise ValueError("plausible_level must be in (0, 1)")
    if not 1e-12 <= alpha <= 0.5:
        raise ValueError("alpha must be between 1e-12 and 0.5")

    prepared = prepare_daily_series(
        metrics,
        events,
        segment=segment,
        quality=quality,
        apply_exclusions=apply_exclusions,
        detector_params=detector_params,
    )
    daily = prepared.daily
    window: tuple[Date, Date] | None = None
    if search_window is not None:
        lo, hi = (pd.Timestamp(w).date() for w in search_window)
        if lo > hi:
            raise ValueError(f"search_window starts ({lo}) after it ends ({hi})")
        window = (lo, hi)
        in_window = (daily.index >= pd.Timestamp(lo)) & (daily.index <= pd.Timestamp(hi))
    else:
        in_window = np.ones(len(daily), dtype=bool)
    disclosure = build_disclosure(
        prepared.quality,
        apply_exclusions,
        {"search": window or (daily.index[0].date(), daily.index[-1].date())},
    )
    usable = daily[in_window & (daily["status"] == "ok")]
    n = len(usable)
    if n < 2 * min_segment_days:
        excluded_here = [d for d in disclosure.excluded_days if d.windows]
        extra = (
            " Flagged days removed from the search range: "
            + ", ".join(d.date.isoformat() for d in excluded_here) + "."
            if excluded_here and apply_exclusions
            else ""
        )
        return _empty_result(
            f"Insufficient data to locate a change point: {n} usable day(s), but at least "
            f"{2 * min_segment_days} are needed ({min_segment_days} on each side of a change)." + extra,
            prepared.segment,
            disclosure,
            n,
            window,
            plausible_level,
            "not attempted: insufficient data",
        )

    dates = [d.date() for d in usable.index]
    x = usable["rate"].to_numpy(dtype=float)
    wd = usable.index.day_of_week.to_numpy()

    # ---- weekday adjustment (only with enough data in every weekday)
    counts = np.bincount(wd, minlength=7)
    if not adjust_weekdays:
        adjust, wk_reason = False, "switched off by the caller"
    elif n < min_days_for_weekday_adjustment:
        adjust, wk_reason = False, f"series too short ({n} < {min_days_for_weekday_adjustment} usable days)"
    elif counts.min() < 3:
        adjust, wk_reason = False, "fewer than 3 usable days for at least one weekday"
    else:
        adjust, wk_reason = True, "multiplicative weekday factors estimated jointly with the shift"

    if _sse(x) <= _tol_cost(x):
        return _constant_result(prepared.segment, disclosure, n, window, plausible_level, n_splits=n - 2 * min_segment_days + 1)

    y, ks, costs, factors, iterations, fallback = _fit(x, wd, min_segment_days, adjust, max_iterations)
    if fallback is not None:
        adjust, wk_reason = False, f"fell back to no adjustment: {fallback}"
    n_splits = len(ks)
    if _sse(y) <= _tol_cost(y):  # constant once the weekday pattern is removed
        return _constant_result(prepared.segment, disclosure, n, window, plausible_level, n_splits=n_splits, adjusted=adjust)
    best = int(np.argmin(costs))
    k_best, c_best = int(ks[best]), float(costs[best])

    dof = max(n - 2 - (6 if adjust else 0), 1)
    sigma2 = c_best / dof
    left, right = y[:k_best].mean(), y[k_best:].mean()
    shift = float(right - left)
    thr = float(stats.chi2.ppf(plausible_level, 1))
    tol = _tol_cost(y)
    if c_best <= tol:
        # A perfect step: no residual noise, so the residual sd is undefined and only splits that
        # also fit exactly are plausible. (The constant case returned above.)
        shift_sd = None
        p_adj = 0.0
        near = costs <= tol
    else:
        t_stat = shift / np.sqrt(sigma2 * (1 / k_best + 1 / (n - k_best)))
        p_single = float(2 * stats.t.sf(abs(t_stat), dof))
        p_adj = min(1.0, p_single * n_splits)
        shift_sd = float(shift / np.sqrt(sigma2))
        near = (costs - c_best) <= thr * sigma2
    detected = bool(p_adj < alpha)

    # ---- plausible calendar dates (unusable days before a candidate start could also be the start)
    plausible: set[Date] = set()
    gap_note = None
    for k in ks[near]:
        start = dates[int(k)]
        prev = dates[int(k) - 1] if k > 0 else start - timedelta(days=1)
        d = prev + timedelta(days=1)
        while d <= start:
            plausible.add(d)
            d += timedelta(days=1)
    best_date = dates[k_best]
    prev_best = dates[k_best - 1]
    if (best_date - prev_best).days > 1:
        gap_note = (
            f"{(best_date - prev_best).days - 1} unusable day(s) (excluded as flagged or missing) lie between "
            f"{prev_best.isoformat()} and {best_date.isoformat()}, so the change could have begun on any of them."
        )
    plausible_dates = sorted(plausible)

    direction = None if shift == 0 else ("decrease" if shift < 0 else "increase")
    level_pct = f"{100 * plausible_level:g}"
    assumptions = [
        "A single sustained shift in the conversion level is assumed (one change point).",
        "Residual day-to-day noise is assumed roughly constant and independent across days.",
        f"The plausible set is an approximate {level_pct}% likelihood-ratio set, not an exact interval.",
        "A date being plausible says only when the level shifted, not why.",
    ]
    if not adjust:
        assumptions.append(f"No weekday adjustment was applied ({wk_reason}); weekday seasonality may blur the estimate.")
    if disclosure.uncertain_context_note:
        assumptions.append(disclosure.uncertain_context_note)

    shift_text = (
        f"{shift_sd:+.2f} residual sd" if shift_sd is not None else "residual sd undefined: the fit has no residual noise"
    )
    if detected:
        summary = (
            f"The best estimate is a {direction} in conversion beginning {best_date.isoformat()} "
            f"({shift_text}; adjusted p = {p_adj:.4g}, Bonferroni over {n_splits} splits). "
            f"{len(plausible_dates)} date(s) are plausible at the {level_pct}% level, "
            f"{plausible_dates[0].isoformat()} to {plausible_dates[-1].isoformat()}"
            f"; n = {k_best} days before and {n - k_best} after. "
            + ("Weekday adjustment applied. " if adjust else f"No weekday adjustment ({wk_reason}). ")
            + (gap_note + " " if gap_note else "")
            + "This locates a shift in time only and does not say why it happened."
        )
    else:
        summary = (
            "No change point is supported: the best split "
            f"({best_date.isoformat()}) has adjusted p = {p_adj:.4g} (Bonferroni over {n_splits} splits), "
            "so the plausible dates span too much of the series to be informative and no events are matched."
        )
    if disclosure.applied and any(d.windows for d in disclosure.excluded_days):
        summary += (
            " Flagged days excluded from the search: "
            + ", ".join(d.date.isoformat() for d in disclosure.excluded_days if d.windows)
            + "."
        )

    return ChangePointResult(
        status="ok",
        segment=prepared.segment,
        change_detected=detected,
        direction=direction,
        best_date=best_date,
        plausible_dates=plausible_dates,
        plausible_range=(plausible_dates[0], plausible_dates[-1]),
        plausible_level=plausible_level,
        threshold_noise_units=thr,
        shift_in_residual_sd=shift_sd,
        p_value_adjusted=p_adj,
        n_splits_scanned=n_splits,
        n_days_used=n,
        n_days_before=k_best,
        n_days_after=n - k_best,
        search_window=window,
        weekday_adjustment=WeekdayAdjustment(
            applied=adjust,
            reason=wk_reason,
            factors={_WEEKDAYS[w]: float(f) for w, f in enumerate(factors)} if adjust and factors is not None else None,
            iterations=iterations if adjust else 0,
        ),
        gap_note=gap_note,
        exclusions=disclosure,
        assumptions=assumptions,
        summary=summary,
    )


# --------------------------------------------------------------------------- candidates


def match_event_candidates(
    change_point: ChangePointResult,
    events: pd.DataFrame,
    *,
    lead_days: int = 3,
) -> CandidateMatchResult:
    """List event-log entries that coincide with the computed plausible dates (Requirement 3.3).

    Every event inside the plausible range is a candidate (``in_plausible_set`` if its date is one of
    the plausible dates, else ``in_plausible_range``). Events dated up to ``lead_days`` *before* the
    earliest plausible date are also listed (``precedes_range_within_lead``): a release can precede
    its visible effect. Later events cannot explain an earlier shift and are not listed.

    A candidate is a date coincidence only. Incident entries also carry Task 2 context: an
    ``observed`` link coincides with flagged traffic; a ``historical_only`` link is uncertain.
    If no change point is supported nothing is matched, and that is stated.
    """
    if lead_days < 0:
        raise ValueError("lead_days must be >= 0")
    ev = validate_events(events)
    if change_point.status != "ok" or not change_point.change_detected or change_point.plausible_range is None:
        why = (
            "the change-point search had insufficient data"
            if change_point.status != "ok"
            else "no change point is statistically supported"
        )
        return CandidateMatchResult(
            status="no_change_point",
            plausible_range=None,
            lead_days=lead_days,
            candidates=[],
            n_events_considered=len(ev),
            statement=f"No events were matched to a change because {why}.",
        )

    lo, hi = change_point.plausible_range
    plausible = set(change_point.plausible_dates)
    best = change_point.best_date
    assert best is not None
    excluded_ctx = {d.date: d for d in change_point.exclusions.excluded_days}
    candidates: list[EventCandidate] = []
    for row in ev.itertuples(index=False):
        day = row.date.date()
        if day in plausible:
            match = "in_plausible_set"
        elif lo <= day <= hi:
            match = "in_plausible_range"
        elif lo - timedelta(days=lead_days) <= day < lo:
            match = "precedes_range_within_lead"
        else:
            continue
        flagged = excluded_ctx.get(day)
        ctx = [c for c in (flagged.incident_context if flagged else []) if c.name == row.name]
        note = (
            "Listed as a candidate because its date coincides with the estimated change; this is not "
            "evidence that it caused the change and it still has to be tested."
        )
        if match == "precedes_range_within_lead":
            note += (
                f" It precedes the earliest plausible date by {(lo - day).days} day(s), which fits a delayed "
                "or gradual rollout but is not established."
            )
        if row.type == "incident":
            note += " It is an incident, so a data-collection problem must be ruled out before reading the change as behaviour."
        if flagged is not None:
            note += " Its date was excluded from the analysis as a flagged day."
        for c in ctx:
            note += " " + c.note
        candidates.append(
            EventCandidate(
                date=day,
                type=str(row.type),
                name=str(row.name),
                scope=str(row.scope),
                description=str(row.description),
                match=match,  # type: ignore[arg-type]
                days_from_best_date=(day - best).days,
                date_excluded_as_flagged=flagged is not None,
                incident_context=ctx,
                note=note,
            )
        )
    order = {"in_plausible_set": 0, "in_plausible_range": 1, "precedes_range_within_lead": 2}
    candidates.sort(key=lambda c: (order[c.match], abs(c.days_from_best_date), c.name))
    statement = (
        f"{len(candidates)} of {len(ev)} logged event(s) are candidates for the change that began "
        f"between {lo.isoformat()} and {hi.isoformat()} (including events up to {lead_days} day(s) before "
        "the earliest plausible date). Candidates are date coincidences, not causes."
        if candidates
        else f"No logged event falls between {lo.isoformat()} and {hi.isoformat()} or up to {lead_days} "
        "day(s) before it, so the change has no event-log candidate."
    )
    return CandidateMatchResult(
        status="ok",
        plausible_range=(lo, hi),
        lead_days=lead_days,
        candidates=candidates,
        n_events_considered=len(ev),
        statement=statement,
    )
