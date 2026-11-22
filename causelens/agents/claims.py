"""When a causal claim is allowed (scientist-rules.md, "Claims").

A claim that a named event accounts for part of the change needs all of: a named identification design (here
difference-in-differences, from ``estimate_did``), a verdict of ``supported`` for the event, a valid comparison, an interval
that excludes zero, and passed pre-trend and placebo checks. Anything less is reported as "consistent with", never as a cause.
These are comparisons of logged values. No number is produced here.
"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from causelens.agents.state import InvestigationState
from causelens.analytics import DidResult, PlaceboResult, PreTrendResult

DESIGN = "difference-in-differences"


class ClaimEligibility(BaseModel):
    """Whether the causal claim for one event is allowed, and which logged calls it rests on."""

    model_config = ConfigDict(frozen=True)

    event: str
    allowed: bool
    reason: str
    did_call: int
    pre_trend_call: int
    placebo_call: int
    verdict_call: int
    verdict_path: str
    design: str = DESIGN


def claim_base(claim: ClaimEligibility) -> str:
    """The pointer of the candidate the claim is about, for example ``/candidates/1``."""
    return claim.verdict_path.rsplit("/", 1)[0]


def required_refs(state: InvestigationState, claim: ClaimEligibility) -> dict[str, tuple[int, str, str]]:
    """What a causal sentence for this claim must quote, as ``name -> (call, pointer, format)``.

    The event, its evaluated lag and onset come from the candidate test; the estimate, its interval, level and sample size come
    from the difference-in-differences call; and every assumption of that call is quoted. A sentence that lacks any of these, or
    quotes another candidate, is not this claim.
    """
    base = claim_base(claim)
    did = state.call(claim.did_call).result
    assert did is not None
    refs: dict[str, tuple[int, str, str]] = {
        "event": (claim.verdict_call, f"{base}/event", "text"),
        "onset": (claim.verdict_call, f"{base}/onset_date", "date"),
        "lag": (claim.verdict_call, f"{base}/lag_days", "count"),
        "effect": (claim.did_call, "/effect_pp/estimate", "pp"),
        "lower": (claim.did_call, "/effect_pp/lower", "pp"),
        "upper": (claim.did_call, "/effect_pp/upper", "pp"),
        "level": (claim.did_call, "/effect_pp/level", "level"),
        "n": (claim.did_call, "/n_obs", "count"),
    }
    refs.update({f"a{i}": (claim.did_call, f"/assumptions/{i}", "text") for i in range(len(did["assumptions"]))})
    return refs


def ref_belongs_to_claim(claim: ClaimEligibility, call: int, path: str) -> bool:
    """Whether a reference may appear in a sentence about this claim (nothing about another candidate may)."""
    if call == claim.verdict_call:
        return path.startswith((claim_base(claim) + "/", "/focal/"))
    return call in (claim.did_call, claim.pre_trend_call, claim.placebo_call)


def assess_claim(
    state: InvestigationState, *, event: str, verdict_call: int, verdict_path: str, did_call: int, pre_trend_call: int, placebo_call: int,
) -> ClaimEligibility:
    did: DidResult = state.model(did_call)
    pre: PreTrendResult = state.model(pre_trend_call)
    placebo: PlaceboResult = state.model(placebo_call)
    verdict = state.call(verdict_call).result
    assert verdict is not None
    node = verdict
    for part in verdict_path.strip("/").split("/"):
        node = node[int(part)] if isinstance(node, list) else node[part]
    problems: list[str] = []
    if node != "supported":
        problems.append("the candidate is not supported")
    if did.status != "ok" or not did.control_valid or did.effect_pp is None:
        problems.append("there is no valid comparison for a difference-in-differences estimate")
    elif did.effect_pp.lower <= 0.0 <= did.effect_pp.upper:
        problems.append("the interval of the estimate includes zero")
    if pre.status != "pass":
        problems.append(f"the pre-trend check did not pass ({pre.status})")
    if placebo.status != "pass":
        problems.append(f"the placebo check did not pass ({placebo.status})")
    reason = "; ".join(problems) if problems else "the design's checks passed and the candidate is supported"
    return ClaimEligibility(
        event=event, allowed=not problems, reason=reason, did_call=did_call, pre_trend_call=pre_trend_call,
        placebo_call=placebo_call, verdict_call=verdict_call, verdict_path=verdict_path,
    )
