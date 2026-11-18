"""Relative-timing contrasts for candidate events (Task 5.1; design.md, "Candidate testing").

The focal-minus-comparison conversion difference ``D_t`` (pp) is modelled as a level plus one step per
candidate:

    D_t = mu + sum_j beta_j * 1[t >= onset_j] + e_t,       onset_j = event_date_j + lag_j

With ``X`` the design matrix over the ``n`` usable days, the pooled variance is ``SSE / (n - rank(X))``,
a single contrast is tested with ``t`` on ``n - rank(X)`` degrees of freedom and a joint restriction on
``q`` coefficients with ``F`` on ``(q, n - rank(X))``, always from the full covariance of the estimates.
HC1 or date-clustered variances are deliberately *not* used: a coefficient identified from one date has
a zero residual on that date, so a sandwich variance for it collapses.

**Identifiability.** Step columns that are identical over the usable days (equal onsets, or no usable day
between them because it was excluded) cannot be separated. They are grouped, and every statement that
needs them separate is *unidentifiable*: no coefficient, share or bound is invented for them.

**Shares.** The share of the total step that begins at a candidate's onset is ``beta_k / sum_j beta_j``.
Its Fieller confidence set can be bounded, disconnected (two rays) or the whole line; it is unavailable,
with a reason, when the contrast cannot be estimated. It describes calendar steps under the lag model,
not a release's causal share.

Pure and deterministic: no network, LLM or randomness.
"""
from __future__ import annotations

import math
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from datetime import date as Date
from datetime import timedelta
from typing import Literal

import numpy as np
import pandas as pd
from pydantic import BaseModel, ConfigDict, Field
from scipy import stats
from statsmodels.stats.diagnostic import acorr_ljungbox

from causelens.analytics.confirm import Interval
from causelens.analytics.did import holm_adjust

#: The timing contrast needs this many usable days before the first onset and from it on.
MIN_DAYS_BEFORE = 14
MIN_DAYS_AFTER = 7
#: Diagnostics fail below this p-value. They screen for gross violations; passing validates nothing.
DIAGNOSTIC_ALPHA = 0.01
LJUNG_BOX_LAGS = 7
DEFAULT_ALPHA = 0.05


# --------------------------------------------------------------------------- result models


class ConfidenceSet(BaseModel):
    """A confidence set for a ratio, as a union of intervals (``None`` means unbounded on that side)."""

    model_config = ConfigDict(frozen=True)

    available: bool
    reason: str | None = None
    kind: Literal["bounded", "disconnected", "half_line", "unbounded"] | None = None
    pieces: list[tuple[float | None, float | None]] = Field(default_factory=list)
    level: float = 0.95

    def contains(self, value: float) -> bool:
        return self.available and any(
            (lo is None or value >= lo) and (hi is None or value <= hi) for lo, hi in self.pieces
        )


class SoleSourceTest(BaseModel):
    """Test of H_k: every other candidate's step is zero (the candidate alone starts the change)."""

    model_config = ConfigDict(frozen=True)

    statistic: Literal["t", "F"]
    value: float
    df1: int
    df2: int
    p_value: float
    p_value_holm: float | None = None
    rejected: bool = False


class CandidateTiming(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    onset_date: Date
    status: Literal["ok", "unidentifiable"]
    unidentifiable_reason: str | None = None
    coincident_with: list[str] = Field(default_factory=list)
    step_pp: Interval | None = None
    step_std_error_pp: float | None = None
    step_t: float | None = None
    step_df: int | None = None
    step_p: float | None = None
    step_p_holm: float | None = None
    step_significant: bool = False
    direction_matches: bool | None = None
    sole_source: SoleSourceTest | None = None
    share_set: ConfidenceSet


class DiagnosticCheck(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    status: Literal["pass", "fail", "not_evaluable"]
    p_value: float | None = None
    detail: str | None = None


class DiagnosticsResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    status: Literal["ok", "failed", "insufficient_days", "not_evaluable"]
    reason: str | None = None
    n_before: int
    n_after: int
    checks: list[DiagnosticCheck] = Field(default_factory=list)
    criterion: str = (
        f"independent, homoskedastic day-level noise: Ljung-Box over lags 1-{LJUNG_BOX_LAGS} and a pre/post "
        f"variance-equality (Levene) test; fail if p < {DIAGNOSTIC_ALPHA:g}; passing does not validate the "
        f"assumptions; needs {MIN_DAYS_BEFORE}+ days before and {MIN_DAYS_AFTER}+ from the first onset"
    )


class TimingAnalysis(BaseModel):
    model_config = ConfigDict(frozen=True)

    status: Literal["ok", "insufficient_days", "assumptions_failed", "not_evaluable"]
    reason: str | None = None
    n_days: int
    residual_df: int | None = None
    rank: int | None = None
    pooled_sd_pp: float | None = None
    total_step_pp: Interval | None = None
    alpha: float
    adjustment: str = "Holm across the candidate step tests, and separately across the sole-source tests"
    diagnostics: DiagnosticsResult
    candidates: dict[str, CandidateTiming]


class AmbiguousPair(BaseModel):
    """Two candidates that give the same onset date at some independently allowed pair of lags."""

    model_config = ConfigDict(frozen=True)

    first: str
    first_lag: int
    second: str
    second_lag: int
    onset_date: Date


# --------------------------------------------------------------------------- step model


@dataclass
class StepFit:
    days: pd.DatetimeIndex
    groups: list[list[str]]  # candidates whose step columns are identical, one entry per estimated column
    group_of: dict[str, int]
    degenerate: dict[str, str]  # candidates whose step column is all zeros or all ones
    beta: np.ndarray  # per group
    M: np.ndarray  # (X'X)^-1 restricted to the step columns
    sigma2: float
    df: int
    rank: int
    n: int
    resid: np.ndarray


def fit_steps(days: pd.DatetimeIndex, y: np.ndarray, onsets: Mapping[str, Date]) -> StepFit | None:
    """OLS step model. Returns ``None`` when no step column is estimable or there are no residual degrees of freedom."""
    n = len(days)
    cols: dict[str, np.ndarray] = {}
    degenerate: dict[str, str] = {}
    for name, onset in onsets.items():
        c = (days >= pd.Timestamp(onset)).astype(float)
        if c.sum() == 0:
            degenerate[name] = "no usable day on or after its onset date"
        elif c.sum() == n:
            degenerate[name] = "every usable day is on or after its onset date"
        else:
            cols[name] = c
    groups: list[list[str]] = []
    reps: list[np.ndarray] = []
    for name, c in cols.items():
        for gi, r in enumerate(reps):
            if np.array_equal(c, r):
                groups[gi].append(name)
                break
        else:
            groups.append([name])
            reps.append(c)
    if not groups:
        return None
    X = np.column_stack([np.ones(n), *reps])
    rank = int(np.linalg.matrix_rank(X))
    if rank < X.shape[1] or n - rank <= 0:
        return None
    XtX_inv = np.linalg.inv(X.T @ X)
    coef = XtX_inv @ (X.T @ y)
    resid = y - X @ coef
    df = n - rank
    sigma2 = float(resid @ resid) / df
    return StepFit(
        days=days, groups=groups, group_of={nm: gi for gi, g in enumerate(groups) for nm in g}, degenerate=degenerate,
        beta=coef[1:], M=XtX_inv[1:, 1:], sigma2=sigma2, df=df, rank=rank, n=n, resid=resid,
    )


def _contrast(fit: StepFit, R: np.ndarray) -> tuple[Literal["t", "F"], float, int, int, float] | None:
    """Wald test of ``R beta = 0``: t for one row, F for several. ``None`` if the covariance is singular."""
    q = R.shape[0]
    b = R @ fit.beta
    V = fit.sigma2 * (R @ fit.M @ R.T)
    if q == 1:
        v = float(V[0, 0])
        if not (v > 0 and math.isfinite(v)):
            return None
        t = float(b[0]) / math.sqrt(v)
        return "t", t, 1, fit.df, float(2 * stats.t.sf(abs(t), fit.df))
    try:
        eig = np.linalg.eigvalsh(V)
        if eig.min() <= 1e-12 * max(eig.max(), 1e-300):
            return None
        f = float(b @ np.linalg.solve(V, b)) / q
    except np.linalg.LinAlgError:
        return None
    return "F", f, q, fit.df, float(stats.f.sf(f, q, fit.df))


def fieller_set(fit: StepFit, group: int, level: float = 0.95) -> ConfidenceSet:
    """Fieller set for ``rho = beta_g / sum(beta)``: all rho with ``(beta_g - rho*total)^2 <= T * Var(beta_g - rho*total)``.

    The quadratic ``a rho^2 + b rho + c <= 0`` gives a bounded interval (a > 0), the complement of one (a < 0,
    two rays) or the whole line when the total step is not distinguishable from zero.
    """
    G = len(fit.groups)
    if G < 2:
        return ConfidenceSet(available=False, reason="only one step is estimated, so its share is 1 by construction", level=level)
    total = float(fit.beta.sum())
    ones = np.ones(G)
    m_k = float((fit.M @ ones)[group])
    s = float(ones @ fit.M @ ones)
    mkk = float(fit.M[group, group])
    crit = float(stats.t.ppf(1 - (1 - level) / 2, fit.df))
    T = fit.sigma2 * crit**2
    bk = float(fit.beta[group])
    a = total**2 - T * s
    b = -2 * bk * total + 2 * T * m_k
    c = bk**2 - T * mkk
    scale = total**2 + T * s + 1e-300
    if abs(a) <= 1e-12 * scale:
        if abs(b) <= 1e-12 * (abs(bk * total) + T * abs(m_k) + 1e-300):
            if c <= 0:
                return ConfidenceSet(available=True, kind="unbounded", pieces=[(None, None)], level=level)
            return ConfidenceSet(available=False, reason="no share is consistent with the data", level=level)
        root = -c / b
        piece = (None, root) if b > 0 else (root, None)
        return ConfidenceSet(available=True, kind="half_line", pieces=[piece], level=level)
    disc = b * b - 4 * a * c
    if a > 0:
        if disc < 0:
            return ConfidenceSet(available=False, reason="no share is consistent with the data", level=level)
        r = math.sqrt(disc)
        lo, hi = sorted(((-b - r) / (2 * a), (-b + r) / (2 * a)))
        return ConfidenceSet(available=True, kind="bounded", pieces=[(lo, hi)], level=level)
    if disc <= 0:
        return ConfidenceSet(available=True, kind="unbounded", pieces=[(None, None)], level=level)
    r = math.sqrt(disc)
    lo, hi = sorted(((-b - r) / (2 * a), (-b + r) / (2 * a)))
    return ConfidenceSet(available=True, kind="disconnected", pieces=[(None, lo), (hi, None)], level=level)


def timing_diagnostics(fit: StepFit, first_onset: Date) -> DiagnosticsResult:
    days = fit.days
    n_before = int((days < pd.Timestamp(first_onset)).sum())
    n_after = int((days >= pd.Timestamp(first_onset)).sum())
    if n_before < MIN_DAYS_BEFORE or n_after < MIN_DAYS_AFTER:
        return DiagnosticsResult(
            status="insufficient_days", n_before=n_before, n_after=n_after,
            reason=(
                f"{n_before} usable days before and {n_after} from the first onset "
                f"(need {MIN_DAYS_BEFORE} and {MIN_DAYS_AFTER}), so the working assumptions cannot be checked"
            ),
        )
    checks: list[DiagnosticCheck] = []
    resid = fit.resid
    if not float(np.ptp(resid)) > 1e-12 * max(1.0, float(np.abs(resid).max())):
        reason = "the step model fits exactly (no residual variation), so noise assumptions cannot be checked"
        return DiagnosticsResult(status="not_evaluable", reason=reason, n_before=n_before, n_after=n_after,
                                 checks=[DiagnosticCheck(name="residual variation", status="not_evaluable", detail=reason)])
    try:
        lb = acorr_ljungbox(resid, lags=[LJUNG_BOX_LAGS], return_df=True)
        p_lb = float(lb["lb_pvalue"].iloc[0])
        checks.append(DiagnosticCheck(
            name=f"Ljung-Box (lags 1-{LJUNG_BOX_LAGS})", p_value=p_lb,
            status="fail" if p_lb < DIAGNOSTIC_ALPHA else "pass",
        ))
    except (ValueError, np.linalg.LinAlgError) as exc:  # pragma: no cover - statsmodels failure modes
        checks.append(DiagnosticCheck(name="Ljung-Box", status="not_evaluable", detail=str(exc)))
    first = pd.Timestamp(first_onset)
    pre, post = resid[days < first], resid[days >= first]
    if len(pre) >= 3 and len(post) >= 3 and np.ptp(pre) > 0 and np.ptp(post) > 0:
        p_lev = float(stats.levene(pre, post, center="median").pvalue)
        checks.append(DiagnosticCheck(
            name="variance equality before vs after the first onset (Levene)", p_value=p_lev,
            status="fail" if p_lev < DIAGNOSTIC_ALPHA else "pass",
        ))
    else:
        checks.append(DiagnosticCheck(name="variance equality (Levene)", status="not_evaluable", detail="a window has no residual spread"))
    if any(c.status == "fail" for c in checks):
        failed = [c.name for c in checks if c.status == "fail"]
        return DiagnosticsResult(status="failed", reason="failed: " + "; ".join(failed), n_before=n_before, n_after=n_after, checks=checks)
    if any(c.status == "not_evaluable" for c in checks):
        return DiagnosticsResult(status="not_evaluable", reason="a required diagnostic could not be evaluated",
                                 n_before=n_before, n_after=n_after, checks=checks)
    return DiagnosticsResult(status="ok", n_before=n_before, n_after=n_after, checks=checks)


# --------------------------------------------------------------------------- analysis of all candidates


def _empty_timing(name: str, onset: Date, reason: str, level: float, coincident: list[str] | None = None) -> CandidateTiming:
    return CandidateTiming(
        name=name, onset_date=onset, status="unidentifiable", unidentifiable_reason=reason,
        coincident_with=coincident or [], share_set=ConfidenceSet(available=False, reason=reason, level=level),
    )


def analyze_timing(
    days: pd.DatetimeIndex,
    diff_pp: np.ndarray,
    onsets: Mapping[str, Date],
    *,
    alpha: float = DEFAULT_ALPHA,
    level: float = 0.95,
) -> TimingAnalysis:
    """Fit the step model for all candidates at once and test each one's sole-source hypothesis and step."""
    names = list(onsets)
    n = len(days)

    def blank(status, reason, diag, residual_df=None, rank=None):
        return TimingAnalysis(
            status=status, reason=reason, n_days=n, residual_df=residual_df, rank=rank, alpha=alpha, diagnostics=diag,
            candidates={nm: _empty_timing(nm, onsets[nm], reason or "timing not evaluated", level) for nm in names},
        )

    if not names:
        return blank("not_evaluable", "no candidate events", DiagnosticsResult(status="not_evaluable", n_before=0, n_after=0))
    fit = fit_steps(days, diff_pp, onsets)
    first_onset = min(onsets.values())
    if fit is None:
        n_before = int((days < pd.Timestamp(first_onset)).sum())
        diag = DiagnosticsResult(status="not_evaluable", n_before=n_before, n_after=n - n_before,
                                 reason="no estimable step (onsets outside the data, or no residual degrees of freedom)")
        return blank("not_evaluable", diag.reason, diag)
    diag = timing_diagnostics(fit, first_onset)

    # per-candidate structure -------------------------------------------------------------
    cands: dict[str, CandidateTiming] = {}
    G = len(fit.groups)
    total = float(fit.beta.sum())
    one = np.ones((1, G))
    total_test = _contrast(fit, one)
    total_se = math.sqrt(fit.sigma2 * float((one @ fit.M @ one.T)[0, 0])) if total_test else float("nan")
    crit = float(stats.t.ppf(1 - (1 - level) / 2, fit.df))
    total_ci = Interval(estimate=total, lower=total - crit * total_se, upper=total + crit * total_se, level=level) if total_test else None

    # step tests (one per group) and Holm across them
    step_stats: dict[int, tuple[float, float, float]] = {}
    for g in range(G):
        R = np.zeros((1, G))
        R[0, g] = 1.0
        res = _contrast(fit, R)
        if res is not None:
            _, t, _, _, p = res
            step_stats[g] = (float(fit.beta[g]), t, p)
    holm_step = dict(zip(step_stats, holm_adjust([v[2] for v in step_stats.values()]), strict=True))

    # sole-source tests: candidate in a singleton group; restriction = every other group's step is 0
    sole: dict[str, tuple[Literal["t", "F"], float, int, int, float]] = {}
    for nm in names:
        if nm in fit.degenerate or len(fit.groups[fit.group_of[nm]]) != 1 or G < 2:
            continue
        g = fit.group_of[nm]
        rows = [j for j in range(G) if j != g]
        R = np.zeros((len(rows), G))
        for i, j in enumerate(rows):
            R[i, j] = 1.0
        res = _contrast(fit, R)
        if res is not None:
            sole[nm] = res
    holm_sole = dict(zip(sole, holm_adjust([v[4] for v in sole.values()]), strict=True))

    for nm in names:
        onset = onsets[nm]
        if nm in fit.degenerate:
            cands[nm] = _empty_timing(nm, onset, fit.degenerate[nm], level)
            continue
        g = fit.group_of[nm]
        members = [m for m in fit.groups[g] if m != nm]
        if members:
            reason = (
                "its onset coincides with " + ", ".join(members) + " at the evaluated lags (or no usable day "
                "separates them), so the data cannot separate their steps"
            )
            cands[nm] = _empty_timing(nm, onset, reason, level, members)
            continue
        if g not in step_stats:
            cands[nm] = _empty_timing(nm, onset, "the step's variance is not estimable", level)
            continue
        beta, t, p = step_stats[g]
        se = abs(beta / t) if t else float("nan")
        direction = None if total == 0 or beta == 0 else bool(np.sign(beta) == np.sign(total))
        ss = None
        if nm in sole:
            kind, value, d1, d2, sp = sole[nm]
            ss = SoleSourceTest(statistic=kind, value=value, df1=d1, df2=d2, p_value=sp,
                                p_value_holm=holm_sole[nm], rejected=bool(holm_sole[nm] < alpha))
        cands[nm] = CandidateTiming(
            name=nm, onset_date=onset, status="ok",
            step_pp=Interval(estimate=beta, lower=beta - crit * se, upper=beta + crit * se, level=level),
            step_std_error_pp=se, step_t=t, step_df=fit.df, step_p=p, step_p_holm=holm_step[g],
            step_significant=bool(holm_step[g] < alpha), direction_matches=direction, sole_source=ss,
            share_set=fieller_set(fit, g, level),
        )

    if diag.status == "insufficient_days":
        status, reason = "insufficient_days", diag.reason
    elif diag.status in ("failed", "not_evaluable"):
        status, reason = ("assumptions_failed" if diag.status == "failed" else "not_evaluable"), diag.reason
    else:
        status, reason = "ok", None
    return TimingAnalysis(
        status=status, reason=reason, n_days=n, residual_df=fit.df, rank=fit.rank, pooled_sd_pp=math.sqrt(fit.sigma2),
        total_step_pp=total_ci, alpha=alpha, diagnostics=diag, candidates=cands,
    )


# --------------------------------------------------------------------------- lag ambiguity


def lag_ambiguity(
    event_dates: Mapping[str, Date],
    onset_set: Collection[Date],
    lags: Mapping[str, Sequence[int]],
) -> list[AmbiguousPair]:
    """Pairs of candidates that reach the same onset date, inside the onset set, at *independently* allowed lags.

    Every lag of one candidate is compared with every lag of the other (so an earlier event at lag 1 is
    compared with the next-day event at lag 0, not only lags that are equal). Such candidates have identical
    step columns, so no amount of data can tell them apart.
    """
    inside = set(onset_set)
    names = list(event_dates)
    out: list[AmbiguousPair] = []
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            for la in lags.get(a, (0,)):
                for lb in lags.get(b, (0,)):
                    da, db = event_dates[a] + timedelta(days=la), event_dates[b] + timedelta(days=lb)
                    if da == db and da in inside:
                        out.append(AmbiguousPair(first=a, first_lag=la, second=b, second_lag=lb, onset_date=da))
    return out
