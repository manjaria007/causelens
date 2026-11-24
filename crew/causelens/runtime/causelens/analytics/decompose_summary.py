"""A compact projection of a ``DecompositionResult`` for clients that cannot read the full result (PR #13).

The full decomposition lists every segment of every partition and runs to 150-250 kB of JSON, past the 30,000-character tool-output limit
of the kiro-cli client a native Crew agent uses. ``summarize_decomposition`` copies out of a full result what an investigation reports
from it: the windows, the settings, the exclusions and assumptions, the localization with its structured segment labels, each selected
segment's own effect, interval, sample size and FDR-adjusted test, the selected share and the partition's remainder exactly as the full
result has them (a negative remaining share stays negative), and the reasons when nothing is localized.

It computes nothing: every value is copied from the full result. It is a different type (``detail = "summary"``) and is not accepted where a
full decomposition is expected; a tool that needs the decomposition recomputes it from the same inputs. Pure and deterministic.
"""
from __future__ import annotations

from collections.abc import Sequence
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from causelens.analytics.decompose import (
    ComparisonWindow,
    DecompositionResult,
    ExplainingSet,
    Localization,
    SegmentEffect,
)
from causelens.analytics.series import ExclusionDisclosure

#: Segments of the localized partition listed (by rank) besides the selected ones, so a reader sees what came next.
TOP_OTHER_SEGMENTS = 3


class DecompositionSettings(BaseModel):
    """The arguments the decomposition was computed with (echoed, so the summary can be reproduced)."""

    model_config = ConfigDict(frozen=True)

    dimensions: list[str]
    include_pairs: bool
    alpha: float
    level: float
    explain_threshold: float
    max_localized_traffic_share: float
    fdr_family: Literal["scan", "partition"]
    inference: Literal["day_level", "binomial"]
    min_days_per_window: int
    apply_exclusions: bool


class SelectedSegment(BaseModel):
    """A segment the localization selected: its structured labels and its full effect record, copied from the partition."""

    model_config = ConfigDict(frozen=True)

    labels: dict[str, str] = Field(description="The segment as dimension -> value, e.g. {'device': 'mobile', 'browser': 'safari'}.")
    label: str
    effect: SegmentEffect


class OtherSegment(BaseModel):
    """A highly ranked segment of the localized partition that was not selected (contribution and its test only)."""

    model_config = ConfigDict(frozen=True)

    label: str
    rank: int
    contribution_pp: float
    rate_change_pp: float | None
    q_value: float | None
    significant: bool | None


class PartitionRemainder(BaseModel):
    """The localized partition's own accounting, copied from the full result."""

    model_config = ConfigDict(frozen=True)

    partition: str
    n_segments: int
    n_tests: int
    n_unreliable: int
    sum_rate_effects_pp: float
    sum_mix_effects_pp: float
    residual_pp: float
    reconciles: bool
    rate_effect_significant_pp: float
    rate_effect_not_distinguishable_pp: float
    selected_set_kind: Literal["local_set", "explaining_set"] | None = Field(
        description="Which of the partition's sets the localization is (the one whose segments it lists)."
    )
    selected_set: ExplainingSet | None = Field(description="That set verbatim, including remaining_share_of_change (it can be negative).")
    local_search_exhaustive: bool


class CausalAttribution(BaseModel):
    """What this tool can say about why the change happened: nothing. Fixed, so an agent quotes it instead of grading evidence itself."""

    model_config = ConfigDict(frozen=True)

    strength: Literal["insufficient"] = "insufficient"
    reason: str = (
        "insufficient for a causal attribution: a decomposition describes where the change is concentrated, not why; no identification "
        "design (difference-in-differences with pre-trend and placebo checks) and no candidate test has run in this call."
    )
    checks_run: list[str] = Field(default_factory=list, description="Causal checks run by this tool: none.")


class DecompositionSummary(BaseModel):
    """The compact decomposition (``detail="summary"``). Every value is copied from the full ``DecompositionResult``."""

    model_config = ConfigDict(frozen=True)

    detail: Literal["summary"] = "summary"
    note: str = (
        "A summary of decompose_change, not a full decomposition: it is not accepted where a decomposition is expected. Tools that need one "
        "(test_candidates, test_event_scope) recompute it when called with the same dataset, windows and settings and no decomposition."
    )
    status: Literal["ok", "insufficient_data"]
    metric: str
    pre: ComparisonWindow
    post: ComparisonWindow
    change_pp: float | None
    conversion_pre: float | None
    conversion_post: float | None
    weekday_mix_difference: float | None
    settings: DecompositionSettings
    n_tests_scanned: int
    n_unreliable_tests: int
    localization: Localization | None
    selected_segments: list[SelectedSegment]
    other_top_segments: list[OtherSegment]
    remainder: PartitionRemainder | None
    localization_note: str | None
    unavailable_reasons: dict[str, str] = Field(
        default_factory=dict, description="Per partition, why it has no explaining set or local set (only partitions that report one)."
    )
    partitions: list[str] = Field(description="Names of every partition in the full result.")
    causal_attribution: CausalAttribution = Field(default_factory=CausalAttribution)
    exclusions: ExclusionDisclosure
    assumptions: list[str]
    summary: str


def summarize_decomposition(
    full: DecompositionResult,
    *,
    dimensions: Sequence[str],
    include_pairs: bool,
    explain_threshold: float,
    max_localized_traffic_share: float,
    min_days_per_window: int,
    apply_exclusions: bool,
) -> DecompositionSummary:
    """Project ``full`` (computed with the given settings) to a ``DecompositionSummary``. Copies; never computes."""
    settings = DecompositionSettings(
        dimensions=list(dimensions), include_pairs=include_pairs, alpha=full.alpha, level=full.level, explain_threshold=explain_threshold,
        max_localized_traffic_share=max_localized_traffic_share, fdr_family=full.fdr_family, inference=full.inference,
        min_days_per_window=min_days_per_window, apply_exclusions=apply_exclusions,
    )
    loc = full.localization
    part = next((p for p in full.partitions if loc is not None and p.name == loc.partition), None)
    selected: list[SelectedSegment] = []
    others: list[OtherSegment] = []
    remainder = None
    if loc is not None and part is not None:
        by_label = {s.label: s for s in part.segments}
        selected = [SelectedSegment(labels=dict(by_label[name].labels), label=name, effect=by_label[name]) for name in loc.segments]
        others = [
            OtherSegment(label=s.label, rank=s.rank, contribution_pp=s.contribution_pp, rate_change_pp=s.rate_change_pp,
                         q_value=s.test.q_value if s.test else None, significant=s.test.significant if s.test else None)
            for s in sorted(part.segments, key=lambda s: (s.rank, s.label)) if s.label not in loc.segments
        ][:TOP_OTHER_SEGMENTS]
        kind: Literal["local_set", "explaining_set"] | None = None
        chosen: ExplainingSet | None = None
        for name in ("local_set", "explaining_set"):  # the same order the investigation report uses
            candidate = getattr(part, name)
            if candidate is not None and list(candidate.segments) == list(loc.segments):
                kind, chosen = name, candidate  # type: ignore[assignment]
                break
        remainder = PartitionRemainder(
            partition=part.name, n_segments=part.n_segments, n_tests=part.n_tests, n_unreliable=part.n_unreliable,
            sum_rate_effects_pp=part.sum_rate_effects_pp, sum_mix_effects_pp=part.sum_mix_effects_pp, residual_pp=part.residual_pp,
            reconciles=part.reconciles, rate_effect_significant_pp=part.rate_effect_significant_pp,
            rate_effect_not_distinguishable_pp=part.rate_effect_not_distinguishable_pp, selected_set_kind=kind, selected_set=chosen,
            local_search_exhaustive=part.local_search_exhaustive,
        )
    reasons: dict[str, str] = {}
    for p in full.partitions:
        texts = [t for t in (p.explaining_set_unavailable_reason, p.local_set_unavailable_reason) if t]
        if texts:
            reasons[p.name] = " ".join(dict.fromkeys(texts))
    return DecompositionSummary(
        status=full.status, metric=full.metric, pre=full.pre, post=full.post, change_pp=full.change_pp, conversion_pre=full.conversion_pre,
        conversion_post=full.conversion_post, weekday_mix_difference=full.weekday_mix_difference, settings=settings,
        n_tests_scanned=full.n_tests_scanned, n_unreliable_tests=full.n_unreliable_tests, localization=loc, selected_segments=selected,
        other_top_segments=others, remainder=remainder, localization_note=full.localization_note, unavailable_reasons=reasons,
        partitions=[p.name for p in full.partitions], exclusions=full.exclusions, assumptions=list(full.assumptions), summary=full.summary,
    )
