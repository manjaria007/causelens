"""Evidence strength (Task 5.4; design.md, "Evidence strength").

Strength is assigned in two steps, and the second can only lower it:

1. From the estimate and the required checks alone. A required check that was not run, or could not be
   evaluated, counts as *not passed*.

   ``strong``        valid comparison, DiD CI excludes 0, pre-trends p > 0.10, all placebos non-significant,
                     every required check run and passed
   ``moderate``      valid comparison, CI excludes 0, but one check is borderline
   ``weak``          CI excludes 0 but a check fails or was not run
   ``insufficient``  CI includes 0, or there is no valid comparison

2. If release attribution is ambiguous, strength is capped at ``moderate``. Ambiguity never raises ``weak``
   or ``insufficient``.

Pure and deterministic: the function only reads what it is given.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from causelens.analytics.did import DidResult, PlaceboResult, PreTrendResult

Strength = Literal["strong", "moderate", "weak", "insufficient"]
ORDER: dict[str, int] = {"insufficient": 0, "weak": 1, "moderate": 2, "strong": 3}


class RequiredCheck(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    outcome: Literal["pass", "borderline", "fail", "not_run"]
    detail: str | None = None


class EvidenceAssessment(BaseModel):
    model_config = ConfigDict(frozen=True)

    strength: Strength
    base_strength: Strength = Field(description="Step 1, before the attribution cap.")
    capped_by_ambiguity: bool
    checks: list[RequiredCheck]
    reasons: list[str]


def _cap(strength: Strength, ceiling: Strength) -> Strength:
    return strength if ORDER[strength] <= ORDER[ceiling] else ceiling


def assess_evidence(
    *,
    did: DidResult | None,
    pre_trends: PreTrendResult | None,
    placebo: PlaceboResult | None,
    timing_assumptions: Literal["pass", "fail", "not_run"],
    exposure_consistent: bool | None,
    pattern_consistent: bool | None,
    attribution_ambiguous: bool,
) -> EvidenceAssessment:
    """Assign ``strong`` / ``moderate`` / ``weak`` / ``insufficient`` with the reasons.

    ``exposure_consistent`` and ``pattern_consistent`` are ``True`` (consistent), ``False`` (contradicted) or
    ``None`` (not assessed or untested); only a contradiction is a failed check, because an undeclared pattern
    is not a required check.
    """
    reasons: list[str] = []
    checks: list[RequiredCheck] = []

    def add(name: str, outcome: Literal["pass", "borderline", "fail", "not_run"], detail: str | None = None) -> None:
        checks.append(RequiredCheck(name=name, outcome=outcome, detail=detail))

    # --- the estimate and the comparison group
    if did is None or did.status != "ok" or did.effect_pp is None or not did.control_valid:
        why = (did.reason if did is not None and did.reason else "no DiD estimate or no valid comparison segment")
        reasons.append(f"insufficient: {why}")
        return EvidenceAssessment(strength="insufficient", base_strength="insufficient", capped_by_ambiguity=False,
                                  checks=checks, reasons=reasons)
    ci = did.effect_pp
    if ci.lower <= 0 <= ci.upper:
        reasons.append(f"insufficient: the DiD interval ({ci.lower:+.3f} to {ci.upper:+.3f} pp) includes 0")
        return EvidenceAssessment(strength="insufficient", base_strength="insufficient", capped_by_ambiguity=False,
                                  checks=checks, reasons=reasons)

    # --- required checks
    if pre_trends is None or pre_trends.status == "not_evaluable":
        add("pre-trends", "not_run", pre_trends.reason if pre_trends else "not run")
    else:
        add("pre-trends", pre_trends.status, f"p = {pre_trends.p_value:.3g}" if pre_trends.p_value is not None else None)
    if placebo is None or placebo.status == "not_evaluable":
        add("placebo", "not_run", placebo.reason if placebo else "not run")
    else:
        add("placebo", placebo.status)
    add("timing assumptions", {"pass": "pass", "fail": "fail", "not_run": "not_run"}[timing_assumptions])
    if exposure_consistent is False:
        add("exposure scope", "fail", "the change is outside the event's exposure scope")
    elif exposure_consistent is True:
        add("exposure scope", "pass")
    if pattern_consistent is False:
        add("declared pattern", "fail", "a declared expected pattern is contradicted")
    elif pattern_consistent is True:
        add("declared pattern", "pass")

    failed = [c for c in checks if c.outcome in ("fail", "not_run")]
    borderline = [c for c in checks if c.outcome == "borderline"]
    if failed:
        base: Strength = "weak"
        reasons.append("weak: " + "; ".join(f"{c.name} {'failed' if c.outcome == 'fail' else 'was not run or not evaluable'}" for c in failed))
    elif borderline:
        base = "moderate"
        reasons.append("moderate: " + "; ".join(f"{c.name} is borderline" for c in borderline))
    else:
        base = "strong"
        reasons.append("strong: the DiD interval excludes 0 and every required check was run and passed")

    final = base
    capped = False
    if attribution_ambiguous:
        final = _cap(base, "moderate")
        capped = ORDER[final] < ORDER[base]
        reasons.append(
            "release attribution is ambiguous (another candidate can account for the same onset date at an allowed lag)"
            + ("; strength capped at moderate" if capped else "; the cap does not apply to a strength already at or below moderate")
        )
    return EvidenceAssessment(strength=final, base_strength=base, capped_by_ambiguity=capped, checks=checks, reasons=reasons)
