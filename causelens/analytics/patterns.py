"""Declared expected-effect patterns, tested directly (Task 5.1; design.md, "Candidate testing").

An event's *exposure scope* (from the event log) says who it could have touched. An *expected pattern* is
what the user expects to move. It is declared per candidate, defaults to ``unspecified``, and is never
inferred from an event's name or description. Non-significance in a segment is never taken as evidence of
no effect, so a pattern is contradicted only by a direct test of that pattern:

* ``uniform``: an omnibus Wald test that the change is equal across the segments of each single-dimension
  partition, on the declared scale (``relative``: log change; ``absolute``: difference). The covariance of the
  segment estimates comes from the shared days (days are the sampling units), so a day effect common to all
  segments cancels instead of being double counted. Holm across the partitions tested. Segments with a zero
  rate or fewer than 5 pooled orders in either window cannot support the test and are excluded and listed;
  a partition with fewer than two left is skipped; if none can be tested the pattern is *untested* and
  contributes nothing.
* ``restricted(scope)``: contradicted when the change outside the scope has a confidence interval excluding
  zero. Absence of an outside effect is claimed only within a user-declared equivalence margin; with no
  margin it is never claimed.

Pure and deterministic.
"""
from __future__ import annotations

from collections.abc import Mapping
from datetime import date as Date
from typing import Literal

import numpy as np
import pandas as pd
from pydantic import BaseModel, ConfigDict, Field, model_validator
from scipy import stats

from causelens.analytics.confirm import Interval
from causelens.analytics.decompose import day_level_test
from causelens.analytics.did import holm_adjust, segment_mask
from causelens.analytics.loaders import DIMENSIONS

MIN_ORDERS_PER_WINDOW = 5
MIN_DAYS_PER_WINDOW = 3
DEFAULT_ALPHA = 0.05


class ExpectedPattern(BaseModel):
    """What the user expects an event to move. Declared, never inferred."""

    model_config = ConfigDict(frozen=True)

    kind: Literal["unspecified", "uniform", "restricted"] = "unspecified"
    scale: Literal["relative", "absolute"] = "relative"
    scope: dict[str, str] | None = None
    equivalence_margin_pp: float | None = Field(None, gt=0, description="Required to claim 'no outside effect'; no default.")

    @model_validator(mode="after")
    def _check(self) -> ExpectedPattern:
        if self.kind == "restricted":
            if not self.scope or any(k not in DIMENSIONS for k in self.scope):
                raise ValueError(f"a restricted pattern needs a scope over the dimensions {list(DIMENSIONS)}")
        elif self.scope is not None:
            raise ValueError("scope is only meaningful for a restricted pattern")
        return self


class PartitionTest(BaseModel):
    model_config = ConfigDict(frozen=True)

    dimension: str
    status: Literal["tested", "skipped"]
    reason: str | None = None
    included: list[str] = Field(default_factory=list)
    excluded: dict[str, str] = Field(default_factory=dict, description="segment -> why it could not support the test")
    statistic: float | None = Field(None, description="Hotelling-type F")
    df1: int | None = None
    df2: int | None = None
    p_value: float | None = None
    p_value_holm: float | None = None


class PatternCheck(BaseModel):
    model_config = ConfigDict(frozen=True)

    kind: Literal["unspecified", "uniform", "restricted"]
    status: Literal["not_declared", "contradicted", "not_contradicted", "untested"]
    detail: str
    scale: str | None = None
    partitions: list[PartitionTest] = Field(default_factory=list)
    adjustment: str | None = None
    outside_change_pp: Interval | None = None
    absence_claimed: bool = False


NOT_DECLARED = PatternCheck(kind="unspecified", status="not_declared", detail="no expected pattern was declared")


# --------------------------------------------------------------------------- uniform


def _window_matrix(data: pd.DataFrame, dim: str, days: pd.DatetimeIndex) -> tuple[pd.DataFrame, pd.DataFrame]:
    sub = data[data["date"].isin(days)]
    g = sub.groupby(["date", dim])[["sessions", "orders"]].sum()
    s = g["sessions"].unstack(dim).reindex(days).fillna(0.0)
    o = g["orders"].unstack(dim).reindex(days).fillna(0.0)
    return s, o


def _ratio_cov(s: pd.DataFrame, o: pd.DataFrame, segments: list[str]) -> tuple[np.ndarray, np.ndarray, int]:
    """Pooled rates and their ratio-estimator covariance with days as the sampling units."""
    s_, o_ = s[segments].to_numpy(float), o[segments].to_numpy(float)
    d = s_.shape[0]
    big_s, big_o = s_.sum(axis=0), o_.sum(axis=0)
    rate = big_o / big_s
    u = o_ - rate * s_
    cov = d / (d - 1) * (u.T @ u) / np.outer(big_s, big_s)
    return rate, cov, d


def uniform_test(
    data: pd.DataFrame,
    usable_days: pd.DatetimeIndex,
    pre_window: tuple[Date, Date],
    post_window: tuple[Date, Date],
    *,
    scale: Literal["relative", "absolute"] = "relative",
    alpha: float = DEFAULT_ALPHA,
) -> PatternCheck:
    """Omnibus test that the change is equal across segments, per single-dimension partition, Holm across partitions."""
    pre_days = usable_days[(usable_days >= pd.Timestamp(pre_window[0])) & (usable_days <= pd.Timestamp(pre_window[1]))]
    post_days = usable_days[(usable_days >= pd.Timestamp(post_window[0])) & (usable_days <= pd.Timestamp(post_window[1]))]
    adjustment = f"Holm across the single-dimension partitions tested, alpha = {alpha:g}"
    if len(pre_days) < MIN_DAYS_PER_WINDOW or len(post_days) < MIN_DAYS_PER_WINDOW:
        return PatternCheck(
            kind="uniform", status="untested", scale=scale, adjustment=adjustment,
            detail=f"untested: {len(pre_days)} usable days before and {len(post_days)} after (need {MIN_DAYS_PER_WINDOW} in each window)",
        )
    parts: list[PartitionTest] = []
    for dim in DIMENSIONS:
        s0, o0 = _window_matrix(data, dim, pre_days)
        s1, o1 = _window_matrix(data, dim, post_days)
        segments = sorted(set(s0.columns) | set(s1.columns))
        for df_ in (s0, o0, s1, o1):
            for seg in segments:
                if seg not in df_.columns:
                    df_[seg] = 0.0
        excluded: dict[str, str] = {}
        included: list[str] = []
        for seg in segments:
            n0, n1 = float(o0[seg].sum()), float(o1[seg].sum())
            if float(s0[seg].sum()) <= 0 or float(s1[seg].sum()) <= 0:
                excluded[seg] = "no sessions in a window"
            elif int((s0[seg] > 0).sum()) < MIN_DAYS_PER_WINDOW or int((s1[seg] > 0).sum()) < MIN_DAYS_PER_WINDOW:
                excluded[seg] = (
                    f"fewer than {MIN_DAYS_PER_WINDOW} active days (days with sessions) in a window, so its day-level "
                    "variance cannot be estimated"
                )
            elif n0 < MIN_ORDERS_PER_WINDOW or n1 < MIN_ORDERS_PER_WINDOW:
                excluded[seg] = f"fewer than {MIN_ORDERS_PER_WINDOW} pooled orders in a window (zero or near-zero rate)"
            else:
                included.append(seg)
        if len(included) < 2:
            parts.append(PartitionTest(dimension=dim, status="skipped", included=included, excluded=excluded,
                                       reason="fewer than 2 segments can support the test"))
            continue
        # Only the included segments define the sampling units. Days on which none of them has sessions (they exist
        # because excluded traffic is present) are dropped, and the degrees of freedom come from the sparsest included
        # segment's active days, so adding excluded traffic can never change this test of the same included observations.
        s0, o0, s1, o1 = (m[included] for m in (s0, o0, s1, o1))
        keep0, keep1 = (s0 > 0).any(axis=1), (s1 > 0).any(axis=1)
        s0, o0, s1, o1 = s0[keep0], o0[keep0], s1[keep1], o1[keep1]
        active = min(int((s0 > 0).sum().min()), int((s1 > 0).sum().min()))  # active days of the sparsest included segment
        r0, c0, d0 = _ratio_cov(s0, o0, included)
        r1, c1, d1 = _ratio_cov(s1, o1, included)
        if scale == "relative":
            theta = np.log(r1) - np.log(r0)
            cov = c1 / np.outer(r1, r1) + c0 / np.outer(r0, r0)
        else:
            theta = r1 - r0
            cov = c1 + c0
        k = len(included)
        C = np.zeros((k - 1, k))
        C[:, 0] = -1.0
        C[np.arange(k - 1), np.arange(1, k)] = 1.0
        v = C @ cov @ C.T
        p_dim = k - 1
        nu = min(d0, d1, active) - 1  # active <= d0, d1; it is smaller when included segments are sparse or misaligned
        if nu - p_dim + 1 < 1:
            parts.append(PartitionTest(dimension=dim, status="skipped", included=included, excluded=excluded,
                                       reason=f"too few days ({min(d0, d1, active)}) to estimate {p_dim} contrasts"))
            continue
        eig = np.linalg.eigvalsh(v)
        if not (eig.min() > 1e-12 * max(eig.max(), 1e-300)) or not np.isfinite(eig).all():
            parts.append(PartitionTest(dimension=dim, status="skipped", included=included, excluded=excluded,
                                       reason="the covariance of the contrasts is singular"))
            continue
        w = float((C @ theta) @ np.linalg.solve(v, C @ theta))
        f = w * (nu - p_dim + 1) / (nu * p_dim)
        p = float(stats.f.sf(f, p_dim, nu - p_dim + 1))
        parts.append(PartitionTest(dimension=dim, status="tested", included=included, excluded=excluded,
                                   statistic=f, df1=p_dim, df2=nu - p_dim + 1, p_value=p))
    tested = [i for i, pt in enumerate(parts) if pt.status == "tested"]
    if not tested:
        return PatternCheck(
            kind="uniform", status="untested", scale=scale, partitions=parts, adjustment=adjustment,
            detail="untested: no partition has two or more segments with enough orders; the declared pattern contributes nothing",
        )
    adj = holm_adjust([parts[i].p_value for i in tested])  # type: ignore[misc]
    for i, a in zip(tested, adj, strict=True):
        parts[i] = parts[i].model_copy(update={"p_value_holm": a})
    bad = [parts[i] for i in tested if parts[i].p_value_holm is not None and parts[i].p_value_holm < alpha]
    if bad:
        names = ", ".join(f"{b.dimension} (Holm p = {b.p_value_holm:.3g})" for b in bad)
        return PatternCheck(
            kind="uniform", status="contradicted", scale=scale, partitions=parts, adjustment=adjustment,
            detail=f"the change is not equal across segments on the {scale} scale: {names}",
        )
    return PatternCheck(
        kind="uniform", status="not_contradicted", scale=scale, partitions=parts, adjustment=adjustment,
        detail=(
            f"no partition rejects equal {scale} change; this does not show the effect is uniform "
            f"({len(tested)} of {len(parts)} partitions could be tested)"
        ),
    )


# --------------------------------------------------------------------------- restricted


def restricted_test(
    data: pd.DataFrame,
    usable_days: pd.DatetimeIndex,
    pre_window: tuple[Date, Date],
    post_window: tuple[Date, Date],
    scope: Mapping[str, str],
    *,
    equivalence_margin_pp: float | None = None,
    level: float = 0.95,
) -> PatternCheck:
    """Contradicted when the change outside ``scope`` has a CI excluding 0; absence needs a declared margin."""
    outside = ~segment_mask(data, [dict(scope)])
    sub = data[outside & data["date"].isin(usable_days)]
    if sub.empty:
        return PatternCheck(kind="restricted", status="untested", detail="no traffic lies outside the declared scope")
    daily = sub.groupby("date")[["sessions", "orders"]].sum()

    def arrays(window: tuple[Date, Date]) -> tuple[np.ndarray, np.ndarray]:
        d = daily[(daily.index >= pd.Timestamp(window[0])) & (daily.index <= pd.Timestamp(window[1]))]
        d = d[d["sessions"] > 0]
        return d["sessions"].to_numpy(float), d["orders"].to_numpy(float)

    s0, o0 = arrays(pre_window)
    s1, o1 = arrays(post_window)
    if len(s0) < MIN_DAYS_PER_WINDOW or len(s1) < MIN_DAYS_PER_WINDOW:
        return PatternCheck(
            kind="restricted", status="untested",
            detail=f"untested: {len(s0)} days before and {len(s1)} after outside the scope (need {MIN_DAYS_PER_WINDOW} in each window)",
        )
    res = day_level_test(s0, o0, s1, o1, level=level)
    ci = res.change_pp
    if ci.lower > 0 or ci.upper < 0:
        return PatternCheck(
            kind="restricted", status="contradicted", outside_change_pp=ci,
            detail=f"outside the declared scope the change is {ci.estimate:+.3f} pp ({100 * level:g}% CI {ci.lower:+.3f} to {ci.upper:+.3f}), which excludes 0",
        )
    margin = equivalence_margin_pp
    if margin is not None and ci.lower > -margin and ci.upper < margin:
        return PatternCheck(
            kind="restricted", status="not_contradicted", outside_change_pp=ci, absence_claimed=True,
            detail=f"the outside change is within the declared margin of +/-{margin:g} pp ({100 * level:g}% CI {ci.lower:+.3f} to {ci.upper:+.3f})",
        )
    return PatternCheck(
        kind="restricted", status="not_contradicted", outside_change_pp=ci, absence_claimed=False,
        detail=(
            f"no outside change is distinguishable from 0 ({100 * level:g}% CI {ci.lower:+.3f} to {ci.upper:+.3f} pp), "
            + ("but it is not inside the declared margin, so absence is not claimed" if margin is not None
               else "and no equivalence margin was declared, so absence is not claimed")
        ),
    )


def check_pattern(
    pattern: ExpectedPattern,
    data: pd.DataFrame,
    usable_days: pd.DatetimeIndex,
    pre_window: tuple[Date, Date],
    post_window: tuple[Date, Date],
    *,
    alpha: float = DEFAULT_ALPHA,
    level: float = 0.95,
) -> PatternCheck:
    if pattern.kind == "unspecified":
        return NOT_DECLARED
    if pattern.kind == "uniform":
        return uniform_test(data, usable_days, pre_window, post_window, scale=pattern.scale, alpha=alpha)
    assert pattern.scope is not None
    return restricted_test(data, usable_days, pre_window, post_window, pattern.scope,
                           equivalence_margin_pp=pattern.equivalence_margin_pp, level=level)


__all__ = [
    "NOT_DECLARED", "ExpectedPattern", "PartitionTest", "PatternCheck", "check_pattern", "restricted_test", "uniform_test",
]
