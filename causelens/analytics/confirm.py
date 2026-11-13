"""``confirm_change``: is a before/after difference in conversion real or noise? (Requirement 2)

Method (design.md): daily conversion is ``orders / sessions``.

* **Before / after values** are pooled conversion rates over each window's usable days
  (total orders / total sessions), with 95% **bootstrap** intervals that resample *days*
  with replacement (``seed`` makes them reproducible). The absolute change (percentage
  points) and relative change get intervals the same way, resampling both windows.
* **Noise test**: a Welch t-test on the day-level rates, reported together with its effect
  size, which is measured against the pre-period's own day-to-day variability
  (``(mean_post - mean_pre) / sd_pre``) as Requirement 2.2 asks.
* The change is called **real** only if the bootstrap interval for the absolute change
  excludes 0 *and* the Welch p-value is below ``alpha``. Disagreement is reported as not
  distinguishable from noise (the conservative reading).

Days flagged by Task 2 are excluded by default and disclosed. If that (or missing data) leaves
fewer than ``min_days_per_window`` usable days in either window, the result is explicitly
``insufficient_data``; no estimate is invented.

Assumptions (also in ``result.assumptions``): days are treated as independent; the two windows
have a similar weekday mix (a difference is reported); nothing else changed in a way that moves
conversion. This tool says whether a *difference* exists, never why.
"""
from __future__ import annotations

import math
from collections.abc import Mapping
from datetime import date as Date
from typing import Literal

import numpy as np
import pandas as pd
from pydantic import BaseModel, ConfigDict, Field
from scipy import stats

from causelens.analytics.data_quality import DataQualityReport, DetectorParams
from causelens.analytics.series import (
    ExclusionDisclosure,
    build_disclosure,
    prepare_daily_series,
)

Window = tuple[Date | str, Date | str]


class Interval(BaseModel):
    model_config = ConfigDict(frozen=True)

    estimate: float
    lower: float
    upper: float
    level: float = 0.95


class WindowSummary(BaseModel):
    model_config = ConfigDict(frozen=True)

    start: Date
    end: Date
    n_days_requested: int
    n_days_used: int
    n_days_excluded: int = Field(description="Flagged days removed by default exclusions.")
    n_days_missing: int = Field(description="Dates with no data (or no sessions).")
    sessions: int
    orders: int
    conversion: Interval | None = Field(
        description="Pooled orders/sessions as a fraction (0.032 = 3.2%); None if no usable day."
    )


class NoiseTest(BaseModel):
    model_config = ConfigDict(frozen=True)

    method: str = "welch_t_on_daily_rates"
    t_statistic: float | None
    degrees_of_freedom: float | None
    p_value: float | None
    alpha: float
    effect_size_pre_sd: float | None = Field(
        description="(mean daily rate after - before) / sd of the pre-period's daily rates."
    )
    pre_daily_sd_pp: float | None = Field(description="Pre-period day-to-day sd of conversion, in pp.")
    post_daily_sd_pp: float | None
    welch_significant: bool
    bootstrap_excludes_zero: bool
    note: str


class DataNeeded(BaseModel):
    model_config = ConfigDict(frozen=True)

    minimum_detectable_change_pp: float | None = Field(
        description="Smallest |change| (pp) detectable with 80% power at the current number of days."
    )
    days_per_window_for_observed_change: int | None = Field(
        description="Days per window for 80% power to detect the *observed* change; None if it is ~0."
    )
    additional_days_per_window: int | None
    note: str


class ConfirmChangeResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    status: Literal["ok", "insufficient_data"]
    metric: str = "conversion"
    segment: dict[str, str]
    pre: WindowSummary
    post: WindowSummary
    abs_change_pp: Interval | None = Field(description="After minus before, in percentage points.")
    rel_change: Interval | None = Field(description="After / before - 1 as a fraction (-0.06 = -6%).")
    noise_test: NoiseTest | None
    is_real: bool
    verdict: Literal["real_change", "not_distinguishable_from_noise", "insufficient_data"]
    stop_investigation: bool = Field(description="True unless the change is real (Requirement 2.3).")
    data_needed: DataNeeded | None
    weekday_mix_difference: float | None = Field(
        description="Total-variation distance between the windows' weekday mixes (0 = identical)."
    )
    exclusions: ExclusionDisclosure
    n_bootstrap: int
    seed: int
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


def _interval(estimate: float, boot: np.ndarray, level: float) -> tuple[Interval, bool]:
    """Percentile interval, widened to contain the estimate if the bootstrap is so skewed that
    it does not (so ``lower <= estimate <= upper`` always holds). Returns (interval, widened)."""
    lo, hi = np.percentile(boot, [100 * (1 - level) / 2, 100 * (1 + level) / 2])
    widened = bool(lo > estimate or hi < estimate)
    return Interval(estimate=estimate, lower=float(min(lo, estimate)), upper=float(max(hi, estimate)), level=level), widened


def _daily_sd(x: np.ndarray) -> float:
    """Sample sd of day-level rates; exactly 0.0 when the values are identical up to float noise."""
    if np.ptp(x) <= 1e-12 * max(float(np.abs(x).max()), 1e-300):
        return 0.0
    return float(np.std(x, ddof=1))


def _weekday_mix(days: pd.DatetimeIndex) -> np.ndarray:
    counts = np.bincount(days.day_of_week, minlength=7).astype(float)
    return counts / counts.sum() if counts.sum() else counts


def _summarise(window: tuple[Date, Date], daily: pd.DataFrame) -> WindowSummary:
    lo, hi = window
    idx = pd.date_range(lo, hi, freq="D")
    sub = daily.reindex(idx)
    status = sub["status"].fillna("outside_data")
    used = sub[status == "ok"]
    return WindowSummary(
        start=lo,
        end=hi,
        n_days_requested=len(idx),
        n_days_used=len(used),
        n_days_excluded=int((status == "excluded").sum()),
        n_days_missing=int(status.isin(["missing", "no_sessions", "outside_data"]).sum()),
        sessions=int(used["sessions"].sum()),
        orders=int(used["orders"].sum()),
        conversion=None,
    )


def _window_days(daily: pd.DataFrame, window: tuple[Date, Date]) -> pd.DataFrame:
    sub = daily.loc[(daily.index >= pd.Timestamp(window[0])) & (daily.index <= pd.Timestamp(window[1]))]
    return sub[sub["status"] == "ok"]


def _pp(x: float) -> str:
    return f"{100 * x:.3f}"


# --------------------------------------------------------------------------- main tool


def confirm_change(
    metrics: pd.DataFrame,
    pre_window: Window,
    post_window: Window,
    events: pd.DataFrame | None = None,
    *,
    segment: Mapping[str, str] | None = None,
    quality: DataQualityReport | None = None,
    apply_exclusions: bool = True,
    detector_params: DetectorParams | None = None,
    n_bootstrap: int = 2000,
    seed: int = 0,
    level: float = 0.95,
    alpha: float = 0.05,
    min_days_per_window: int = 7,
) -> ConfirmChangeResult:
    """Report before/after conversion, the change with 95% intervals, and whether it is real.

    ``pre_window`` and ``post_window`` are inclusive ``(start, end)`` dates and must not overlap
    (pre must end before post starts). ``events`` lets Task 2 link flagged days to incidents;
    ``quality`` reuses an existing Task 2 report. Raises ``ValueError`` on invalid windows or
    arguments; thin data produces ``status="insufficient_data"`` rather than an error.
    """
    if n_bootstrap < 200:
        raise ValueError("n_bootstrap must be at least 200 for stable intervals")
    if not 0 < level < 1:
        raise ValueError("level must be in (0, 1)")
    if not 1e-12 <= alpha <= 0.5:
        raise ValueError("alpha must be between 1e-12 and 0.5")
    if min_days_per_window < 2:
        raise ValueError("min_days_per_window must be at least 2 (a variance needs two days)")
    pre = _parse_window("pre", pre_window)
    post = _parse_window("post", post_window)
    if pre[1] >= post[0]:
        raise ValueError(f"pre window must end before the post window starts ({pre[1]} >= {post[0]})")

    prepared = prepare_daily_series(
        metrics,
        events,
        segment=segment,
        quality=quality,
        apply_exclusions=apply_exclusions,
        detector_params=detector_params,
    )
    disclosure = build_disclosure(prepared.quality, apply_exclusions, {"pre": pre, "post": post})
    pre_days, post_days = _window_days(prepared.daily, pre), _window_days(prepared.daily, post)
    pre_sum = _summarise(pre, prepared.daily)
    post_sum = _summarise(post, prepared.daily)

    assumptions = [
        "Days are treated as independent observations (no autocorrelation adjustment).",
        "The two windows are assumed to have a similar weekday mix; the difference is reported.",
        "This tool shows whether a difference exists between the windows; it does not say why.",
    ]

    def exclusion_sentence() -> str:
        removed = [d for d in disclosure.excluded_days if d.windows]
        if not apply_exclusions:
            return " Flagged days were not excluded (exclusions switched off)."
        if not removed:
            return " No flagged days fell inside either window."
        parts = ", ".join(f"{d.date.isoformat()} ({'/'.join(d.windows)})" for d in removed)
        return f" Flagged days removed from the windows: {parts}."

    # ---- insufficient data: say so explicitly, produce no estimate
    short = [
        f"{name} window has {s.n_days_used} usable day(s) of {s.n_days_requested} requested "
        f"({s.n_days_excluded} excluded as flagged, {s.n_days_missing} missing or without sessions)"
        for name, s in (("pre", pre_sum), ("post", post_sum))
        if s.n_days_used < min_days_per_window
    ]
    if short:
        summary = (
            "Insufficient data to confirm a change: "
            + "; ".join(short)
            + f"; at least {min_days_per_window} usable days are needed in each window."
            + exclusion_sentence()
            + " No before/after estimate is reported."
        )
        return ConfirmChangeResult(
            status="insufficient_data",
            segment=prepared.segment,
            pre=pre_sum,
            post=post_sum,
            abs_change_pp=None,
            rel_change=None,
            noise_test=None,
            is_real=False,
            verdict="insufficient_data",
            stop_investigation=True,
            data_needed=None,
            weekday_mix_difference=None,
            exclusions=disclosure,
            n_bootstrap=n_bootstrap,
            seed=seed,
            assumptions=assumptions,
            summary=summary,
        )

    # ---- point estimates (pooled rates)
    po, ps = pre_days["orders"].to_numpy(), pre_days["sessions"].to_numpy()
    qo, qs = post_days["orders"].to_numpy(), post_days["sessions"].to_numpy()
    before, after = po.sum() / ps.sum(), qo.sum() / qs.sum()
    abs_change = after - before
    rel_change = after / before - 1 if before > 0 else float("nan")

    # ---- bootstrap over days (resample each window's days with replacement)
    rng = np.random.default_rng(seed)
    ip = rng.integers(0, len(po), size=(n_bootstrap, len(po)))
    iq = rng.integers(0, len(qo), size=(n_bootstrap, len(qo)))
    b_before = po[ip].sum(axis=1) / ps[ip].sum(axis=1)
    b_after = qo[iq].sum(axis=1) / qs[iq].sum(axis=1)
    before_ci, w1 = _interval(before, b_before, level)
    after_ci, w2 = _interval(after, b_after, level)
    abs_ci, w3 = _interval(abs_change, b_after - b_before, level)
    rel_ci: Interval | None = None
    w4 = False
    if before > 0:
        with np.errstate(divide="ignore", invalid="ignore"):
            b_rel = b_after / b_before - 1
        b_rel = b_rel[np.isfinite(b_rel)]
        if len(b_rel):
            rel_ci, w4 = _interval(rel_change, b_rel, level)

    # ---- noise test on day-level rates, with effect size against the pre-period's variability
    x_pre, x_post = pre_days["rate"].to_numpy(), post_days["rate"].to_numpy()
    sd_pre, sd_post = _daily_sd(x_pre), _daily_sd(x_post)
    mean_diff = float(x_post.mean() - x_pre.mean())
    if sd_pre == 0 and sd_post == 0:
        t_stat = df = None
        p_value = 0.0 if abs(mean_diff) > 1e-12 * max(float(np.abs(x_pre).max()), 1e-300) else 1.0
        mean_diff = 0.0 if p_value == 1.0 else mean_diff
        note_extra = "Both windows have identical daily rates within themselves (zero variability)."
    else:
        res = stats.ttest_ind(x_post, x_pre, equal_var=False)
        t_stat, p_value = float(res.statistic), float(res.pvalue)
        v1, v2 = sd_pre**2 / len(x_pre), sd_post**2 / len(x_post)
        df = float((v1 + v2) ** 2 / (v1**2 / (len(x_pre) - 1) + v2**2 / (len(x_post) - 1)))
        note_extra = ""
    effect = mean_diff / sd_pre if sd_pre > 0 else None
    boot_excludes = bool(abs_ci.lower > 0 or abs_ci.upper < 0)
    welch_sig = bool(p_value < alpha)
    is_real = boot_excludes and welch_sig

    if is_real:
        noise_note = "Both the bootstrap interval and the Welch test indicate a difference between the windows."
    elif boot_excludes != welch_sig:
        noise_note = (
            "The bootstrap interval and the Welch test disagree, so the difference is not "
            "distinguishable from noise at this evidence level."
        )
    else:
        noise_note = "Neither the bootstrap interval nor the Welch test separates the windows."
    noise = NoiseTest(
        t_statistic=t_stat,
        degrees_of_freedom=df,
        p_value=p_value,
        alpha=alpha,
        effect_size_pre_sd=effect,
        pre_daily_sd_pp=100 * sd_pre,
        post_daily_sd_pp=100 * sd_post,
        welch_significant=welch_sig,
        bootstrap_excludes_zero=boot_excludes,
        note=(noise_note + " " + note_extra).strip(),
    )

    # ---- how much data would be needed if the change is not distinguishable from noise
    data_needed: DataNeeded | None = None
    if not is_real:
        z_a, z_b = stats.norm.ppf(1 - alpha / 2), stats.norm.ppf(0.8)
        n_now = min(len(x_pre), len(x_post))
        mde = float((z_a + z_b) * math.sqrt(sd_pre**2 / len(x_pre) + sd_post**2 / len(x_post)))
        needed = extra = None
        if abs(mean_diff) > 0 and (sd_pre > 0 or sd_post > 0):
            needed = max(math.ceil((z_a + z_b) ** 2 * (sd_pre**2 + sd_post**2) / mean_diff**2), 2)
            extra = max(0, needed - n_now)
            note = (
                f"At the observed change and day-to-day variability, about {needed} days per window "
                f"would give 80% power at alpha {alpha:g} (you have {n_now}): roughly {extra} more per "
                "window, if the change is real and the variability holds. With the current days only "
                f"changes of about {_pp(mde)} pp or more could be detected."
            )
        else:
            note = (
                "The observed change is essentially zero, so collecting more days of the same data "
                f"would not help unless a smaller change matters; with the current days only changes "
                f"of about {_pp(mde)} pp or more could be detected."
            )
        data_needed = DataNeeded(
            minimum_detectable_change_pp=100 * mde,
            days_per_window_for_observed_change=needed,
            additional_days_per_window=extra,
            note=note,
        )

    pre_sum = pre_sum.model_copy(update={"conversion": before_ci})
    post_sum = post_sum.model_copy(update={"conversion": after_ci})
    mix_gap = float(
        0.5 * np.abs(_weekday_mix(pre_days.index) - _weekday_mix(post_days.index)).sum()
    )
    if mix_gap > 0.1:
        assumptions.append(
            f"The windows' weekday mixes differ (distance {mix_gap:.2f}); part of the difference may be "
            "weekday seasonality, which this tool does not remove."
        )
    if w1 or w2 or w3 or w4:
        assumptions.append(
            "A bootstrap interval was widened to include its point estimate because the resampled "
            "distribution was strongly skewed."
        )
    if disclosure.uncertain_context_note:
        assumptions.append(disclosure.uncertain_context_note)
    if disclosure.truncation_caution and any(d.windows for d in disclosure.excluded_days if d.baseline_truncated):
        assumptions.append(disclosure.truncation_caution)

    abs_pp = abs_ci.model_copy(update={k: 100 * getattr(abs_ci, k) for k in ("estimate", "lower", "upper")})
    p_text = "p < 0.0001" if p_value < 1e-4 else f"p = {p_value:.4f}"
    body = (
        f"Conversion was {_pp(before)}% before (n={len(x_pre)} days, {pre_sum.sessions:,} sessions) and "
        f"{_pp(after)}% after (n={len(x_post)} days, {post_sum.sessions:,} sessions): a change of "
        f"{abs_pp.estimate:+.3f} pp (95% CI {abs_pp.lower:+.3f} to {abs_pp.upper:+.3f})"
    )
    if rel_ci is not None:
        body += f", {100 * rel_ci.estimate:+.1f}% relative (95% CI {100 * rel_ci.lower:+.1f}% to {100 * rel_ci.upper:+.1f}%)"
    eff_text = "n/a" if effect is None else f"{effect:+.2f}"
    body += f". Effect size {eff_text} pre-period daily sd; Welch {p_text}. "
    if is_real:
        body += "The difference is distinguishable from normal day-to-day variation; this does not say why it happened."
    else:
        body += (
            "The difference is NOT distinguishable from normal day-to-day variation, so the investigation "
            "should stop here. " + (data_needed.note if data_needed else "")
        )
    summary = body + exclusion_sentence()

    return ConfirmChangeResult(
        status="ok",
        segment=prepared.segment,
        pre=pre_sum,
        post=post_sum,
        abs_change_pp=abs_pp,
        rel_change=rel_ci,
        noise_test=noise,
        is_real=is_real,
        verdict="real_change" if is_real else "not_distinguishable_from_noise",
        stop_investigation=not is_real,
        data_needed=data_needed,
        weekday_mix_difference=mix_gap,
        exclusions=disclosure,
        n_bootstrap=n_bootstrap,
        seed=seed,
        assumptions=assumptions,
        summary=summary,
    )
