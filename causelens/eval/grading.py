"""Grade one investigation against the answer key (Requirement 10.2).

The answer key (``ground_truth.json``, or the same structure generated for another seed) is read only here, after the
investigation has finished. The investigation runs against a server that was configured with the metrics and events files alone.

``extract`` reads what the investigation reported (from the structured result, whose numbers are traced to its log) and ``grade``
compares it with the key. A criterion passes only on positive evidence in the result: an investigation that stopped early, found no
estimate, or never localized fails the criteria it did not meet.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from causelens.agents import InvestigationResult

CRITERIA = ("identifies_cause", "localizes_segment", "rejects_decoy", "flags_data_quality", "ci_covers_truth")

CRITERION_TEXT = {
    "identifies_cause": "names the planted event, and only it, as supported",
    "localizes_segment": "localizes the change to the planted segment",
    "rejects_decoy": "rejects every decoy event",
    "flags_data_quality": "flags every planted data-quality day",
    "ci_covers_truth": "its interval for the planted event covers the true segment effect",
}


class Findings(BaseModel):
    """What an investigation reported, reduced to what grading needs."""

    model_config = ConfigDict(frozen=True)

    status: str
    supported: list[str]
    rejected: list[str]
    localized_segments: list[str] | None
    flagged_dates: list[str]
    excluded_dates: list[str]
    #: event -> (lower, upper) of the difference-in-differences interval, for events that have an estimate
    intervals: dict[str, tuple[float, float]]
    strength: str
    ambiguous_events: list[str] = Field(default_factory=list)


class Criterion(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: Literal["identifies_cause", "localizes_segment", "rejects_decoy", "flags_data_quality", "ci_covers_truth"]
    passed: bool
    detail: str


class Grade(BaseModel):
    model_config = ConfigDict(frozen=True)

    criteria: list[Criterion]
    passed: bool = Field(description="True only if every criterion passed.")
    wrong_event_supported: list[str] = Field(description="Events supported that are not the planted cause (an incorrect attribution).")
    has_estimate: bool = Field(description="Whether the investigation produced an interval for the planted event (needed to judge coverage).")


def extract(result: InvestigationResult) -> Findings:
    """Read the graded facts out of a structured result."""
    quality = result.data_quality
    localization = result.localization
    return Findings(
        status=result.status,
        supported=[c.event.value for c in result.candidates if c.verdict.value == "supported"],  # type: ignore[misc]
        rejected=[c.event.value for c in result.candidates if c.verdict.value == "rejected"],  # type: ignore[misc]
        localized_segments=[s.value for s in localization.segments] if localization and localization.localized else None,  # type: ignore[misc]
        flagged_dates=sorted({f.date.value for f in quality.flags}) if quality else [],  # type: ignore[misc]
        excluded_dates=[d.value for d in quality.excluded_dates] if quality else [],  # type: ignore[misc]
        intervals={
            e.event.value: (e.effect_pp.lower, e.effect_pp.upper)  # type: ignore[misc]
            for e in result.causal_estimates if e.effect_pp is not None
        },
        strength=result.evidence.strength,
        ambiguous_events=[c.event.value for c in result.candidates if c.attribution_ambiguous.value is True],  # type: ignore[misc]
    )


def segment_label_parts(segment: dict[str, str]) -> set[str]:
    return {f"{k}={v}" for k, v in segment.items()}


def _label_parts(label: str) -> set[str]:
    return {part.strip() for part in label.split(",") if part.strip()}


def grade(findings: Findings, truth: dict[str, Any]) -> Grade:
    """Grade ``findings`` against an answer key shaped like ``data/ground_truth.json``."""
    key = truth["grading"]
    planted = truth["planted_cause"]
    cause = key["must_identify_event"]
    out: list[Criterion] = []

    only_cause = findings.supported == [cause]
    out.append(Criterion(
        name="identifies_cause", passed=only_cause,
        detail=f"supported: {findings.supported or 'none'} (status {findings.status})",
    ))

    want = segment_label_parts(key["must_localize_to"])
    got = findings.localized_segments
    out.append(Criterion(
        name="localizes_segment", passed=got is not None and len(got) == 1 and _label_parts(got[0]) == want,
        detail=f"localized to: {got if got is not None else 'nothing'}",
    ))

    decoys = list(key["must_reject"])
    missed = [d for d in decoys if d not in findings.rejected]
    out.append(Criterion(
        name="rejects_decoy", passed=not missed,
        detail="rejected: " + (", ".join(findings.rejected) or "none") + (f"; not rejected: {missed}" if missed else ""),
    ))

    wanted_days = list(key["must_flag_data_quality"])
    absent = [d for d in wanted_days if d not in findings.flagged_dates or d not in findings.excluded_dates]
    out.append(Criterion(
        name="flags_data_quality", passed=not absent,
        detail=f"flagged and excluded: {findings.excluded_dates or 'none'}" + (f"; missing: {absent}" if absent else ""),
    ))

    true_effect = planted[key["ci_must_cover"]]
    interval = findings.intervals.get(cause)
    covered = interval is not None and interval[0] <= true_effect <= interval[1]
    out.append(Criterion(
        name="ci_covers_truth", passed=covered,
        detail=("no interval for the planted event" if interval is None else f"interval [{interval[0]:.4f}, {interval[1]:.4f}]")
        + f" against the true {true_effect:.4f} pp",
    ))
    return Grade(
        criteria=out, passed=all(c.passed for c in out), wrong_event_supported=[e for e in findings.supported if e != cause],
        has_estimate=interval is not None,
    )
