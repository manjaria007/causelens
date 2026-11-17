"""``decompose_change``: where did the conversion change happen? (Requirement 4)

For a before/after comparison the pooled conversion ``R = orders / sessions`` changes by
``dR = R1 - R0``. For any partition of the traffic into segments (each value of one dimension, or
each combination of two dimensions) the change is split exactly into, per segment ``s``:

    rate effect   = w_bar_s * (r1_s - r0_s)               its conversion changed
    mix effect    = (w1_s - w0_s) * (r_bar_s - R_bar)     its share of traffic changed

with ``w`` the segment's share of the window's sessions, ``r`` its conversion, and bars denoting
the midpoint of the two windows (``w_bar = (w0 + w1) / 2`` and so on). The midpoint weights split the
rate x mix interaction evenly instead of assigning it to one side, so the result does not depend on
which window is called "base". Centring the mix effect on ``R_bar`` means a segment that gains
traffic only moves the overall rate if its conversion differs from the average. Because shares sum
to 1 in each window, ``sum(w1 - w0) = 0``, and

    sum(rate effects) + sum(mix effects) = R1 - R0     (an accounting identity).

The **residual** reported per partition is ``(R1 - R0) - (sum of rate + sum of mix)``, computed from
independently derived totals. It is numerically zero (checked within ``1e-9`` pp); it exists so the
reconciliation is *shown*, not assumed. A segment present in only one window has no observable rate
change: its rate effect is 0 and its whole contribution is a mix effect.

**Overlapping partitions are kept separate.** ``device`` and ``device x browser`` describe the same
sessions, so their contributions must never be added together. Each partition has its own segments,
closure, ranking, explaining set and (optionally) its own FDR family.

**Tests.** Each segment with sessions in both windows is tested for ``H0: conversion unchanged``.
Sessions within a day are not independent draws (weekday effects, day-level noise), so the default test
treats **days as the sampling units**: each window's pooled rate gets a ratio-estimator variance from its
daily counts and the difference is tested with a Welch t statistic and Welch degrees of freedom, the same
method as ``confirm_change``. Estimating variance from few days is accounted for by the t distribution, so
the test is calibrated even with a week per window. The plain binomial two-proportion test is always
reported next to it (and is available as ``inference="binomial"``).

A segment is only tested when the normal approximation can be trusted: every expected cell of the pooled
before/after table (orders and non-orders in each window) must be at least 5. Sparse segments get no
p-value, q-value or significance claim and are left out of the FDR family; Fisher's exact p-value is shown
for information only. P-values feed Benjamini-Hochberg q-values, over every reliably tested segment in all
partitions by default.

**Localization.** The ranked explaining set is the fewest segments reaching the target share of the change.
A *localization* additionally requires the set to carry little traffic, so a traffic-limited search over
combinations of positively contributing segments (exhaustive up to ``MAX_SEARCH_ATOMS`` of them) looks for
the narrowest qualifying set rather than only testing the ranked prefix against the limit.

Task 2's default exclusions are applied to whole days (so segment mix is not distorted) and the
comparison windows and excluded dates are disclosed. This tool says where the change is concentrated,
never why.
"""
from __future__ import annotations

import itertools
import math
from collections.abc import Sequence
from datetime import date as Date
from typing import Literal

import numpy as np
import pandas as pd
from pydantic import BaseModel, ConfigDict, Field
from scipy import stats

from causelens.analytics.confirm import Interval
from causelens.analytics.data_quality import (
    DataQualityReport,
    DetectorParams,
    check_data_quality,
)
from causelens.analytics.loaders import DIMENSIONS, validate_metrics
from causelens.analytics.series import (
    ExclusionDisclosure,
    build_disclosure,
    prepare_daily_series,
)

Window = tuple[Date | str, Date | str]

#: Reconciliation tolerance in percentage points (Requirement 4.3: "within rounding").
CLOSURE_TOLERANCE_PP = 1e-9
#: Smallest expected count in any cell of the pooled before/after 2x2 table for the normal approximation.
MIN_EXPECTED_COUNT = 5.0
#: Fewest days a window needs for a day-level variance (a variance needs two observations).
MIN_DAYS_FOR_DAY_LEVEL = 2
#: Largest number of positive-contribution segment groups searched exhaustively per partition (2**n subsets).
MAX_SEARCH_ATOMS = 12
#: Net changes smaller than this (pp) are treated as zero when expressing shares of the change.
NO_CHANGE_PP = 1e-9


# --------------------------------------------------------------------------- statistics


def benjamini_hochberg(p_values: Sequence[float]) -> list[float]:
    """Benjamini-Hochberg adjusted q-values, in the input order.

    ``q_(i) = min_{j >= i} p_(j) * m / j`` over the ascending order statistics, capped at 1. Ties are
    handled by the cumulative minimum, so equal p-values get equal q-values. Raises ``ValueError``
    for values outside [0, 1] or NaN.
    """
    p = np.asarray(list(p_values), dtype=float)
    if p.size == 0:
        return []
    if np.any(np.isnan(p)) or np.any(p < 0) or np.any(p > 1):
        raise ValueError("p-values must lie in [0, 1]")
    m = len(p)
    order = np.argsort(p, kind="stable")
    scaled = p[order] * m / np.arange(1, m + 1)
    q_sorted = np.minimum(np.minimum.accumulate(scaled[::-1])[::-1], 1.0)
    q = np.empty(m)
    q[order] = q_sorted
    return [float(x) for x in q]


class TwoProportionResult(BaseModel):
    """Plain binomial two-proportion comparison of a segment's conversion before and after."""

    model_config = ConfigDict(frozen=True)

    method: str = "two_proportion_z_pooled"
    interval_method: str = "newcombe_hybrid_score"
    count_pre: int
    n_pre: int
    count_post: int
    n_post: int
    rate_pre: float
    rate_post: float
    change_pp: Interval = Field(description="Post minus pre conversion, in percentage points.")
    z_statistic: float | None = Field(description="None when the pooled rate is 0 or 1 (no variance).")
    p_value: float = Field(description="Two-sided plain binomial p-value (sessions as independent trials).")
    expected_min_count: float = Field(
        description="Smallest expected cell count of the pooled before/after table under the null."
    )
    small_sample: bool = Field(
        description=f"expected_min_count < {MIN_EXPECTED_COUNT:g}: the normal approximation is unreliable."
    )


def _wilson(p: float, n: float, z: float) -> tuple[float, float]:
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return centre - half, centre + half


def two_proportion_test(
    count_pre: int,
    n_pre: int,
    count_post: int,
    n_post: int,
    *,
    level: float = 0.95,
) -> TwoProportionResult:
    """Pooled two-proportion z-test and Newcombe interval for ``p_post - p_pre``.

    Sessions are treated as independent trials, so day-to-day variation is ignored and the p-value is
    optimistic for real traffic; ``day_level_test`` is the calibrated alternative. A pooled rate of
    exactly 0 or 1 has no variance: ``z`` is ``None`` and ``p = 1``. ``small_sample`` is true when the
    smallest expected cell count of the pooled 2 x 2 table (orders and non-orders in each window) is
    below 5, where the normal approximation should not be trusted.
    """
    if not (0 < level < 1):
        raise ValueError("level must be in (0, 1)")
    for name, c, n in (("pre", count_pre, n_pre), ("post", count_post, n_post)):
        if n <= 0 or c < 0 or c > n:
            raise ValueError(f"{name}: need 0 <= count <= n and n > 0 (got count={c}, n={n})")

    p0, p1 = count_pre / n_pre, count_post / n_post
    diff = p1 - p0
    pooled = (count_pre + count_post) / (n_pre + n_post)
    pq = pooled * (1 - pooled)
    if pq <= 0:
        z = None
        p_value = 1.0
    else:
        z = diff / math.sqrt(pq * (1 / n_pre + 1 / n_post))
        p_value = float(2 * stats.norm.sf(abs(z)))

    zc = float(stats.norm.ppf(1 - (1 - level) / 2))
    l0, u0 = _wilson(p0, n_pre, zc)
    l1, u1 = _wilson(p1, n_post, zc)
    lower = diff - math.sqrt((p1 - l1) ** 2 + (u0 - p0) ** 2)
    upper = diff + math.sqrt((u1 - p1) ** 2 + (p0 - l0) ** 2)
    expected_min = min(n_pre * pooled, n_pre * (1 - pooled), n_post * pooled, n_post * (1 - pooled))
    return TwoProportionResult(
        count_pre=count_pre,
        n_pre=n_pre,
        count_post=count_post,
        n_post=n_post,
        rate_pre=p0,
        rate_post=p1,
        change_pp=Interval(estimate=100 * diff, lower=100 * lower, upper=100 * upper, level=level),
        z_statistic=z,
        p_value=p_value,
        expected_min_count=float(expected_min),
        small_sample=bool(expected_min < MIN_EXPECTED_COUNT),
    )


def fisher_exact_p(count_pre: int, n_pre: int, count_post: int, n_post: int) -> float:
    """Two-sided Fisher exact p-value for the pooled 2 x 2 table (independent sessions assumed)."""
    table = [[count_pre, n_pre - count_pre], [count_post, n_post - count_post]]
    return float(stats.fisher_exact(table).pvalue)


class DayLevelResult(BaseModel):
    """Difference of pooled rates with days as the sampling units (Welch t on a ratio estimator)."""

    model_config = ConfigDict(frozen=True)

    method: str = "day_level_welch_t_on_pooled_rate"
    change_pp: Interval = Field(description="Post minus pre pooled conversion, in percentage points.")
    std_error_pp: float
    t_statistic: float | None = Field(description="None when both windows have no day-to-day variation.")
    degrees_of_freedom: float | None
    p_value: float
    n_days_pre: int
    n_days_post: int
    design_effect_pre: float | None = Field(
        description="Day-level variance of the pooled rate / binomial variance (1 = sessions act as "
        "independent trials; None when the rate is 0 or 1 or there is no variation)."
    )
    design_effect_post: float | None


def _day_level_core(
    sessions_pre: np.ndarray,
    orders_pre: np.ndarray,
    sessions_post: np.ndarray,
    orders_post: np.ndarray,
    level: float,
) -> dict[str, np.ndarray]:
    """Vectorised day-level test. Inputs have shape (segments, days); every segment needs sessions in
    both windows. Days are the units, so a segment's absent days count as zero-session days.

    For each window the pooled rate ``R = sum(orders) / sum(sessions)`` has the ratio-estimator variance
    ``d / (d - 1) * sum((orders_i - R * sessions_i)**2) / (sum sessions)**2`` over its ``d`` days. The
    difference is tested with ``t = dR / sqrt(v0 + v1)`` on Welch-Satterthwaite degrees of freedom.
    A relative standard deviation under 1e-9 counts as no variation (float noise, not data).
    """
    d0, d1 = sessions_pre.shape[1], sessions_post.shape[1]
    s0, o0 = sessions_pre.sum(axis=1), orders_pre.sum(axis=1)
    s1, o1 = sessions_post.sum(axis=1), orders_post.sum(axis=1)
    r0, r1 = o0 / s0, o1 / s1
    v0 = d0 / (d0 - 1) * ((orders_pre - r0[:, None] * sessions_pre) ** 2).sum(axis=1) / s0**2
    v1 = d1 / (d1 - 1) * ((orders_post - r1[:, None] * sessions_post) ** 2).sum(axis=1) / s1**2
    v0 = np.where(v0 <= 1e-18 * r0**2, 0.0, v0)
    v1 = np.where(v1 <= 1e-18 * r1**2, 0.0, v1)
    var = v0 + v1
    flat = var <= 0
    diff = r1 - r0
    se = np.sqrt(var)
    safe_var = np.where(flat, 1.0, var)
    with np.errstate(divide="ignore", invalid="ignore"):
        df = np.where(
            flat, 1.0, safe_var**2 / (v0**2 / (d0 - 1) + v1**2 / (d1 - 1)),
        )
        t = np.where(flat, 0.0, diff / np.where(flat, 1.0, se))
    p = 2 * stats.t.sf(np.abs(t), df)
    scale = np.maximum(np.maximum(np.abs(r0), np.abs(r1)), 1e-300)
    p = np.where(flat, np.where(np.abs(diff) <= 1e-9 * scale, 1.0, 0.0), p)
    crit = stats.t.ppf(1 - (1 - level) / 2, df)
    half = np.where(flat, 0.0, crit * se)
    with np.errstate(divide="ignore", invalid="ignore"):
        deff0 = np.where((r0 > 0) & (r0 < 1) & (v0 > 0), v0 / (r0 * (1 - r0) / s0), np.nan)
        deff1 = np.where((r1 > 0) & (r1 < 1) & (v1 > 0), v1 / (r1 * (1 - r1) / s1), np.nan)
    return {
        "diff": diff, "lower": diff - half, "upper": diff + half, "se": se, "t": np.where(flat, np.nan, t),
        "df": np.where(flat, np.nan, df), "p": p, "deff0": deff0, "deff1": deff1,
    }


def _day_level_result(core: dict[str, np.ndarray], i: int, level: float, d0: int, d1: int) -> DayLevelResult:
    def num(key: str) -> float | None:
        v = float(core[key][i])
        return None if math.isnan(v) else v

    return DayLevelResult(
        change_pp=Interval(
            estimate=100 * float(core["diff"][i]),
            lower=100 * float(core["lower"][i]),
            upper=100 * float(core["upper"][i]),
            level=level,
        ),
        std_error_pp=100 * float(core["se"][i]),
        t_statistic=num("t"),
        degrees_of_freedom=num("df"),
        p_value=float(core["p"][i]),
        n_days_pre=d0,
        n_days_post=d1,
        design_effect_pre=num("deff0"),
        design_effect_post=num("deff1"),
    )


def day_level_test(
    sessions_pre: Sequence[float],
    orders_pre: Sequence[float],
    sessions_post: Sequence[float],
    orders_post: Sequence[float],
    *,
    level: float = 0.95,
) -> DayLevelResult:
    """Test a change in pooled conversion using days as the sampling units.

    Each argument is one value per day (a segment's absent days should be given as zeros). Needs at least
    two days in each window and some sessions in each. Unlike the plain binomial test this reflects
    day-to-day variation, and with few days the t distribution widens the p-value and interval to match.
    """
    if not (0 < level < 1):
        raise ValueError("level must be in (0, 1)")
    arrays = [np.asarray(a, dtype=float) for a in (sessions_pre, orders_pre, sessions_post, orders_post)]
    if arrays[0].shape != arrays[1].shape or arrays[2].shape != arrays[3].shape:
        raise ValueError("sessions and orders must have one value per day in each window")
    d0, d1 = len(arrays[0]), len(arrays[2])
    if d0 < MIN_DAYS_FOR_DAY_LEVEL or d1 < MIN_DAYS_FOR_DAY_LEVEL:
        raise ValueError(f"need at least {MIN_DAYS_FOR_DAY_LEVEL} days in each window (got {d0} and {d1})")
    if arrays[0].sum() <= 0 or arrays[2].sum() <= 0:
        raise ValueError("each window needs some sessions")
    if np.any(arrays[1] < 0) or np.any(arrays[3] < 0) or np.any(arrays[1] > arrays[0]) or np.any(arrays[3] > arrays[2]):
        raise ValueError("orders must be between 0 and sessions on every day")
    core = _day_level_core(*(a[None, :] for a in arrays), level)
    return _day_level_result(core, 0, level, d0, d1)


# --------------------------------------------------------------------------- result models


class ComparisonWindow(BaseModel):
    """A comparison window as actually used, after exclusions."""

    model_config = ConfigDict(frozen=True)

    name: Literal["pre", "post"]
    start: Date
    end: Date
    n_days_requested: int
    n_days_used: int
    n_days_excluded: int = Field(description="Flagged days removed by default exclusions.")
    excluded_dates: list[Date]
    n_days_missing: int = Field(description="Dates with no data (or no sessions).")
    sessions: int
    orders: int
    conversion: float | None = Field(description="Pooled orders / sessions as a fraction; None without sessions.")


class RateChangeTest(BaseModel):
    """Test of a segment's conversion change, with what the p-value rests on stated.

    ``reliable`` is false when a cell of the pooled before/after table has fewer than 5 expected counts:
    then there is no p-value, no q-value and no significance claim, and the segment is left out of the
    FDR family. ``p_value_exact`` (Fisher) is shown for information only in that case.
    """

    model_config = ConfigDict(frozen=True)

    basis: Literal["day_level_welch_t", "binomial_z", "none"] = Field(
        description="What p_value comes from: days as units, plain binomial, or nothing (unreliable)."
    )
    change_pp: Interval = Field(description="Interval consistent with the basis, in percentage points.")
    binomial: TwoProportionResult
    day_level: DayLevelResult | None = Field(description="None for unreliable segments or windows under 2 days.")
    reliable: bool
    note: str | None = Field(description="Why the test is unreliable or fell back, if it did.")
    p_value: float | None
    p_value_exact: float | None = Field(description="Fisher exact p-value; only computed for unreliable segments.")
    q_value: float | None = Field(description="Benjamini-Hochberg q in the family chosen by `fdr_family`.")
    q_value_partition: float | None = Field(description="BH q among the reliably tested segments of this partition.")
    q_value_scan: float | None = Field(description="BH q among every reliably tested segment across all partitions.")
    significant: bool = Field(description="q_value < alpha: the rate change is distinguishable from noise.")


class SegmentEffect(BaseModel):
    model_config = ConfigDict(frozen=True)

    labels: dict[str, str]
    label: str
    status: Literal["both", "only_pre", "only_post"]
    sessions_pre: int
    sessions_post: int
    orders_pre: int
    orders_post: int
    conversion_pre: float | None = Field(description="Fraction; None if the segment has no pre sessions.")
    conversion_post: float | None
    share_pre: float = Field(description="Segment's share of the pre window's sessions (fraction).")
    share_post: float
    share_change_pp: float = Field(description="Change in traffic share, in percentage points.")
    rate_change_pp: float | None = Field(description="Post minus pre conversion; None for one-sided segments.")
    rate_effect_pp: float
    mix_effect_pp: float
    contribution_pp: float = Field(description="Rate effect plus mix effect.")
    share_of_change: float | None = Field(
        description="contribution / overall change; None when the overall change is about zero."
    )
    rank: int = Field(description="1 = contributes most in the direction of the overall change; ties share a rank.")
    test: RateChangeTest | None = Field(description="None for segments seen in only one window.")


class ExplainingSet(BaseModel):
    """The smallest set of segments (by rank) whose contributions reach the threshold share."""

    model_config = ConfigDict(frozen=True)

    kind: Literal["ranked_prefix", "traffic_limited_search"] = "ranked_prefix"
    threshold: float
    segments: list[str]
    k: int
    selected_share_of_change: float = Field(description="Sum of selected contributions / overall change.")
    remaining_share_of_change: float = Field(
        description="1 - selected share. Can be negative when the selected set over-explains and the "
        "other segments offset it; it is never forced to a tidy number."
    )
    rate_effect_pp: float
    mix_effect_pp: float
    traffic_share_pre: float
    traffic_share_post: float
    all_selected_significant: bool = Field(
        description="True when every selected segment has a testable, significant rate change."
    )
    note: str


class PartitionResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    dimensions: list[str]
    name: str
    n_segments: int
    n_tests: int = Field(description="Segments with a reliable test (they enter the FDR family).")
    n_unreliable: int = Field(
        description="Segments seen in both windows whose counts are too sparse for the normal approximation."
    )
    segments: list[SegmentEffect] = Field(description="Ordered by rank, then label.")
    sum_rate_effects_pp: float
    sum_mix_effects_pp: float
    residual_pp: float = Field(
        description="Overall change minus (rate + mix effects): the part this partition does not "
        "account for. An accounting identity makes it numerically zero."
    )
    reconciles: bool = Field(description=f"|residual_pp| <= {CLOSURE_TOLERANCE_PP:g} pp.")
    rate_effect_significant_pp: float = Field(
        description="Sum of rate effects of segments whose rate change is significant (q < alpha)."
    )
    rate_effect_not_distinguishable_pp: float = Field(
        description="Sum of rate effects of segments whose change cannot be told from noise, "
        "including segments with no test."
    )
    explaining_set: ExplainingSet | None = Field(
        description="The fewest segments, ranked by contribution, that reach the target share (no traffic limit)."
    )
    explaining_set_unavailable_reason: str | None = None
    local_set: ExplainingSet | None = Field(
        None,
        description="The best set within the traffic limit found by searching combinations of the "
        "positively contributing segments (significant sets first, then fewest segments, then least traffic).",
    )
    local_set_unavailable_reason: str | None = None
    local_search_exhaustive: bool = Field(
        True, description=f"False when more than {MAX_SEARCH_ATOMS} positive contributors limited the search."
    )


class Localization(BaseModel):
    """Pointer to the narrowest explaining set found across the (overlapping) partitions."""

    model_config = ConfigDict(frozen=True)

    partition: str
    segments: list[str]
    selected_share_of_change: float
    traffic_share_post: float
    all_selected_significant: bool
    statement: str


class DecompositionResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    status: Literal["ok", "insufficient_data"]
    metric: str = "conversion"
    pre: ComparisonWindow
    post: ComparisonWindow
    change_pp: float | None = Field(description="Pooled post minus pre conversion, in percentage points.")
    conversion_pre: float | None
    conversion_post: float | None
    weekday_mix_difference: float | None
    partitions: list[PartitionResult]
    localization: Localization | None
    localization_note: str | None = Field(
        None, description="Why no localization is reported (a broad shift, no net change, ...)."
    )
    fdr_family: Literal["scan", "partition"]
    n_tests_scanned: int = Field(description="Segments with a reliable test across all partitions.")
    n_unreliable_tests: int = Field(0, description="Segments too sparse to test, across all partitions.")
    alpha: float
    level: float
    inference: Literal["day_level", "binomial"]
    exclusions: ExclusionDisclosure
    assumptions: list[str]
    summary: str


# --------------------------------------------------------------------------- helpers


def _parse_window(name: str, window: Window) -> tuple[Date, Date]:
    try:
        start, end = (pd.Timestamp(w).date() for w in window)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} window must be (start, end) dates: {exc}") from None
    if start > end:
        raise ValueError(f"{name} window starts ({start}) after it ends ({end})")
    return start, end


def _label(dims: Sequence[str], values: Sequence[str]) -> str:
    return ", ".join(f"{d}={v}" for d, v in zip(dims, values, strict=True))


def _partition_name(dims: Sequence[str]) -> str:
    return " × ".join(dims)


def _fsum(values: Sequence[float]) -> float:
    return math.fsum(values)


def _weekday_gap(pre_days: pd.DatetimeIndex, post_days: pd.DatetimeIndex) -> float:
    def mix(days: pd.DatetimeIndex) -> np.ndarray:
        counts = np.bincount(days.day_of_week, minlength=7).astype(float)
        return counts / counts.sum() if counts.sum() else counts

    return float(0.5 * np.abs(mix(pre_days) - mix(post_days)).sum())


def _window_summary(
    name: Literal["pre", "post"], window: tuple[Date, Date], daily: pd.DataFrame, rows: pd.DataFrame
) -> ComparisonWindow:
    idx = pd.date_range(window[0], window[1], freq="D")
    status = daily["status"].reindex(idx).fillna("outside_data")
    sessions, orders = int(rows["sessions"].sum()), int(rows["orders"].sum())
    return ComparisonWindow(
        name=name,
        start=window[0],
        end=window[1],
        n_days_requested=len(idx),
        n_days_used=int((status == "ok").sum()),
        n_days_excluded=int((status == "excluded").sum()),
        excluded_dates=[d.date() for d in idx[(status == "excluded").to_numpy()]],
        n_days_missing=int(status.isin(["missing", "no_sessions", "outside_data"]).sum()),
        sessions=sessions,
        orders=orders,
        conversion=orders / sessions if sessions > 0 else None,
    )


class _Encoded:
    """Rows of both windows as integer codes, so every partition is a few ``bincount`` calls.

    Counts are integers below 2**53 (enforced by the loader), so float64 sums of them are exact and
    independent of row order. Categories are factorized in sorted order, which makes segment order
    lexicographic in the labels.
    """

    def __init__(self, rows: pd.DataFrame, post_start: Date):
        self.codes: dict[str, np.ndarray] = {}
        self.cats: dict[str, list[str]] = {}
        for dim in DIMENSIONS:
            codes, cats = pd.factorize(rows[dim], sort=True)
            self.codes[dim], self.cats[dim] = codes, [str(c) for c in cats]
        day_codes, days = pd.factorize(rows["date"], sort=True)
        self.day_codes = day_codes
        self.n_days = len(days)
        self.is_post_day = np.asarray(days >= pd.Timestamp(post_start))
        self.n_pre_days = int((~self.is_post_day).sum())
        self.n_post_days = int(self.is_post_day.sum())
        self.sessions = rows["sessions"].to_numpy(dtype=float)
        self.orders = rows["orders"].to_numpy(dtype=float)

    def matrices(self, dims: Sequence[str]) -> tuple[np.ndarray, np.ndarray, list[int]]:
        """Daily sessions and orders per segment: arrays of shape (segments, days), plus the sizes."""
        sizes = [len(self.cats[d]) for d in dims]
        segment = np.ravel_multi_index(tuple(self.codes[d] for d in dims), sizes)
        n_seg = int(np.prod(sizes))
        flat = segment * self.n_days + self.day_codes
        shape = (n_seg, self.n_days)
        sessions = np.bincount(flat, weights=self.sessions, minlength=n_seg * self.n_days).reshape(shape)
        orders = np.bincount(flat, weights=self.orders, minlength=n_seg * self.n_days).reshape(shape)
        return sessions, orders, sizes


def _competition_ranks(keys: list[float]) -> list[int]:
    """Rank 1 = largest key; ties (exactly equal keys) share a rank."""
    return [1 + sum(1 for other in keys if other > k) for k in keys]


# --------------------------------------------------------------------------- main tool


def decompose_change(
    metrics: pd.DataFrame,
    pre_window: Window,
    post_window: Window,
    events: pd.DataFrame | None = None,
    *,
    dimensions: Sequence[str] = DIMENSIONS,
    include_pairs: bool = True,
    quality: DataQualityReport | None = None,
    apply_exclusions: bool = True,
    detector_params: DetectorParams | None = None,
    alpha: float = 0.05,
    level: float = 0.95,
    explain_threshold: float = 0.8,
    max_localized_traffic_share: float = 0.5,
    fdr_family: Literal["scan", "partition"] = "scan",
    inference: Literal["day_level", "binomial"] = "day_level",
    min_days_per_window: int = 7,
) -> DecompositionResult:
    """Decompose the pooled conversion change into per-segment rate and mix effects.

    Partitions are every value of each dimension in ``dimensions`` and (``include_pairs``) every pair
    of those dimensions. Windows are inclusive ``(start, end)`` dates; pre must end before post
    starts. Raises ``ValueError`` for invalid arguments; too little data gives
    ``status="insufficient_data"`` with no estimates.
    """
    dims_in = list(dimensions)
    unknown = [d for d in dims_in if d not in DIMENSIONS]
    if unknown or not dims_in or len(set(dims_in)) != len(dims_in):
        raise ValueError(f"dimensions must be distinct names among {list(DIMENSIONS)} (got {dims_in})")
    if not 1e-12 <= alpha <= 0.5:
        raise ValueError("alpha must be between 1e-12 and 0.5")
    if not 0 < level < 1:
        raise ValueError("level must be in (0, 1)")
    if not 0 < explain_threshold <= 1:
        raise ValueError("explain_threshold must be in (0, 1]")
    if not 0 < max_localized_traffic_share <= 1:
        raise ValueError("max_localized_traffic_share must be in (0, 1]")
    if fdr_family not in ("scan", "partition"):
        raise ValueError("fdr_family must be 'scan' or 'partition'")
    if inference not in ("day_level", "binomial"):
        raise ValueError("inference must be 'day_level' or 'binomial'")
    if min_days_per_window < 1:
        raise ValueError("min_days_per_window must be at least 1")
    pre = _parse_window("pre", pre_window)
    post = _parse_window("post", post_window)
    if pre[1] >= post[0]:
        raise ValueError(f"pre window must end before the post window starts ({pre[1]} >= {post[0]})")

    data = validate_metrics(metrics)
    quality = quality or check_data_quality(data, events, detector_params)
    prepared = prepare_daily_series(data, events, quality=quality, apply_exclusions=apply_exclusions)
    usable_days = prepared.daily.index[prepared.daily["status"] == "ok"]
    rows = data[data["date"].isin(usable_days)]
    pre_rows = rows[(rows["date"] >= pd.Timestamp(pre[0])) & (rows["date"] <= pd.Timestamp(pre[1]))]
    post_rows = rows[(rows["date"] >= pd.Timestamp(post[0])) & (rows["date"] <= pd.Timestamp(post[1]))]

    win_pre = _window_summary("pre", pre, prepared.daily, pre_rows)
    win_post = _window_summary("post", post, prepared.daily, post_rows)
    disclosure = build_disclosure(quality, apply_exclusions, {"pre": pre, "post": post})

    def removed_sentence() -> str:
        if not apply_exclusions:
            return " Flagged days were not excluded (exclusions switched off)."
        parts = [f"{d.isoformat()} (pre)" for d in win_pre.excluded_dates] + [
            f"{d.isoformat()} (post)" for d in win_post.excluded_dates
        ]
        return (
            " Flagged days removed from the windows: " + ", ".join(parts) + "."
            if parts
            else " No flagged days fell inside either window."
        )

    short = [
        f"{w.name} window has {w.n_days_used} usable day(s) of {w.n_days_requested} requested "
        f"({w.n_days_excluded} excluded as flagged, {w.n_days_missing} missing or without sessions)"
        for w in (win_pre, win_post)
        if w.n_days_used < min_days_per_window or w.sessions <= 0
    ]
    if short:
        return DecompositionResult(
            status="insufficient_data",
            pre=win_pre,
            post=win_post,
            change_pp=None,
            conversion_pre=win_pre.conversion,
            conversion_post=win_post.conversion,
            weekday_mix_difference=None,
            partitions=[],
            localization=None,
            localization_note="No decomposition was possible, so nothing is localized.",
            fdr_family=fdr_family,
            n_tests_scanned=0,
            alpha=alpha,
            level=level,
            inference=inference,
            exclusions=disclosure,
            assumptions=[],
            summary=(
                "Insufficient data to decompose the change: "
                + "; ".join(short)
                + f"; at least {min_days_per_window} usable day(s) with sessions are needed in each window."
                + removed_sentence()
                + " No decomposition is reported."
            ),
        )

    n0, n1 = win_pre.sessions, win_post.sessions
    o0, o1 = win_pre.orders, win_post.orders
    r0_all, r1_all = o0 / n0, o1 / n1
    change_pp = 100 * (r1_all - r0_all)
    r_bar_all = (r0_all + r1_all) / 2
    sign = 1.0 if change_pp >= 0 else -1.0
    has_change = abs(change_pp) > NO_CHANGE_PP

    dim_sets: list[tuple[str, ...]] = [(d,) for d in dims_in]
    if include_pairs:
        dim_sets += list(itertools.combinations(dims_in, 2))

    # ---- per-partition accounting and tests
    window_rows = rows[
        ((rows["date"] >= pd.Timestamp(pre[0])) & (rows["date"] <= pd.Timestamp(pre[1])))
        | ((rows["date"] >= pd.Timestamp(post[0])) & (rows["date"] <= pd.Timestamp(post[1])))
    ]
    enc = _Encoded(window_rows, post[0])
    day_level_ok = inference == "day_level" and min(enc.n_pre_days, enc.n_post_days) >= MIN_DAYS_FOR_DAY_LEVEL
    raw: dict[tuple[str, ...], list[dict]] = {}
    for dims in dim_sets:
        daily_s, daily_o, sizes = enc.matrices(dims)
        s_pre, o_pre = daily_s[:, ~enc.is_post_day], daily_o[:, ~enc.is_post_day]
        s_post, o_post = daily_s[:, enc.is_post_day], daily_o[:, enc.is_post_day]
        tot = {
            "s0": s_pre.sum(axis=1), "c0": o_pre.sum(axis=1),
            "s1": s_post.sum(axis=1), "c1": o_post.sum(axis=1),
        }
        both = np.flatnonzero((tot["s0"] > 0) & (tot["s1"] > 0))
        core = (
            _day_level_core(s_pre[both], o_pre[both], s_post[both], o_post[both], level)
            if day_level_ok and len(both)
            else None
        )
        position = {int(seg): i for i, seg in enumerate(both)}
        entries: list[dict] = []
        for seg in np.flatnonzero((tot["s0"] > 0) | (tot["s1"] > 0)):
            codes = np.unravel_index(seg, sizes)
            values = tuple(enc.cats[d][int(c)] for d, c in zip(dims, codes, strict=True))
            s0, s1 = int(tot["s0"][seg]), int(tot["s1"][seg])
            c0, c1 = int(tot["c0"][seg]), int(tot["c1"][seg])
            w0, w1 = s0 / n0, s1 / n1
            r0 = c0 / s0 if s0 > 0 else None
            r1 = c1 / s1 if s1 > 0 else None
            if r0 is not None and r1 is not None:
                status = "both"
                rate_effect = (w0 + w1) / 2 * (r1 - r0)
                r_bar = (r0 + r1) / 2
            else:
                status = "only_post" if r0 is None else "only_pre"
                rate_effect = 0.0
                r_bar = r1 if r0 is None else r0
            mix_effect = (w1 - w0) * (r_bar - r_bar_all)
            entry = {
                "values": values,
                "status": status,
                "s0": s0, "s1": s1, "c0": c0, "c1": c1,
                "w0": w0, "w1": w1, "r0": r0, "r1": r1,
                "rate_pp": 100 * rate_effect,
                "mix_pp": 100 * mix_effect,
                "test": None,
            }
            if status == "both":
                day = (
                    _day_level_result(core, position[int(seg)], level, enc.n_pre_days, enc.n_post_days)
                    if core is not None
                    else None
                )
                entry["test"] = _segment_test(c0, s0, c1, s1, day, inference, level)
            entries.append(entry)
        raw[dims] = entries

    # ---- multiple comparisons: Benjamini-Hochberg over reliable tests, per partition and across the scan
    tested = [
        (dims, i) for dims, es in raw.items() for i, e in enumerate(es)
        if e["test"] is not None and e["test"]["p"] is not None
    ]
    q_scan = benjamini_hochberg([raw[d][i]["test"]["p"] for d, i in tested])
    for (d, i), q in zip(tested, q_scan, strict=True):
        raw[d][i]["q_scan"] = q
    for dims, es in raw.items():
        idx = [i for i, e in enumerate(es) if e["test"] is not None and e["test"]["p"] is not None]
        for i, q in zip(idx, benjamini_hochberg([es[i]["test"]["p"] for i in idx]), strict=True):
            es[i]["q_partition"] = q

    # ---- build partition results
    partitions: list[PartitionResult] = []
    for dims, entries in raw.items():
        keys = [sign * (e["rate_pp"] + e["mix_pp"]) if has_change else abs(e["rate_pp"] + e["mix_pp"]) for e in entries]
        ranks = _competition_ranks(keys)
        order = sorted(range(len(entries)), key=lambda i: (ranks[i], entries[i]["values"]))

        segs: list[SegmentEffect] = []
        for i in order:
            e = entries[i]
            test = None
            if e["test"] is not None:
                t = e["test"]
                q = None
                if t["p"] is not None:
                    q = e["q_scan"] if fdr_family == "scan" else e["q_partition"]
                test = RateChangeTest(
                    basis=t["basis"],
                    change_pp=t["change_pp"],
                    binomial=t["binomial"],
                    day_level=t["day"],
                    reliable=t["reliable"],
                    note=t["note"],
                    p_value=t["p"],
                    p_value_exact=t["p_exact"],
                    q_value=q,
                    q_value_partition=e.get("q_partition"),
                    q_value_scan=e.get("q_scan"),
                    significant=bool(q is not None and q < alpha),
                )
            contribution = e["rate_pp"] + e["mix_pp"]
            segs.append(
                SegmentEffect(
                    labels=dict(zip(dims, e["values"], strict=True)),
                    label=_label(dims, e["values"]),
                    status=e["status"],
                    sessions_pre=e["s0"], sessions_post=e["s1"],
                    orders_pre=e["c0"], orders_post=e["c1"],
                    conversion_pre=e["r0"], conversion_post=e["r1"],
                    share_pre=e["w0"], share_post=e["w1"],
                    share_change_pp=100 * (e["w1"] - e["w0"]),
                    rate_change_pp=None if e["r0"] is None or e["r1"] is None else 100 * (e["r1"] - e["r0"]),
                    rate_effect_pp=e["rate_pp"],
                    mix_effect_pp=e["mix_pp"],
                    contribution_pp=contribution,
                    share_of_change=contribution / change_pp if has_change else None,
                    rank=ranks[i],
                    test=test,
                )
            )

        sum_rate = _fsum([s.rate_effect_pp for s in segs])
        sum_mix = _fsum([s.mix_effect_pp for s in segs])
        residual = change_pp - (sum_rate + sum_mix)
        sig_rate = _fsum([s.rate_effect_pp for s in segs if s.test is not None and s.test.significant])
        noise_rate = _fsum([s.rate_effect_pp for s in segs if s.test is None or not s.test.significant])

        explaining, why_not = _explaining_set(segs, change_pp, has_change, sign, explain_threshold)
        local, local_why, exhaustive = _local_set(
            segs, change_pp, has_change, sign, explain_threshold, max_localized_traffic_share
        )
        partitions.append(
            PartitionResult(
                dimensions=list(dims),
                name=_partition_name(dims),
                n_segments=len(segs),
                n_tests=sum(1 for s in segs if s.test is not None and s.test.reliable),
                n_unreliable=sum(1 for s in segs if s.test is not None and not s.test.reliable),
                segments=segs,
                sum_rate_effects_pp=sum_rate,
                sum_mix_effects_pp=sum_mix,
                residual_pp=residual,
                reconciles=bool(abs(residual) <= CLOSURE_TOLERANCE_PP),
                rate_effect_significant_pp=sig_rate,
                rate_effect_not_distinguishable_pp=noise_rate,
                explaining_set=explaining,
                explaining_set_unavailable_reason=why_not,
                local_set=local,
                local_set_unavailable_reason=local_why,
                local_search_exhaustive=exhaustive,
            )
        )

    localization, localization_note = _localization(
        partitions, change_pp, explain_threshold, max_localized_traffic_share
    )
    pre_days = pd.DatetimeIndex(sorted(set(pre_rows["date"])))
    post_days = pd.DatetimeIndex(sorted(set(post_rows["date"])))
    gap = _weekday_gap(pre_days, post_days) if len(pre_days) and len(post_days) else None

    n_scanned = len(tested)
    n_unreliable = sum(p.n_unreliable for p in partitions)
    n_fallback = sum(
        1 for p in partitions for s in p.segments
        if s.test is not None and s.test.reliable and s.test.basis == "binomial_z"
    )
    assumptions = _assumptions(
        win_pre, win_post, partitions, disclosure, inference, fdr_family, n_scanned, n_unreliable, n_fallback,
        gap, explain_threshold, max_localized_traffic_share,
    )
    summary = _summary(
        change_pp, r0_all, r1_all, win_pre, win_post, partitions, localization, localization_note, n_scanned,
        n_unreliable, fdr_family, inference, level, alpha,
    ) + removed_sentence()

    return DecompositionResult(
        status="ok",
        pre=win_pre,
        post=win_post,
        change_pp=change_pp,
        conversion_pre=r0_all,
        conversion_post=r1_all,
        weekday_mix_difference=gap,
        partitions=partitions,
        localization=localization,
        localization_note=localization_note,
        fdr_family=fdr_family,
        n_tests_scanned=n_scanned,
        n_unreliable_tests=n_unreliable,
        alpha=alpha,
        level=level,
        inference=inference,
        exclusions=disclosure,
        assumptions=assumptions,
        summary=summary,
    )


def _segment_test(
    c0: int, s0: int, c1: int, s1: int, day: DayLevelResult | None, inference: str, level: float
) -> dict:
    """Choose what a segment's p-value rests on, or decline to give one when counts are too sparse."""
    binom = two_proportion_test(c0, s0, c1, s1, level=level)
    if binom.small_sample:
        exact = fisher_exact_p(c0, s0, c1, s1)
        note = (
            f"Too sparse for the normal approximation: the smallest expected count in the pooled before/after "
            f"table is {binom.expected_min_count:.2f} (at least {MIN_EXPECTED_COUNT:g} is needed). No "
            f"significance is claimed. Fisher's exact p = {exact:.3g} is shown for information only; it "
            "assumes independent sessions."
        )
        return {
            "binomial": binom, "day": None, "basis": "none", "change_pp": binom.change_pp,
            "reliable": False, "note": note, "p": None, "p_exact": exact,
        }
    if inference == "day_level" and day is not None:
        return {
            "binomial": binom, "day": day, "basis": "day_level_welch_t", "change_pp": day.change_pp,
            "reliable": True, "note": None, "p": day.p_value, "p_exact": None,
        }
    note = "Plain binomial test: sessions are treated as independent, so the p-value ignores day-to-day variation and is optimistic."
    if inference == "day_level":
        note = "A window has fewer than 2 days, so day-to-day variation cannot be estimated. " + note
    return {
        "binomial": binom, "day": None, "basis": "binomial_z", "change_pp": binom.change_pp,
        "reliable": True, "note": note, "p": binom.p_value, "p_exact": None,
    }


# --------------------------------------------------------------------------- ranking and wording


def _build_set(
    chosen: list[SegmentEffect],
    change_pp: float,
    threshold: float,
    kind: Literal["ranked_prefix", "traffic_limited_search"],
    reached: bool = True,
    traffic_limit: float | None = None,
) -> ExplainingSet:
    total = _fsum([s.contribution_pp for s in chosen])
    share = total / change_pp
    tested_all = all(s.test is not None and s.test.significant for s in chosen)
    traffic_post = _fsum([s.share_post for s in chosen])
    limit_text = f", carrying {traffic_post:.1%} of post-window sessions (limit {traffic_limit:.0%})" if traffic_limit else ""
    note = (
        f"{len(chosen)} segment(s) account for {share:.1%} of the overall change (target {threshold:.0%})"
        + limit_text
        + ("; above 100% because the other segments moved the opposite way" if share > 1 + 1e-9 else "")
        + (
            ""
            if reached
            else "; the target could not be reached even with every segment moving in the direction of the change"
        )
        + (
            "; every selected rate change is distinguishable from noise"
            if tested_all
            else "; not every selected segment has a testable rate change distinguishable from noise "
            "(it may reflect a traffic-mix shift or sparse data)"
        )
        + "."
    )
    return ExplainingSet(
        kind=kind,
        threshold=threshold,
        segments=[s.label for s in chosen],
        k=len(chosen),
        selected_share_of_change=share,
        remaining_share_of_change=1 - share,
        rate_effect_pp=_fsum([s.rate_effect_pp for s in chosen]),
        mix_effect_pp=_fsum([s.mix_effect_pp for s in chosen]),
        traffic_share_pre=_fsum([s.share_pre for s in chosen]),
        traffic_share_post=traffic_post,
        all_selected_significant=tested_all,
        note=note,
    )


def _explaining_set(
    segs: list[SegmentEffect], change_pp: float, has_change: bool, sign: float, threshold: float
) -> tuple[ExplainingSet | None, str | None]:
    """The fewest segments, ranked by contribution in the direction of the change, reaching the target.

    Taking the largest contributions first minimises the number of segments needed, so this is the
    smallest set without any constraint on traffic. Segments with exactly equal ranks are taken together
    so the choice never depends on label order.
    """
    if not has_change:
        return None, (
            "The pooled conversion did not change (to within rounding), so there is no change to explain; "
            "contributions that remain are offsetting movements."
        )
    ordered: list[list[SegmentEffect]] = []
    for s in segs:  # already ordered by rank
        if sign * s.contribution_pp > 0:
            if ordered and ordered[-1][0].rank == s.rank:
                ordered[-1].append(s)
            else:
                ordered.append([s])
    target = threshold * abs(change_pp)
    chosen: list[SegmentEffect] = []
    reached = False
    for group in ordered:
        chosen.extend(group)
        if sign * _fsum([s.contribution_pp for s in chosen]) >= target * (1 - 1e-12):
            reached = True
            break
    if not chosen:
        return None, "No segment moves in the direction of the overall change."
    return _build_set(chosen, change_pp, threshold, "ranked_prefix", reached), None


def _signature(s: SegmentEffect) -> tuple[int, int, int, int]:
    """Segments with the same counts have identical numbers, so they are interchangeable."""
    return (s.sessions_pre, s.sessions_post, s.orders_pre, s.orders_post)


def _local_set(
    segs: list[SegmentEffect],
    change_pp: float,
    has_change: bool,
    sign: float,
    threshold: float,
    max_traffic: float,
) -> tuple[ExplainingSet | None, str | None, bool]:
    """Best set of segments that reaches the target share while carrying at most ``max_traffic``.

    The ranked prefix can fail the traffic limit even though another combination qualifies (a large
    segment may rank first yet carry too much traffic while a smaller one explains nearly as much), so
    this searches combinations of the positively contributing segments instead of only testing the prefix.
    Segments with identical counts are one indivisible group, which keeps the choice independent of
    labels. The search is exhaustive over up to ``MAX_SEARCH_ATOMS`` groups (the largest contributors
    when there are more; the third return value says which). Among qualifying combinations it prefers
    one whose segments all have a distinguishable rate change, then the fewest segments, then the least
    traffic, then the largest contribution.
    """
    if not has_change:
        return None, "The pooled conversion did not change (to within rounding), so there is nothing to localize.", True
    positives = [s for s in segs if sign * s.contribution_pp > 0]
    if not positives:
        return None, "No segment moves in the direction of the overall change.", True

    groups: dict[tuple[int, int, int, int], list[SegmentEffect]] = {}
    for s in positives:
        groups.setdefault(_signature(s), []).append(s)
    atoms = sorted(
        groups.items(),
        key=lambda kv: (-sign * _fsum([m.contribution_pp for m in kv[1]]), kv[0]),
    )
    exhaustive = len(atoms) <= MAX_SEARCH_ATOMS
    members = [m for _, m in atoms[:MAX_SEARCH_ATOMS]]
    n = len(members)
    contribution = np.array([sign * _fsum([s.contribution_pp for s in m]) for m in members])
    traffic = np.array([_fsum([s.share_post for s in m]) for m in members])
    size = np.array([len(m) for m in members])
    untested = np.array([0 if all(s.test is not None and s.test.significant for s in m) else 1 for m in members])

    masks = np.arange(1, 1 << n, dtype=np.int64)
    total_c, total_t = np.zeros(len(masks)), np.zeros(len(masks))
    total_k, total_u = np.zeros(len(masks), dtype=np.int64), np.zeros(len(masks), dtype=np.int64)
    for j in range(n):  # fixed, label-independent accumulation order
        on = ((masks >> j) & 1).astype(bool)
        total_c += np.where(on, contribution[j], 0.0)
        total_t += np.where(on, traffic[j], 0.0)
        total_k += np.where(on, size[j], 0)
        total_u += np.where(on, untested[j], 0)

    target = threshold * abs(change_pp)
    feasible = np.flatnonzero((total_c >= target * (1 - 1e-12)) & (total_t <= max_traffic * (1 + 1e-12)))
    if len(feasible) == 0:
        if exhaustive:
            why = (
                f"No combination of the positively contributing segments carries at most {max_traffic:.0%} of "
                f"post-window sessions while accounting for {threshold:.0%} of the change."
            )
        else:
            why = (
                f"No qualifying combination was found among the {MAX_SEARCH_ATOMS} largest positive "
                "contributors (the search is limited when more segments contribute)."
            )
        return None, why, exhaustive
    order = np.lexsort(
        (masks[feasible], -total_c[feasible], total_t[feasible], total_k[feasible], total_u[feasible] > 0)
    )
    best = int(masks[feasible[order[0]]])
    chosen = [s for j in range(n) if (best >> j) & 1 for s in members[j]]
    chosen.sort(key=lambda s: (s.rank, s.label))
    return _build_set(chosen, change_pp, threshold, "traffic_limited_search", True, max_traffic), None, exhaustive


def _localization(
    partitions: list[PartitionResult], change_pp: float, threshold: float, max_traffic: float
) -> tuple[Localization | None, str | None]:
    """Pick the narrowest traffic-limited set across the (overlapping) partitions, if there is one.

    Each partition's ``local_set`` already comes from a search over combinations, so a qualifying
    combination is never missed because a larger segment ranked first. "Broad shift" is only said when
    that search was exhaustive and found nothing.
    """
    with_sets = [p for p in partitions if p.explaining_set is not None]
    if not with_sets:
        return None, (
            "No segment can be singled out: the pooled conversion did not change (to within rounding) or "
            "no segment moves in the direction of the change."
        )
    local = [p for p in partitions if p.local_set is not None]
    if not local:
        if all(p.local_search_exhaustive for p in with_sets):
            return None, (
                f"No combination of segments within any single partition carries at most {max_traffic:.0%} of "
                f"post-window sessions while accounting for {threshold:.0%} of the change (every combination of "
                "the positively contributing segments was checked), so the change is spread across traffic: it "
                "is consistent with a broad shift rather than a local break."
            )
        return None, (
            f"No qualifying set was found: no combination of the {MAX_SEARCH_ATOMS} largest positive "
            f"contributors in each partition carries at most {max_traffic:.0%} of post-window sessions while "
            f"accounting for {threshold:.0%} of the change. The search was limited, so this does not by itself "
            "establish a broad shift."
        )
    best = min(
        local,
        key=lambda p: (
            not p.local_set.all_selected_significant,  # type: ignore[union-attr]
            p.local_set.k,  # type: ignore[union-attr]
            p.local_set.traffic_share_post,  # type: ignore[union-attr]
            p.name,
        ),
    )
    es = best.local_set
    assert es is not None
    statement = (
        f"The change is concentrated in {' and '.join(es.segments)} (partition {best.name}), which accounts for "
        f"{es.selected_share_of_change:.0%} of the overall change of {change_pp:+.3f} pp while carrying "
        f"{es.traffic_share_post:.0%} of post-window sessions. This describes where the change is concentrated; "
        "it does not say why it happened."
    )
    return (
        Localization(
            partition=best.name,
            segments=es.segments,
            selected_share_of_change=es.selected_share_of_change,
            traffic_share_post=es.traffic_share_post,
            all_selected_significant=es.all_selected_significant,
            statement=statement,
        ),
        None,
    )


def _assumptions(
    pre: ComparisonWindow,
    post: ComparisonWindow,
    partitions: list[PartitionResult],
    disclosure: ExclusionDisclosure,
    inference: str,
    fdr_family: str,
    n_scanned: int,
    n_unreliable: int,
    n_fallback: int,
    gap: float | None,
    threshold: float,
    max_traffic: float,
) -> list[str]:
    family_text = (
        f"all {n_scanned} reliably tested segments scanned across partitions"
        if fdr_family == "scan"
        else "the reliably tested segments of each partition separately"
    )
    if inference == "day_level":
        test_text = (
            "Segment tests use days as the sampling units (ratio-estimator variance of each window's pooled "
            "rate, Welch t with Welch degrees of freedom), so day-to-day variation and the number of days are "
            "reflected in the p-values and intervals; the plain binomial p-value is reported alongside."
        )
    else:
        test_text = (
            "Segment tests treat sessions as independent trials (plain binomial z-test): day-to-day variation "
            "is ignored, so these p-values are optimistic."
        )
    out = [
        (
            f"Comparison windows: before = {pre.start} to {pre.end}, after = {post.start} to {post.end}; "
            "sessions and orders are pooled across the usable days of each window."
        ),
        "Contributions are descriptive accounting of where the pooled rate moved, not causal effects.",
        (
            "Rate and mix effects use midpoint weights, which split the rate x mix interaction evenly; the "
            "decomposition does not depend on which window is the base."
        ),
        (
            "Partitions overlap (the same sessions appear in every partition). Contributions are valid within "
            "a partition and must not be added across partitions."
        ),
        (
            "Choosing the narrowest explaining set among several overlapping partitions is itself a "
            "data-driven selection; q-values account for the number of segments tested, not for that choice."
        ),
        (
            f"Benjamini-Hochberg controls the false discovery rate over {family_text}, assuming the tests "
            "are independent or positively dependent; overlapping segments are positively dependent, which "
            "BH tolerates, but the guarantee is approximate."
        ),
        test_text,
        (
            "The explaining set is the fewest segments, ranked by contribution in the direction of the change, "
            f"that reach {threshold:.0%} of it. A localization additionally needs a set carrying at most "
            f"{max_traffic:.0%} of post-window sessions and is found by searching combinations of the "
            f"positively contributing segments (exhaustively up to {MAX_SEARCH_ATOMS} of them per partition)."
        ),
    ]
    if n_unreliable:
        out.append(
            f"{n_unreliable} segment(s) are too sparse for a reliable test (a cell of the pooled before/after "
            f"table has fewer than {MIN_EXPECTED_COUNT:g} expected counts): they have no p-value, q-value or "
            "significance claim and are not in the false-discovery family, although their contribution is counted."
        )
    if n_fallback:
        out.append(
            f"{n_fallback} segment test(s) used the plain binomial test because a window has fewer than "
            f"{MIN_DAYS_FOR_DAY_LEVEL} days; those p-values are optimistic."
        )
    if gap is not None and gap > 0.1:
        out.append(
            f"The windows' weekday mixes differ (distance {gap:.2f}); part of a segment's change may be "
            "weekday seasonality, which this tool does not remove."
        )
    if any(s.status != "both" for p in partitions for s in p.segments):
        out.append(
            "Some segments appear in only one window: they have no observable rate change, so their whole "
            "contribution is a mix effect and they have no test."
        )
    if disclosure.uncertain_context_note:
        out.append(disclosure.uncertain_context_note)
    if disclosure.truncation_caution and any(d.windows for d in disclosure.excluded_days if d.baseline_truncated):
        out.append(disclosure.truncation_caution)
    return out


def _summary(
    change_pp: float,
    r0: float,
    r1: float,
    pre: ComparisonWindow,
    post: ComparisonWindow,
    partitions: list[PartitionResult],
    loc: Localization | None,
    loc_note: str | None,
    n_scanned: int,
    n_unreliable: int,
    fdr_family: str,
    inference: str,
    level: float,
    alpha: float,
) -> str:
    text = (
        f"Pooled conversion moved from {100 * r0:.3f}% (n={pre.n_days_used} days, {pre.sessions:,} sessions) to "
        f"{100 * r1:.3f}% (n={post.n_days_used} days, {post.sessions:,} sessions), a change of {change_pp:+.3f} pp. "
    )
    if loc is not None:
        best = next(p for p in partitions if p.name == loc.partition)
        text += loc.statement + " "
        seg = next(s for s in best.segments if s.label == loc.segments[0])
        if len(loc.segments) == 1 and seg.test is not None:
            t = seg.test
            interval = (
                f"conversion {100 * seg.conversion_pre:.3f}% to {100 * seg.conversion_post:.3f}% "
                f"({t.change_pp.estimate:+.3f} pp, {100 * level:g}% CI {t.change_pp.lower:+.3f} to "
                f"{t.change_pp.upper:+.3f}); rate effect {seg.rate_effect_pp:+.3f} pp, mix effect "
                f"{seg.mix_effect_pp:+.3f} pp; "
            )
            if t.q_value is None:
                text += f"For {seg.label}: {interval}no reliable test is available (sparse counts). "
            else:
                text += (
                    f"For {seg.label}: {interval}BH q = {t.q_value:.3g}"
                    + (" (distinguishable from noise). " if t.significant else f" (not below {alpha:g}). ")
                )
        if not loc.all_selected_significant:
            text += (
                "Not every selected segment has a rate change distinguishable from noise, so part of this may be "
                "a traffic-mix shift. "
            )
    else:
        text += (loc_note or "No segment can be singled out as explaining the change.") + " "
    text += (
        "False discovery rate was controlled with Benjamini-Hochberg over "
        f"{'all ' + str(n_scanned) + ' segment tests across partitions' if fdr_family == 'scan' else 'each partition'}"
        f" ({'days as the sampling units' if inference == 'day_level' else 'plain binomial tests, which are optimistic'})"
        + (f"; {n_unreliable} sparse segment(s) were not tested" if n_unreliable else "")
        + ". Every partition reconciles to the overall change"
        + (
            "."
            if all(p.reconciles for p in partitions)
            else ", EXCEPT: " + ", ".join(p.name for p in partitions if not p.reconciles) + "."
        )
    )
    return text
