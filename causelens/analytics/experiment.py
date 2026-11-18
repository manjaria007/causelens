"""``power_analysis`` and ``design_experiment`` (Task 6; Requirement 7).

All numbers come from a deterministic calculation, never from the LLM (Requirement 7.4).

**Units.** ``baseline`` is a *fraction* in (0, 1). ``mde_pp`` is a *signed percentage-point* change of the treatment
arm relative to the control arm, so ``target_rate = baseline + mde_pp / 100``.

**Power.** The planned test is the two-sided pooled two-proportion z-test. Required sample size and achieved power use
the normal approximation (variance pooled under the null, unpooled under the alternative, both tails). The reported
power is "achieved power under the normal approximation", not exact finite-sample power; ``approximation_reliable`` is
false when an expected count is below 10. Allocation is explicit and integer: ``n_control = n``,
``n_treatment = ceil(allocation_ratio * n)``, and ``n`` is the smallest integer reaching the target power (the
previous pair, ``n - 1``, does not). With several primary comparisons, alpha is divided by their number (Bonferroni)
and the power is *per comparison*, not whole-experiment power.

**Design.** ``design_experiment`` turns a Task 5 result into a rollback experiment for the focal segment: the control
arm keeps the current experience, whose baseline is the focal conversion over the usable post-period days. It tests the
current contribution of the rolled-back release with the other releases held fixed; it cannot establish the sole
historical cause. Missing inputs give ``status="needs_input"`` and say which, never an invented value.
"""
from __future__ import annotations

import math
import numbers
from collections.abc import Sequence
from datetime import date as Date
from typing import Literal

import numpy as np
import pandas as pd
from pydantic import BaseModel, ConfigDict, Field
from scipy import stats

from causelens.analytics.candidates import CandidateTestingResult, CandidateVerdict
from causelens.analytics.confirm import Interval
from causelens.analytics.data_quality import DataQualityReport
from causelens.analytics.did import Panel, build_panel, did_on_panel

ACHIEVED_POWER_LABEL = "achieved power under the normal approximation"
MIN_EXPECTED_COUNT = 10.0
MIN_BASELINE_DAYS = 7
MIN_ALLOCATION_RATIO, MAX_ALLOCATION_RATIO = 0.01, 100.0
MAX_SAMPLE_SIZE = 10**12

Window = tuple[Date | str, Date | str]


# --------------------------------------------------------------------------- validation


def _real(value: object, name: str) -> float:
    if isinstance(value, (bool, np.bool_)) or not isinstance(value, numbers.Real):
        raise ValueError(f"{name} must be a real number, got {value!r}")  # noqa: TRY004 (bad arguments are ValueError in this API)
    x = float(value)
    if not math.isfinite(x):
        raise ValueError(f"{name} must be finite, got {value!r}")
    return x


def _count(value: object, name: str) -> int:
    if isinstance(value, (bool, np.bool_)) or not isinstance(value, (int, np.integer)):
        raise ValueError(f"{name} must be an integer, got {value!r}")  # noqa: TRY004 (bad arguments are ValueError in this API)
    return int(value)


def _check_inputs(
    baseline: object, mde_pp: object, alpha: object, power: object, allocation_ratio: object, n_comparisons: object
) -> tuple[float, float, float, float, float, float, int]:
    p0 = _real(baseline, "baseline")
    if not 0.0 < p0 < 1.0:
        raise ValueError(f"baseline is a fraction and must be in (0, 1), got {baseline!r}")
    d_pp = _real(mde_pp, "mde_pp")
    if d_pp == 0.0:
        raise ValueError("mde_pp must be non-zero: a zero effect needs infinite power")
    p1 = p0 + d_pp / 100.0
    if not 0.0 < p1 < 1.0:
        raise ValueError(
            f"target_rate = baseline + mde_pp / 100 = {p1!r} must be in (0, 1); mde_pp is in percentage points, not a fraction"
        )
    a = _real(alpha, "alpha")
    if not 0.0 < a <= 0.5:
        raise ValueError(f"alpha must be in (0, 0.5], got {alpha!r}")
    k = _count(n_comparisons, "n_comparisons")
    if k < 1:
        raise ValueError(f"n_comparisons must be at least 1, got {n_comparisons!r}")
    a_per = a / k
    pw = _real(power, "power")
    if not a_per < pw < 1.0:
        raise ValueError(f"power must be in (alpha per comparison = {a_per:g}, 1), got {power!r}")
    r = _real(allocation_ratio, "allocation_ratio")
    if not MIN_ALLOCATION_RATIO <= r <= MAX_ALLOCATION_RATIO:
        raise ValueError(
            f"allocation_ratio (treatment per control session) must be in [{MIN_ALLOCATION_RATIO:g}, {MAX_ALLOCATION_RATIO:g}], got {allocation_ratio!r}"
        )
    return p0, p1, d_pp, a, pw, r, k


# --------------------------------------------------------------------------- the power calculation


def normal_approx_power(
    baseline: float, mde_pp: float, n_control: int, n_treatment: int, *, alpha: float = 0.05, n_comparisons: int = 1
) -> float:
    """Achieved power under the normal approximation for explicit integer arm sizes (both tails, per comparison)."""
    p0, p1, _, a, _, _, k = _check_inputs(baseline, mde_pp, alpha, 0.99999, 1.0, n_comparisons)
    nc, nt = _count(n_control, "n_control"), _count(n_treatment, "n_treatment")
    if nc < 1 or nt < 1:
        raise ValueError("both arms need at least one session")
    return _power(p0, p1, nc, nt, a / k)


def _power(p0: float, p1: float, nc: float, nt: float, alpha_per: float) -> float:
    d = abs(p1 - p0)
    z = float(stats.norm.isf(alpha_per / 2.0))
    pbar = (nc * p0 + nt * p1) / (nc + nt)
    se0 = math.sqrt(pbar * (1.0 - pbar) * (1.0 / nc + 1.0 / nt))
    se1 = math.sqrt(p0 * (1.0 - p0) / nc + p1 * (1.0 - p1) / nt)
    return float(stats.norm.cdf((d - z * se0) / se1) + stats.norm.cdf((-d - z * se0) / se1))


def _min_expected_count(p0: float, p1: float, nc: int, nt: int) -> float:
    pbar = (nc * p0 + nt * p1) / (nc + nt)
    return float(min(nc * p0, nc * (1 - p0), nt * p1, nt * (1 - p1), nc * pbar, nc * (1 - pbar), nt * pbar, nt * (1 - pbar)))


class PowerResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    baseline: float = Field(description="Control-arm conversion, a fraction.")
    mde_pp: float = Field(description="Signed percentage-point change of the treatment arm relative to control.")
    target_rate: float = Field(description="baseline + mde_pp / 100.")
    alpha: float
    n_comparisons: int
    alpha_per_comparison: float
    target_power: float = Field(description="Power per comparison, not whole-experiment power.")
    allocation_ratio: float = Field(description="Treatment sessions per control session, before rounding up.")
    n_control: int
    n_treatment: int = Field(description="ceil(allocation_ratio * n_control), per treatment arm.")
    n_total_per_comparison: int
    achieved_power: float = Field(description=f"{ACHIEVED_POWER_LABEL}, per comparison, at the integer sizes above.")
    achieved_power_label: str = ACHIEVED_POWER_LABEL
    previous_pair_power: float | None = Field(description="Same quantity at n_control - 1 (below the target by construction).")
    min_expected_count: float
    approximation_reliable: bool
    warnings: list[str]
    assumptions: list[str]
    method: str = "two-sided pooled two-proportion z-test; normal approximation"


def power_analysis(
    baseline: float,
    mde_pp: float,
    *,
    alpha: float = 0.05,
    power: float = 0.8,
    allocation_ratio: float = 1.0,
    n_comparisons: int = 1,
) -> PowerResult:
    """Smallest integer sample size per arm that reaches ``power`` for a signed ``mde_pp`` change from ``baseline``.

    ``baseline`` is a fraction; ``mde_pp`` is in percentage points (``target_rate = baseline + mde_pp / 100``).
    ``alpha`` is the family-wise level over ``n_comparisons`` primary comparisons (Bonferroni), and ``power`` is per
    comparison. Raises ``ValueError`` for an invalid input.
    """
    p0, p1, d_pp, a, pw, r, k = _check_inputs(baseline, mde_pp, alpha, power, allocation_ratio, n_comparisons)
    a_per = a / k

    def nt_of(n: int) -> int:
        return max(1, math.ceil(r * n - 1e-12))

    def reaches(n: int) -> bool:
        return _power(p0, p1, n, nt_of(n), a_per) >= pw

    hi = 2
    while not reaches(hi):
        hi *= 2
        if hi > MAX_SAMPLE_SIZE:
            raise ValueError(
                f"the required sample size exceeds {MAX_SAMPLE_SIZE:,} per arm; the effect is too small to design for"
            )
    lo = 1  # infeasible or the minimum; refine below
    if reaches(lo):
        hi = lo
    else:
        while hi - lo > 1:
            mid = (lo + hi) // 2
            if reaches(mid):
                hi = mid
            else:
                lo = mid
    n = hi
    nt = nt_of(n)
    achieved = _power(p0, p1, n, nt, a_per)
    prev = _power(p0, p1, n - 1, nt_of(n - 1), a_per) if n > 1 else None
    min_count = _min_expected_count(p0, p1, n, nt)
    reliable = min_count >= MIN_EXPECTED_COUNT
    warnings: list[str] = []
    if not reliable:
        warnings.append(
            f"the normal approximation is unreliable here: the smallest expected count is {min_count:.1f} (< {MIN_EXPECTED_COUNT:g}); "
            "use an exact or simulation-based power calculation before relying on this sample size"
        )
    return PowerResult(
        baseline=p0, mde_pp=d_pp, target_rate=p1, alpha=a, n_comparisons=k, alpha_per_comparison=a_per, target_power=pw,
        allocation_ratio=r, n_control=n, n_treatment=nt, n_total_per_comparison=n + nt, achieved_power=achieved,
        previous_pair_power=prev, min_expected_count=min_count, approximation_reliable=reliable, warnings=warnings,
        assumptions=[
            "Sessions are independent and identically distributed within an arm, with no carryover between sessions.",
            f"The test is two-sided at alpha = {a:g}" + (f" divided by {k} comparisons ({a_per:g} each, Bonferroni)" if k > 1 else "") + ".",
            "Power is per comparison" + (", not the probability that every comparison is detected." if k > 1 else "."),
            "The effect is the planning value; the true effect is unknown, and a smaller one needs a larger sample.",
        ],
    )


# --------------------------------------------------------------------------- design


class BaselineInfo(BaseModel):
    model_config = ConfigDict(frozen=True)

    rate: float = Field(description="Orders per session of the focal segment over the usable post-period days.")
    orders: int
    sessions: int
    n_days: int
    first_day: Date
    last_day: Date
    requested_window: tuple[Date, Date]
    excluded_dates: list[Date] = Field(description="Flagged days removed inside the requested window.")
    population: str


class TrafficInfo(BaseModel):
    model_config = ConfigDict(frozen=True)

    mean_daily_sessions: float
    n_days: int
    covers_full_week: bool
    assumption: str


class Guardrail(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    kind: Literal["assignment_diagnostic", "external_monitor", "requires_instrumentation"]
    computable_from_current_data: bool
    detail: str
    reference: dict[str, float | int] | None = None


class MdeInfo(BaseModel):
    model_config = ConfigDict(frozen=True)

    mde_pp: float
    source: Literal["user_declared", "provisional_planning_heuristic"]
    label: str
    observed_effect: Interval | None = Field(None, description="The differential effect the heuristic started from.")
    observed_for: str | None = None
    caveats: list[str]


class ArmPlan(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    role: Literal["control", "rollback"]
    rollback_of: str | None
    n_sessions: int
    allocation_share: float
    days_to_fill: float


class ExperimentPlan(BaseModel):
    model_config = ConfigDict(frozen=True)

    arms: list[ArmPlan]
    n_comparisons: int
    power: PowerResult
    slowest_arm: str
    duration_days: int = Field(description="ceil of the slowest-filling arm's days.")
    duration_weeks: int
    duration_days_rounded_to_weeks: int


class ExperimentDesign(BaseModel):
    model_config = ConfigDict(frozen=True)

    status: Literal["ok", "needs_input"]
    purpose: Literal["settle", "confirm"] | None = None
    needs: list[str] = Field(default_factory=list)
    reason: str | None = None
    focal: str | None = None
    target: list[str] = Field(default_factory=list)
    evidence_strength: str | None = None
    hypothesis: str | None = None
    intervention: str | None = None
    randomization_unit: str | None = None
    primary_metric: str | None = None
    guardrails: list[Guardrail] = Field(default_factory=list)
    mde: MdeInfo | None = None
    baseline: BaselineInfo | None = None
    traffic: TrafficInfo | None = None
    plan: ExperimentPlan | None = None
    alternative_plan: ExperimentPlan | None = Field(
        None, description="Shared-control multi-arm option, offered when more than one candidate is still open."
    )
    alternative_note: str | None = None
    assumptions: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    summary: str


def _needs(reason: str, needs: Sequence[str], **kw) -> ExperimentDesign:
    return ExperimentDesign(status="needs_input", needs=list(needs), reason=reason, summary=f"Experiment design needs input: {reason}", **kw)


def _window(w: Window) -> tuple[Date, Date]:
    try:
        a, b = (pd.Timestamp(x).date() for x in w)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"post_window must be (start, end) dates: {exc}") from None
    if a > b:
        raise ValueError(f"post_window starts ({a}) after it ends ({b})")
    return a, b


def _effect_for(c: CandidateVerdict, panel: Panel) -> Interval | None:
    """The candidate's differential effect: from Task 5 if it was computed, otherwise a default HC1 DiD at its onset."""
    d = c.differential_effect if c.differential_effect is not None else did_on_panel(panel, c.onset_date)
    return d.effect_pp if d.status == "ok" and d.effect_pp is not None else None


def _heuristic_mde(effect: Interval) -> float | None:
    """Reverse the sign of the interval bound nearest zero (a rollback undoes the observed effect); None if it spans 0."""
    if effect.lower > 0:
        return -effect.lower
    if effect.upper < 0:
        return -effect.upper
    return None


def _size_mde(cands: Sequence[CandidateVerdict], panel: Panel) -> tuple[float, Interval, str] | str:
    """Heuristic MDE over the arms: the smallest magnitude wins so every arm is powered; a string explains a failure."""
    found: list[tuple[float, Interval, str]] = []
    for c in cands:
        eff = _effect_for(c, panel)
        if eff is None:
            return f"no usable estimate to size the experiment for {c.event}: no differential-effect estimate is available"
        h = _heuristic_mde(eff)
        if h is None:
            return (f"no usable estimate to size the experiment for {c.event}: its differential-effect interval "
                    f"({eff.lower:+.3f} to {eff.upper:+.3f} pp) includes 0")
        found.append((h, eff, c.event))
    return min(found, key=lambda t: abs(t[0]))


def _plan(
    arms: list[tuple[str, str]], baseline: float, mde_pp: float, daily_sessions: float, *, alpha: float, power: float, allocation_ratio: float
) -> ExperimentPlan:
    k = len(arms)
    res = power_analysis(baseline, mde_pp, alpha=alpha, power=power, allocation_ratio=allocation_ratio, n_comparisons=k)
    r = res.allocation_ratio
    control_share = 1.0 / (1.0 + k * r)
    treat_share = r / (1.0 + k * r)
    plan_arms = [ArmPlan(name="control (current experience retained)", role="control", rollback_of=None, n_sessions=res.n_control,
                         allocation_share=control_share, days_to_fill=res.n_control / (daily_sessions * control_share))]
    for event, _ in arms:
        plan_arms.append(ArmPlan(name=f"rollback of {event}", role="rollback", rollback_of=event, n_sessions=res.n_treatment,
                                 allocation_share=treat_share, days_to_fill=res.n_treatment / (daily_sessions * treat_share)))
    slowest = max(plan_arms, key=lambda a: a.days_to_fill)
    days = max(1, math.ceil(slowest.days_to_fill - 1e-9))
    weeks = math.ceil(days / 7)
    return ExperimentPlan(arms=plan_arms, n_comparisons=k, power=res, slowest_arm=slowest.name, duration_days=days,
                          duration_weeks=weeks, duration_days_rounded_to_weeks=7 * weeks)


def design_experiment(
    metrics: pd.DataFrame,
    candidate_testing: CandidateTestingResult,
    post_window: Window,
    *,
    events: pd.DataFrame | None = None,
    quality: DataQualityReport | None = None,
    apply_exclusions: bool = True,
    target_candidate: str | None = None,
    arms: Literal["leading", "each_candidate"] = "leading",
    mde_pp: float | None = None,
    alpha: float = 0.05,
    power: float = 0.8,
    allocation_ratio: float = 1.0,
) -> ExperimentDesign:
    """A randomized rollback experiment for the focal segment, sized by ``power_analysis`` on observed traffic.

    The default is a two-arm rollback of the leading candidate (other releases held fixed). ``arms="each_candidate"``
    plans one rollback arm per still-open candidate against a shared control, when independent rollbacks are feasible.
    ``mde_pp`` is the signed percentage-point change the experiment should detect (a business target, preferred).
    Without it, a provisional planning heuristic is used when the interval excludes 0; otherwise the result is
    ``needs_input``. Raises ``ValueError`` for invalid arguments.
    """
    if arms not in ("leading", "each_candidate"):
        raise ValueError("arms must be 'leading' or 'each_candidate'")
    if arms == "each_candidate" and target_candidate is not None:
        raise ValueError("target_candidate and arms='each_candidate' are mutually exclusive")
    declared = None if mde_pp is None else _real(mde_pp, "mde_pp")
    if declared == 0.0:
        raise ValueError("mde_pp must be non-zero")
    _check_inputs(0.5, 1.0, alpha, power, allocation_ratio, 1)  # validates alpha, power and allocation up front
    window = _window(post_window)
    names = {c.event for c in candidate_testing.candidates}
    if target_candidate is not None and target_candidate not in names:
        raise ValueError(f"target_candidate {target_candidate!r} is not a candidate; candidates are {sorted(names)}")

    focal = candidate_testing.focal
    if candidate_testing.status != "ok" or focal is None or not candidate_testing.candidates:
        return _needs(
            "there is no candidate release to randomize (" + candidate_testing.summary + ") Name the intervention to test, "
            "or consider a randomized experiment on the broad change.", ["candidate_event"],
            evidence_strength="insufficient",
        )

    # ---- which candidate(s) -------------------------------------------------------------
    open_ = [c for c in candidate_testing.candidates if c.verdict != "rejected"]
    if arms == "each_candidate":
        if len(open_) < 2:
            raise ValueError("arms='each_candidate' needs at least two candidates that are not rejected")
        targets = open_
    elif target_candidate is not None:
        targets = [next(c for c in candidate_testing.candidates if c.event == target_candidate)]
    elif candidate_testing.leading is not None:
        targets = [next(c for c in candidate_testing.candidates if c.event == candidate_testing.leading)]
    elif len(open_) == 1:
        targets = open_
    else:
        listed = ", ".join(c.event for c in open_) or "none (every candidate was rejected as the sole source)"
        return _needs(
            f"no single candidate leads; open candidates: {listed}. Name one with target_candidate, or use arms='each_candidate'.",
            ["target_candidate"], focal=focal.label,
        )
    lead = targets[0]
    strength = lead.evidence.strength if (len(targets) == 1 and lead.evidence is not None) else "insufficient"
    purpose: Literal["settle", "confirm"] = "confirm" if strength == "strong" else "settle"

    # ---- baseline and traffic from the observed post period ------------------------------
    panel = build_panel(metrics, focal.segments, events=events, quality=quality, apply_exclusions=apply_exclusions)
    d = panel.daily
    inside = (d.index >= pd.Timestamp(window[0])) & (d.index <= pd.Timestamp(window[1]))
    use = d[inside & d["usable"].to_numpy(dtype=bool)]
    excluded = [x for x in panel.excluded_dates if window[0] <= x <= window[1]]
    base_kw = {"focal": focal.label, "evidence_strength": strength, "target": [c.event for c in targets]}
    if len(use) < MIN_BASELINE_DAYS:
        return _needs(
            f"only {len(use)} usable post-period days in {window[0]} to {window[1]} (need {MIN_BASELINE_DAYS} to cover a weekday cycle)",
            ["post_window"], **base_kw,
        )
    orders, sessions = int(use["o_f"].sum()), int(use["s_f"].sum())
    if sessions <= 0 or orders <= 0 or orders >= sessions:
        return _needs(
            f"the post-period conversion rate ({orders} orders in {sessions} sessions) is not strictly between 0 and 1, so a "
            "rate-based power calculation is undefined", ["baseline"], **base_kw,
        )
    baseline_rate = orders / sessions
    baseline = BaselineInfo(
        rate=baseline_rate, orders=orders, sessions=sessions, n_days=len(use), first_day=use.index[0].date(), last_day=use.index[-1].date(),
        requested_window=window, excluded_dates=excluded, population=focal.label,
    )
    daily = float(use["s_f"].mean())
    traffic = TrafficInfo(
        mean_daily_sessions=daily, n_days=len(use), covers_full_week=len(use) >= 7,
        assumption=f"traffic continues at the observed rate of {daily:,.0f} eligible sessions per day (mean of {len(use)} usable post-period days); all eligible sessions are enrolled",
    )

    # ---- the minimum detectable effect ---------------------------------------------------
    if declared is not None:
        mde = MdeInfo(
            mde_pp=declared, source="user_declared", label="user-declared business target", observed_effect=None, observed_for=None,
            caveats=["Declared by the user; it is not derived from this data."],
        )
    else:
        sized = _size_mde(targets, panel)
        if isinstance(sized, str):
            return _needs(sized + ". Declare a business target with mde_pp.", ["mde_pp"], baseline=baseline, traffic=traffic, **base_kw)
        h, eff, event = sized
        mde = MdeInfo(
            mde_pp=h, source="provisional_planning_heuristic", label="provisional planning heuristic (not a business target)",
            observed_effect=eff, observed_for=event,
            caveats=[
                (
                    f"Reverses the sign of the {100 * eff.level:g}% interval bound nearest zero ({eff.lower:+.3f} to {eff.upper:+.3f} pp), "
                    "so a rollback is sized to undo the smallest effect the data still supports."
                ),
                (
                    "It does not correct winner's curse or selection bias: the estimate was selected because it was notable, and the "
                    "true effect of a rollback can be smaller."
                ),
                "Prefer a declared business target (mde_pp).",
            ] + (["Several arms: the smallest-magnitude value over the arms sizes every arm."] if len(targets) > 1 else []),
        )

    # ---- plans ---------------------------------------------------------------------------
    pairs = [(c.event, c.event) for c in targets]
    try:
        plan = _plan(pairs, baseline_rate, mde.mde_pp, daily, alpha=alpha, power=power, allocation_ratio=allocation_ratio)
    except ValueError as exc:
        return _needs(f"the sample-size calculation is not possible: {exc}", ["mde_pp"], baseline=baseline, traffic=traffic, mde=mde, **base_kw)

    alt_plan = alt_note = None
    if arms == "leading" and len(open_) >= 2:
        alt_mde: float | str = declared if declared is not None else _size_mde(open_, panel)
        if not isinstance(alt_mde, str):
            alt_value = alt_mde if declared is not None else alt_mde[0]
            try:
                alt_plan = _plan([(c.event, c.event) for c in open_], baseline_rate, alt_value, daily, alpha=alpha, power=power,
                                 allocation_ratio=allocation_ratio)
                alt_note = (
                    f"Offered because {len(open_)} candidates are still open: if independent rollbacks are feasible and comparing their "
                    f"contributions is the objective, run {len(open_)} rollback arms against one shared control (alpha {alpha:g} divided by "
                    f"{len(open_)}; power {power:g} is per comparison, not for the whole experiment). Its minimum detectable effect is "
                    f"{alt_value:+.3g} pp."
                )
            except ValueError:
                alt_plan = None

    # ---- wording --------------------------------------------------------------------------
    target_names = " and ".join(c.event for c in targets)
    direction = "raise" if mde.mde_pp > 0 else "lower"
    intervention = (
        f"Randomly assign sessions in {focal.label} to keep the current experience (control) or to roll back {target_names}, "
        "holding every other release fixed."
        if len(targets) == 1 else
        f"Randomly assign sessions in {focal.label} to the current experience (one shared control) or to a rollback of exactly one of: {target_names}; other releases stay fixed."
    )
    hypothesis = (
        f"If {target_names} currently contributes to the conversion of {focal.label}, then rolling it back for randomly assigned sessions "
        f"would {direction} conversion by about {abs(mde.mde_pp):.3g} pp relative to the sessions that keep it. A null result would mean "
        "the rollback does not change conversion by an amount this experiment can detect. This tests the release's current contribution "
        "and cannot establish that it was the sole historical cause."
    )
    non_focal = {"sessions": int(use["s_c"].sum()), "orders": int(use["o_c"].sum())}
    ref = ({**non_focal, "conversion_rate": non_focal["orders"] / non_focal["sessions"]} if non_focal["sessions"] > 0 else non_focal)
    guardrails = [
        Guardrail(name="Sample-ratio mismatch (SRM)", kind="assignment_diagnostic", computable_from_current_data=False,
                  detail="Compare the assigned arm sizes with the planned allocation. Needs the experiment's assignment logs; it cannot be computed from the metrics table."),
        Guardrail(name="Conversion in traffic outside the experiment", kind="external_monitor", computable_from_current_data=True,
                  detail="Watch conversion of the sessions that are not enrolled, as a monitor for a concurrent change. It is only meaningful while those sessions stay outside the experiment. Reference: the same days, outside the focal segment.",
                  reference=ref),
        Guardrail(name="Revenue or order value per session", kind="requires_instrumentation", computable_from_current_data=False,
                  detail="The metrics table has sessions and orders only, so value per order cannot be measured; it needs new instrumentation."),
        Guardrail(name="Checkout errors and page latency", kind="requires_instrumentation", computable_from_current_data=False,
                  detail="Not measured in the current data; it needs new instrumentation."),
    ]
    assumptions = [
        "Randomizing by session assumes sessions are independent and that there is no carryover between a visitor's sessions; the data has no visitor ids, so neither condition is established. Visitor-level randomization would need identifiers.",
        traffic.assumption + ".",
        f"Duration is the slowest-filling arm's days, rounded up to whole weeks ({plan.duration_days} days, {plan.duration_weeks} week(s)); the shared control is counted once.",
        "The rollback tests the current contribution of the release with other releases held fixed; it does not establish the sole historical cause.",
        f"The baseline is the usable post-period focal conversion ({baseline.orders:,} orders in {baseline.sessions:,} sessions, {baseline.first_day} to {baseline.last_day}), the experience the control arm keeps.",
        "Sample size and power use the normal approximation; see the warnings for whether it is reliable here.",
    ] + plan.power.assumptions
    warnings = list(plan.power.warnings)
    if excluded:
        warnings.append("flagged days " + ", ".join(str(x) for x in excluded) + " were removed from the baseline and traffic")
    if mde.source == "provisional_planning_heuristic":
        warnings.append("the minimum detectable effect is a provisional planning heuristic, not a business target")
    summary = (
        f"{'Confirmation' if purpose == 'confirm' else 'Settling'} experiment (evidence {strength}): {len(plan.arms)}-arm randomized rollback in "
        f"{focal.label}. Detect {mde.mde_pp:+.3g} pp from a baseline of {100 * baseline_rate:.3g}% [{mde.label}] with "
        f"{plan.power.n_control:,} control and {plan.power.n_treatment:,} sessions per rollback arm; {ACHIEVED_POWER_LABEL} is "
        f"{plan.power.achieved_power:.3f} per comparison at alpha {plan.power.alpha_per_comparison:g}. The slowest arm fills in about "
        f"{plan.duration_days} day(s) of observed traffic, so plan {plan.duration_days_rounded_to_weeks} days ({plan.duration_weeks} week(s)) "
        "when rounded up to whole weeks."
    )
    return ExperimentDesign(
        status="ok", purpose=purpose, hypothesis=hypothesis, intervention=intervention,
        randomization_unit="session (a planning assumption: requires independent sessions and no cross-session carryover)",
        primary_metric=f"conversion rate (orders per session) of {focal.label}", guardrails=guardrails, mde=mde, baseline=baseline,
        traffic=traffic, plan=plan, alternative_plan=alt_plan, alternative_note=alt_note, assumptions=assumptions, warnings=warnings,
        summary=summary, **base_kw,
    )
