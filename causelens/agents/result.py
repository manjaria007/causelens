"""The structured investigation result (Requirement 8.1) in the shape the scientist rules require.

Every number is a ``Traced`` or ``TracedInterval``: the value was read from a logged tool result and carries the call and
pointer it came from. The only plain integers are log positions. Text that comes from a tool is a ``Traced`` of unit
``text``. The few fixed phrases the orchestrator writes itself (``wording.py``) contain no digits.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from causelens.agents.claims import ClaimEligibility
from causelens.agents.state import InvestigationRequest, StageRecord
from causelens.agents.trace import RenderedStatement, Traced, TracedInterval

Strength = Literal["strong", "moderate", "weak", "insufficient"]
Status = Literal[
    "completed",  # every stage ran
    "stopped_change_not_confirmed",  # Requirement 2.3
    "stopped_insufficient_data",
    "stopped_tool_error",
]


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True)


class DatasetFacts(_Frozen):
    """Which exact data the numbers come from (``list_datasets``)."""

    id: Traced
    n_rows: Traced
    first_date: Traced
    last_date: Traced
    metrics_sha256: Traced
    events_sha256: Traced | None


class DataQualityFlagNote(_Frozen):
    date: Traced
    segment: Traced
    kind: Traced
    reason: Traced
    linked_event: Traced | None


class DataQualityNotes(_Frozen):
    n_days_checked: Traced
    excluded_dates: list[Traced]
    missing_dates: list[Traced]
    exclusion_statement: Traced
    flags: list[DataQualityFlagNote]
    note: str = Field(description="A tracking break is a valid explanation; flags mark possible data issues, not confirmed ones.")


class ConfirmedChange(_Frozen):
    status: Traced
    verdict: Traced
    summary: Traced
    pre_conversion: TracedInterval | None
    post_conversion: TracedInterval | None
    abs_change_pp: TracedInterval | None
    rel_change: TracedInterval | None
    noise_p_value: Traced | None = Field(description="Reported only next to the effect size above.")
    effect_size_pre_sd: Traced | None
    data_needed: list[Traced] = Field(default_factory=list, description="How much more data would be needed, when the change is not confirmed.")


class ChangePointFinding(_Frozen):
    status: Traced
    best_date: Traced | None
    plausible_dates: list[Traced]
    plausible_level: Traced
    direction: Traced | None
    summary: Traced
    matched_events: list[Traced] = Field(description="Event names within the plausible range or up to the lead window before it: date coincidences only.")
    match_statement: Traced | None


class LocalizationFinding(_Frozen):
    localized: bool
    status: Traced
    overall_change_pp: Traced | None
    partition: Traced | None
    segments: list[Traced]
    share_of_change: Traced | None
    traffic_share_post: Traced | None
    all_segments_significant: Traced | None
    statement: Traced | None
    reason_if_not_localized: Traced | None
    n_tests_scanned: Traced
    fdr_family: Traced
    alpha: Traced
    multiple_comparisons: str


class LagView(_Frozen):
    lag_days: Traced
    onset_date: Traced
    verdict: Traced
    headline: Traced


class CandidateFinding(_Frozen):
    event: Traced
    event_type: Traced
    event_date: Traced
    lag_days: Traced
    onset_date: Traced
    verdict: Traced
    headline: Traced
    hypothesis: Traced
    reasons: list[Traced]
    attribution_ambiguous: Traced
    attribution_note: Traced
    evidence_strength: Traced | None
    lag_sensitivity: list[LagView]
    why: str = Field(description="Why the candidate was kept as the leading explanation, kept as an alternative, or rejected.")


class CausalEstimate(_Frozen):
    event: Traced
    onset_date: Traced
    focal: Traced
    comparison: Traced
    design: str
    effect_pp: TracedInterval | None
    status: Traced
    overall_pp: TracedInterval | None
    lost_orders: TracedInterval | None
    pre_trend_status: Traced
    pre_trend_slope_pp_per_day: TracedInterval | None
    pre_trend_p_value: Traced | None
    placebo_status: Traced
    placebo_n_evaluated: Traced
    assumptions: list[Traced]
    attribution_label: str
    claim: ClaimEligibility
    matches_candidate_test: bool = Field(description="The standalone difference-in-differences equals the one embedded in the candidate test.")


class Unexplained(_Frozen):
    remaining_share_of_change: Traced | None
    rate_effect_not_distinguishable_pp: Traced | None
    explained_by_a_supported_event: bool
    note: str


class EvidenceSummary(_Frozen):
    strength: Strength
    base_strength: Strength | None
    source_call: int | None = Field(description="The call whose evidence assessment this is; None when set by the orchestrator's rule.")
    reason: str
    reasons: list[Traced]
    weakest_event: Traced | None


class ExperimentArmPlan(_Frozen):
    name: Traced
    role: Traced
    n_sessions: Traced
    allocation_share: Traced


class ExperimentRecommendation(_Frozen):
    status: Traced
    purpose: Traced | None
    hypothesis: Traced | None
    intervention: Traced | None
    randomization_unit: Traced | None
    primary_metric: Traced | None
    guardrails: list[Traced]
    mde_pp: Traced | None
    arms: list[ExperimentArmPlan]
    n_per_arm_control: Traced | None
    duration_days: Traced | None
    achieved_power: Traced | None
    needs: list[Traced]
    warnings: list[Traced]
    summary: Traced


class InvestigationResult(_Frozen):
    question: str
    request: InvestigationRequest
    status: Status
    stopped_reason: str | None
    stopped_call: int | None = Field(description="The log entry of the failed call, when a tool error stopped the run.")
    dataset: DatasetFacts | None
    data_quality: DataQualityNotes | None
    confirmed_change: ConfirmedChange | None
    change_point: ChangePointFinding | None
    localization: LocalizationFinding | None
    candidates: list[CandidateFinding]
    alternatives_considered: list[Traced]
    causal_estimates: list[CausalEstimate]
    causal_estimate_note: str | None
    unexplained: Unexplained | None
    evidence: EvidenceSummary
    recommended_experiment: ExperimentRecommendation | None
    experiment_note: str | None
    assumptions: list[Traced]
    statements: list[RenderedStatement]
    rejected_statements: list[str]
    stages: list[StageRecord]
    n_calls: int
