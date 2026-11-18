"""Difference-in-differences, its checks, and projection to the overall metric (Task 5.2, 5.3).

``estimate_did`` compares a *focal* segment (or a union of segments) with a *comparison* group over
the days that survive Task 2's default exclusions. The model is the one in design.md:

    conversion ~ treated:post + treated + C(date),   weights = sessions

with date fixed effects, so the ``post`` main effect is collinear and is omitted. The coefficient on
``treated:post`` is a **differential effect**: the focal segment's change minus the comparison group's
change. It equals the segment's own effect only if the event did not move the comparison group, and
that assumption is carried in every result. Attributing the estimate to a named event is a separate
claim made elsewhere (``candidates.py``).

Variance options are ordinary multi-day regression options: ``cov="hc1"`` (the default) or
``cov="cluster"`` (clustered by date, t reference on ``n_dates - 1`` degrees of freedom). On the simulator
with no planted effect (200 seeds) the 95% interval covered 0 in 95.5% of seeds with HC1 and in 99.5% with
date clusters, whose standard error (0.065 pp) is about 43% above the estimate's true sampling sd
(0.045 pp): clustering by date is too conservative with two observations per cluster, so HC1 is the default.
Neither is used for the single-date timing contrasts in ``timing.py``.

Pure and deterministic: no network or LLM calls, no randomness (placebo dates are a fixed grid).
"""
from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from datetime import date as Date
from typing import Literal

import numpy as np
import pandas as pd
import statsmodels.api as sm
from pydantic import BaseModel, ConfigDict, Field
from scipy import stats

from causelens.analytics.confirm import Interval
from causelens.analytics.data_quality import DataQualityReport, DetectorParams
from causelens.analytics.loaders import DIMENSIONS, validate_metrics
from causelens.analytics.series import ExclusionDisclosure, prepare_daily_series

SegmentSpec = Mapping[str, str]
Cov = Literal["cluster", "hc1"]
CheckStatus = Literal["pass", "borderline", "fail", "not_evaluable"]

#: Pre-trend criteria (prespecified in design.md): pass above PASS, fail below FAIL, borderline between.
PRETREND_PASS_P = 0.10
PRETREND_FAIL_P = 0.05
#: A placebo family fails when any Holm-adjusted p-value is below this.
PLACEBO_ALPHA = 0.05
#: Fewest usable days a check needs before the event (pre-trends) / before and after a fake date (placebos).
MIN_PRE_DAYS_FOR_TREND = 14
MIN_DAYS_BEFORE_PLACEBO = 14
MIN_DAYS_AFTER_PLACEBO = 7
N_PLACEBOS = 3


# --------------------------------------------------------------------------- panel


class Panel(BaseModel):
    """Daily sessions and orders for the focal and comparison groups, with Task 2 exclusions applied."""

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    daily: pd.DataFrame = Field(
        description="Index: every date in the data. Columns s_f, o_f, s_c, o_c (counts), s_all (sessions of the whole population, including traffic in neither group), usable (bool), "
        "rate_f, rate_c (fractions) and diff_pp (focal minus comparison, pp); NaN where not usable."
    )
    focal: list[dict[str, str]]
    comparison: list[dict[str, str]] | None = Field(description="None means 'everything outside the focal segments'.")
    disclosure: ExclusionDisclosure
    excluded_dates: list[Date]
    exclusions_applied: bool

    @property
    def usable_days(self) -> pd.DatetimeIndex:
        return self.daily.index[self.daily["usable"].to_numpy(dtype=bool)]


def _segments(spec: SegmentSpec | Sequence[SegmentSpec], what: str) -> list[dict[str, str]]:
    items = [spec] if isinstance(spec, Mapping) else list(spec)
    out: list[dict[str, str]] = []
    for seg in items:
        d = {str(k): str(v) for k, v in seg.items()}
        if not d or any(k not in DIMENSIONS for k in d):
            raise ValueError(f"{what} segments must name dimensions among {list(DIMENSIONS)} (got {d})")
        out.append(d)
    if not out:
        raise ValueError(f"{what} needs at least one segment")
    return out


def segment_mask(data: pd.DataFrame, segments: Sequence[Mapping[str, str]]) -> pd.Series:
    """Rows matching at least one conjunctive segment."""
    mask = pd.Series(False, index=data.index)
    for seg in segments:
        mk = pd.Series(True, index=data.index)
        for dim, value in seg.items():
            mk &= data[dim] == value
        mask |= mk
    return mask


def build_panel(
    metrics: pd.DataFrame,
    focal: SegmentSpec | Sequence[SegmentSpec],
    *,
    comparison: SegmentSpec | Sequence[SegmentSpec] | None = None,
    events: pd.DataFrame | None = None,
    quality: DataQualityReport | None = None,
    apply_exclusions: bool = True,
    detector_params: DetectorParams | None = None,
) -> Panel:
    """Daily focal and comparison series over usable days (Task 2's default exclusions applied).

    ``comparison`` defaults to all traffic outside the focal segments. Overlapping focal and comparison
    rows are rejected. A comparison group with no traffic is allowed here (it makes the panel's control
    invalid, which ``estimate_did`` reports as ``insufficient``).
    """
    data = validate_metrics(metrics)
    focal_segs = _segments(focal, "focal")
    comp_segs = _segments(comparison, "comparison") if comparison is not None else None
    prepared = prepare_daily_series(
        data, events, quality=quality, apply_exclusions=apply_exclusions, detector_params=detector_params
    )
    fmask = segment_mask(data, focal_segs)
    if not fmask.any():
        raise ValueError(f"no rows match the focal segment(s) {focal_segs}")
    cmask = ~fmask if comp_segs is None else segment_mask(data, comp_segs)
    if (fmask & cmask).any():
        raise ValueError("focal and comparison segments overlap")

    all_days = prepared.daily.index

    def daily_sum(mask: pd.Series) -> pd.DataFrame:
        g = data[mask].groupby("date")[["sessions", "orders"]].sum()
        return g.reindex(all_days).fillna(0.0).astype(float)

    f, c = daily_sum(fmask), daily_sum(cmask)
    total = data.groupby("date")["sessions"].sum().reindex(all_days).fillna(0.0).astype(float)
    daily = pd.DataFrame(
        {"s_f": f["sessions"], "o_f": f["orders"], "s_c": c["sessions"], "o_c": c["orders"], "s_all": total}, index=all_days
    )
    daily.index.name = "date"
    status_ok = (prepared.daily["status"] == "ok").to_numpy()
    daily["usable"] = status_ok & (daily["s_f"] > 0).to_numpy() & (daily["s_c"] > 0).to_numpy()
    with np.errstate(divide="ignore", invalid="ignore"):
        daily["rate_f"] = (daily["o_f"] / daily["s_f"]).where(daily["usable"])
        daily["rate_c"] = (daily["o_c"] / daily["s_c"]).where(daily["usable"])
    daily["diff_pp"] = 100 * (daily["rate_f"] - daily["rate_c"])
    excluded = [d.date() for d in all_days[(prepared.daily["status"] == "excluded").to_numpy()]]
    return Panel(
        daily=daily,
        focal=focal_segs,
        comparison=comp_segs,
        disclosure=prepared.disclosure,
        excluded_dates=excluded,
        exclusions_applied=apply_exclusions,
    )


# --------------------------------------------------------------------------- regression helpers


def _wls(
    y: np.ndarray, X: np.ndarray, w: np.ndarray | None, groups: np.ndarray, cov: Cov, index: int, level: float
) -> tuple[float, float, float, float, Interval]:
    """Fit WLS and return (estimate, se, df, p, interval) for one coefficient."""
    model = sm.WLS(y, X, weights=w) if w is not None else sm.OLS(y, X)
    if cov == "cluster":
        res = model.fit(cov_type="cluster", cov_kwds={"groups": groups})
        df = float(len(np.unique(groups)) - 1)
    else:
        res = model.fit(cov_type="HC1")
        df = float(res.df_resid)
    est, se = float(res.params[index]), float(res.bse[index])
    # Residuals at rounding level mean the data are exactly fitted (for example, two groups that never change):
    # the standard error is then rounding noise, and a "significant" effect from it would be spurious.
    if np.max(np.abs(res.resid)) <= 1e-9 * max(1.0, float(np.max(np.abs(y)))):
        raise ValueError("regression is not estimable (degenerate: the data are fitted exactly, with no residual variation)")
    if not (math.isfinite(est) and math.isfinite(se)) or se <= 0 or df < 1:
        raise ValueError("regression is not estimable (degenerate variance or too few clusters)")
    t = est / se
    p = float(2 * stats.t.sf(abs(t), df))
    crit = float(stats.t.ppf(1 - (1 - level) / 2, df))
    return est, se, df, p, Interval(estimate=est, lower=est - crit * se, upper=est + crit * se, level=level)


def _long_rows(daily: pd.DataFrame, weights: Literal["sessions", "none"]):
    """Stack comparison then focal rows for the usable days: y, w, treated, date index, date codes."""
    d = daily[daily["usable"].to_numpy(dtype=bool)]
    t = len(d)
    y = np.concatenate([d["rate_c"].to_numpy(float), d["rate_f"].to_numpy(float)])
    w = np.concatenate([d["s_c"].to_numpy(float), d["s_f"].to_numpy(float)]) if weights == "sessions" else None
    treated = np.concatenate([np.zeros(t), np.ones(t)])
    codes = np.concatenate([np.arange(t), np.arange(t)])
    return d.index, y, w, treated, codes


# --------------------------------------------------------------------------- estimate_did


class DidResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    status: Literal["ok", "insufficient"]
    reason: str | None = None
    event_date: Date
    effect_pp: Interval | None = Field(None, description="Differential effect (focal minus comparison change), pp.")
    std_error_pp: float | None = None
    p_value: float | None = None
    degrees_of_freedom: float | None = None
    cov: Cov
    weights: Literal["sessions", "none"]
    control_valid: bool
    n_days_pre: int
    n_days_post: int
    n_obs: int
    focal_sessions_post: int
    comparison_sessions_post: int
    excluded_dates: list[Date]
    assumptions: list[str]


DID_ASSUMPTIONS = (
    (
        "Parallel trends: without the event the focal and comparison groups would have moved alike "
        "(checked on the pre-period, not proven)."
    ),
    (
        "The comparison group was not affected by the event; the estimate is a differential effect, equal to "
        "the focal segment's own effect only if that holds."
    ),
    (
        "No other change hit the focal segment differently at about the same time (unlogged releases, "
        "traffic-mix shifts); a date coincidence is not a cause."
    ),
    "The effect is constant over the post period and across the focal segment.",
)


def _insufficient(onset: Date, reason: str, cov: Cov, weights, control_valid: bool, panel: Panel, **counts) -> DidResult:
    return DidResult(
        status="insufficient", reason=reason, event_date=onset, cov=cov, weights=weights,
        control_valid=control_valid, excluded_dates=panel.excluded_dates, assumptions=list(DID_ASSUMPTIONS),
        n_days_pre=counts.get("n_pre", 0), n_days_post=counts.get("n_post", 0), n_obs=counts.get("n_obs", 0),
        focal_sessions_post=counts.get("f_post", 0), comparison_sessions_post=counts.get("c_post", 0),
    )


def did_on_panel(
    panel: Panel,
    event_date: Date | str,
    *,
    cov: Cov = "hc1",
    weights: Literal["sessions", "none"] = "sessions",
    level: float = 0.95,
    min_days_pre: int = 7,
    min_days_post: int = 3,
) -> DidResult:
    """DiD on an existing panel. ``event_date`` is the first treated day (event date plus any lag)."""
    if cov not in ("cluster", "hc1"):
        raise ValueError("cov must be 'cluster' or 'hc1'")
    if weights not in ("sessions", "none"):
        raise ValueError("weights must be 'sessions' or 'none'")
    if not 0 < level < 1:
        raise ValueError("level must be in (0, 1)")
    onset = pd.Timestamp(event_date)
    daily = panel.daily
    use = daily[daily["usable"].to_numpy(dtype=bool)]
    pre, post = use[use.index < onset], use[use.index >= onset]
    counts = {
        "n_pre": len(pre), "n_post": len(post), "n_obs": 2 * len(use),
        "f_post": int(post["s_f"].sum()), "c_post": int(post["s_c"].sum()),
    }
    control_valid = bool(len(use) > 0 and (daily["s_c"] > 0).any())
    if not control_valid:
        return _insufficient(onset.date(), "no valid comparison group: it has no sessions on usable days", cov, weights, False, panel, **counts)
    if len(pre) < min_days_pre or len(post) < min_days_post:
        return _insufficient(
            onset.date(),
            f"too few usable days: {len(pre)} before and {len(post)} from the event date "
            f"(need {min_days_pre} and {min_days_post})", cov, weights, True, panel, **counts,
        )
    index, y, w, treated, codes = _long_rows(daily, weights)
    t = len(index)
    post_flag = (index >= onset).astype(float)
    dummies = np.zeros((2 * t, t))
    dummies[np.arange(2 * t), codes] = 1.0
    X = np.column_stack([treated, treated * np.concatenate([post_flag, post_flag]), dummies])
    try:
        _, se, df, p, ci = _wls(y * 100, X, w, codes, cov, 1, level)
    except ValueError as exc:
        return _insufficient(onset.date(), str(exc), cov, weights, True, panel, **counts)
    return DidResult(
        status="ok", event_date=onset.date(), effect_pp=ci, std_error_pp=se, p_value=p, degrees_of_freedom=df,
        cov=cov, weights=weights, control_valid=True, excluded_dates=panel.excluded_dates,
        assumptions=list(DID_ASSUMPTIONS), **{k: counts[k2] for k, k2 in
        (("n_days_pre", "n_pre"), ("n_days_post", "n_post"), ("n_obs", "n_obs"),
         ("focal_sessions_post", "f_post"), ("comparison_sessions_post", "c_post"))},
    )


def estimate_did(
    metrics: pd.DataFrame,
    focal: SegmentSpec | Sequence[SegmentSpec],
    event_date: Date | str,
    *,
    comparison: SegmentSpec | Sequence[SegmentSpec] | None = None,
    events: pd.DataFrame | None = None,
    quality: DataQualityReport | None = None,
    apply_exclusions: bool = True,
    cov: Cov = "hc1",
    weights: Literal["sessions", "none"] = "sessions",
    level: float = 0.95,
) -> DidResult:
    """Differential effect of the focal segment from ``event_date`` on, in percentage points.

    Reports the estimate with a ``level`` interval and the sample size, or ``status="insufficient"`` with a
    reason when there is no valid comparison group or too few usable days (never a partial estimate).
    """
    panel = build_panel(metrics, focal, comparison=comparison, events=events, quality=quality, apply_exclusions=apply_exclusions)
    return did_on_panel(panel, event_date, cov=cov, weights=weights, level=level)


# --------------------------------------------------------------------------- pre-trends


class PreTrendResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    status: CheckStatus
    reason: str | None = None
    slope_difference_pp_per_day: Interval | None = None
    p_value: float | None = None
    n_days: int
    onset: Date
    criterion: str = (
        f"pass if p > {PRETREND_PASS_P:g}, borderline if {PRETREND_FAIL_P:g} <= p <= {PRETREND_PASS_P:g}, "
        f"fail if p < {PRETREND_FAIL_P:g}; needs {MIN_PRE_DAYS_FOR_TREND}+ pre-period days"
    )


def pretrend_status(p: float) -> CheckStatus:
    """Prespecified criterion: pass if p > 0.10, fail if p < 0.05, borderline in between (inclusive)."""
    return "pass" if p > PRETREND_PASS_P else ("fail" if p < PRETREND_FAIL_P else "borderline")


def pre_trends_on_panel(
    panel: Panel, event_date: Date | str, *, cov: Cov = "hc1", weights: Literal["sessions", "none"] = "sessions", level: float = 0.95
) -> PreTrendResult:
    onset = pd.Timestamp(event_date)
    pre_panel = panel.model_copy(update={"daily": panel.daily[panel.daily.index < onset]})
    index, y, w, treated, codes = _long_rows(pre_panel.daily, weights)
    n = len(index)
    if n < MIN_PRE_DAYS_FOR_TREND:
        return PreTrendResult(status="not_evaluable", reason=f"only {n} usable pre-period days (need {MIN_PRE_DAYS_FOR_TREND})", n_days=n, onset=onset.date())
    elapsed = np.asarray((index - index[0]).days, dtype=float)  # calendar days, so missing days do not compress the slope
    t = np.concatenate([elapsed, elapsed])
    t = t - t.mean()
    X = np.column_stack([np.ones(2 * n), treated, t, treated * t])
    try:
        _, _, _, p, ci = _wls(y * 100, X, w, codes, cov, 3, level)
    except ValueError as exc:
        return PreTrendResult(status="not_evaluable", reason=str(exc), n_days=n, onset=onset.date())
    return PreTrendResult(status=pretrend_status(p), slope_difference_pp_per_day=ci, p_value=p, n_days=n, onset=onset.date())


def check_pre_trends(
    metrics: pd.DataFrame,
    focal: SegmentSpec | Sequence[SegmentSpec],
    event_date: Date | str,
    *,
    comparison: SegmentSpec | Sequence[SegmentSpec] | None = None,
    events: pd.DataFrame | None = None,
    quality: DataQualityReport | None = None,
    apply_exclusions: bool = True,
    cov: Cov = "hc1",
    weights: Literal["sessions", "none"] = "sessions",
) -> PreTrendResult:
    """Pre-period ``conversion ~ treated * t``: is the focal segment already trending away from the comparison?"""
    panel = build_panel(metrics, focal, comparison=comparison, events=events, quality=quality, apply_exclusions=apply_exclusions)
    return pre_trends_on_panel(panel, event_date, cov=cov, weights=weights)


# --------------------------------------------------------------------------- placebo


class PlaceboEffect(BaseModel):
    model_config = ConfigDict(frozen=True)

    fake_date: Date
    effect_pp: Interval
    p_value: float
    p_value_holm: float


class PlaceboResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    status: CheckStatus
    reason: str | None = None
    placebos: list[PlaceboEffect]
    n_attempted: int
    n_evaluated: int
    onset: Date
    criterion: str = (
        f"fail if any Holm-adjusted placebo p < {PLACEBO_ALPHA:g}; fake dates need {MIN_DAYS_BEFORE_PLACEBO}+ "
        f"days before and {MIN_DAYS_AFTER_PLACEBO}+ after inside the pre-period"
    )


def holm_adjust(p_values: Sequence[float]) -> list[float]:
    """Holm step-down adjusted p-values, in input order (monotone, capped at 1)."""
    p = np.asarray(list(p_values), dtype=float)
    if p.size == 0:
        return []
    order = np.argsort(p, kind="stable")
    m = len(p)
    adj = np.minimum(1.0, np.maximum.accumulate((m - np.arange(m)) * p[order]))
    out = np.empty(m)
    out[order] = adj
    return [float(x) for x in out]


def placebo_dates(pre_days: pd.DatetimeIndex, n: int = N_PLACEBOS) -> list[Date]:
    """A fixed grid of fake event dates inside the pre-period (no randomness)."""
    k = len(pre_days)
    lo, hi = MIN_DAYS_BEFORE_PLACEBO, k - MIN_DAYS_AFTER_PLACEBO
    if hi < lo or n < 1:
        return []
    positions = sorted({round(lo + (hi - lo) * (i + 1) / (n + 1)) for i in range(n)}) if hi > lo else [lo]
    return [pre_days[p].date() for p in positions if lo <= p <= hi]


def placebo_on_panel(
    panel: Panel,
    event_date: Date | str,
    *,
    cov: Cov = "hc1",
    weights: Literal["sessions", "none"] = "sessions",
    level: float = 0.95,
    dates: Sequence[Date | str] | None = None,
) -> PlaceboResult:
    onset = pd.Timestamp(event_date)
    pre_panel = panel.model_copy(update={"daily": panel.daily[panel.daily.index < onset]})
    pre_days = pre_panel.usable_days
    fakes = [pd.Timestamp(d).date() for d in dates] if dates is not None else placebo_dates(pre_days)
    if not fakes:
        return PlaceboResult(
            status="not_evaluable", reason=f"pre-period too short for a placebo date ({len(pre_days)} usable days)",
            placebos=[], n_attempted=0, n_evaluated=0, onset=onset.date(),
        )
    results: list[tuple[Date, Interval, float]] = []
    for fake in fakes:
        r = did_on_panel(pre_panel, fake, cov=cov, weights=weights, level=level,
                         min_days_pre=MIN_DAYS_BEFORE_PLACEBO, min_days_post=MIN_DAYS_AFTER_PLACEBO)
        if r.status == "ok":
            assert r.effect_pp is not None and r.p_value is not None
            results.append((fake, r.effect_pp, r.p_value))
    if not results:
        return PlaceboResult(status="not_evaluable", reason="no placebo date could be estimated", placebos=[],
                             n_attempted=len(fakes), n_evaluated=0, onset=onset.date())
    adj = holm_adjust([p for _, _, p in results])
    placebos = [PlaceboEffect(fake_date=d, effect_pp=ci, p_value=p, p_value_holm=a) for (d, ci, p), a in zip(results, adj, strict=True)]
    complete = len(results) == len(fakes)
    reason = None if complete else f"{len(fakes) - len(results)} of {len(fakes)} placebo dates could not be estimated"
    if any(a < PLACEBO_ALPHA for a in adj):
        status: CheckStatus = "fail"  # a failure among the evaluated dates stands
    elif not complete:
        status = "not_evaluable"  # an unevaluated required placebo is incomplete, never a pass
    else:
        status = "pass"
    return PlaceboResult(status=status, reason=reason, placebos=placebos, n_attempted=len(fakes), n_evaluated=len(results), onset=onset.date())


def run_placebo(
    metrics: pd.DataFrame,
    focal: SegmentSpec | Sequence[SegmentSpec],
    event_date: Date | str,
    *,
    comparison: SegmentSpec | Sequence[SegmentSpec] | None = None,
    events: pd.DataFrame | None = None,
    quality: DataQualityReport | None = None,
    apply_exclusions: bool = True,
    cov: Cov = "hc1",
    weights: Literal["sessions", "none"] = "sessions",
    dates: Sequence[Date | str] | None = None,
) -> PlaceboResult:
    """DiD at fake event dates inside the pre-period; a real-looking effect there is evidence against the design."""
    panel = build_panel(metrics, focal, comparison=comparison, events=events, quality=quality, apply_exclusions=apply_exclusions)
    return placebo_on_panel(panel, event_date, cov=cov, weights=weights, dates=dates)


# --------------------------------------------------------------------------- projection


class ImpactResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    status: Literal["ok", "unavailable"]
    reason: str | None = None
    overall_pp: Interval | None = Field(None, description="Effect on the overall conversion rate over the post period, pp.")
    orders_change: Interval | None = Field(None, description="Signed change in orders over the post period (negative = lost).")
    lost_orders: Interval | None = Field(None, description="Orders lost (positive) over the post period; negative if orders were gained.")
    focal_share_post: float | None = None
    focal_sessions_post: int
    total_sessions_post: int
    n_days_post: int
    assumptions: list[str]


def project_impact(did: DidResult, panel: Panel) -> ImpactResult:
    """Scale a segment's differential effect to the overall metric and to orders over the post period.

    ``overall = effect * focal share of all post-period sessions`` (the whole population, including traffic in neither group) and ``orders change = effect * focal post
    sessions``; the DiD interval is scaled, so ``lower <= estimate <= upper`` is preserved. The post-period
    traffic is treated as known: only the DiD estimate carries uncertainty.
    """
    assumptions = [
        (
            "Assumes the comparison group was unaffected by the event; otherwise the differential effect "
            "understates (or overstates) the event's total effect on the overall metric and on orders."
        ),
        "Assumes the effect was constant over the post period and across the focal segment.",
        "Uncertainty reflects the DiD estimate only; post-period traffic is taken as observed.",
    ]
    if did.status != "ok" or did.effect_pp is None:
        return ImpactResult(status="unavailable", reason=did.reason or "no DiD estimate", focal_sessions_post=0,
                            total_sessions_post=0, n_days_post=did.n_days_post, assumptions=assumptions)
    onset = pd.Timestamp(did.event_date)
    use = panel.daily[panel.daily["usable"].to_numpy(dtype=bool)]
    post = use[use.index >= onset]
    f_post = float(post["s_f"].sum())
    total = float(post["s_all"].sum())  # the whole population's traffic, also when an explicit comparison group is used
    if total <= 0 or f_post <= 0:
        return ImpactResult(status="unavailable", reason="no post-period traffic", focal_sessions_post=int(f_post),
                            total_sessions_post=int(total), n_days_post=len(post), assumptions=assumptions)
    share = f_post / total

    def scale(iv: Interval, k: float) -> Interval:
        return Interval(estimate=iv.estimate * k, lower=iv.lower * k, upper=iv.upper * k, level=iv.level)

    eff = did.effect_pp
    orders = scale(eff, f_post / 100.0)
    lost = scale(eff, -f_post / 100.0)
    lost = Interval(estimate=lost.estimate, lower=min(lost.lower, lost.upper), upper=max(lost.lower, lost.upper), level=lost.level)
    return ImpactResult(
        status="ok", overall_pp=scale(eff, share), orders_change=orders, lost_orders=lost, focal_share_post=share,
        focal_sessions_post=int(f_post), total_sessions_post=int(total), n_days_post=len(post), assumptions=assumptions,
    )
