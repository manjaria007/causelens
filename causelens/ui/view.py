"""The console's view of an investigation: six steps, each with its evidence (Requirement 8.3).

Everything a step shows is read from the orchestrator's structured result or, for the evidence panels, from the logged MCP results
through a ``Tracer``, so each number carries the call and pointer it came from. ``build_view`` ends by re-reading every traced value
from the log (``verify_traces``). Nothing here computes a statistic; the only arithmetic is picking which logged values go where.

Six steps (design: detect, locate, localize, test, estimate, recommend) map onto the orchestrator's seven stages:
``detect`` is validate plus confirm, because a tracking break has to be ruled out before a change is believed.

No MCP tool returns a daily series, so the "series" panel shows the level of conversion in the two windows (from ``confirm_change``)
against the change point, the candidate events and the excluded days. A daily series would need a new tool.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from causelens.agents import Investigation, InvestigationResult
from causelens.agents.result import (
    CandidateFinding,
    CausalEstimate,
    ExperimentRecommendation,
    Unexplained,
)
from causelens.agents.trace import (
    Traced,
    TracedBand,
    TracedInterval,
    Tracer,
    verify_traces,
)

STEP_KEYS = ("detect", "locate", "localize", "test", "estimate", "recommend")
STEP_STAGES: dict[str, tuple[str, ...]] = {
    "detect": ("validate", "confirm"), "locate": ("change_point",), "localize": ("localize",),
    "test": ("test",), "estimate": ("estimate",), "recommend": ("recommend",),
}
STEP_TITLES = {
    "detect": "Detect: is the change real?", "locate": "Locate: when did it start?", "localize": "Localize: where is it concentrated?",
    "test": "Test: which explanations survive?", "estimate": "Estimate: how large, and how sure?", "recommend": "Recommend: what to test next",
}
STEP_INTRO = {
    "detect": "Data quality is checked first: a tracking break is a valid explanation. Then the change is compared with ordinary "
              "day-to-day variation.",
    "locate": "The most plausible onset date, with the set of dates the data cannot rule out, and the logged events whose dates coincide.",
    "localize": "Which segments carry the change. This describes where the change is, not why.",
    "test": "Each logged event is tested against a named hypothesis. A rejection is of that hypothesis only, and an event that is not "
            "supported is not thereby cleared.",
    "estimate": "A differential effect with its interval and sample size, the checks that go with it, and the assumptions it rests on.",
    "recommend": "The experiment that would settle what the data cannot, sized by the power tools.",
}
MAX_WATERFALL_ROWS = 12

StepStatus = Literal["done", "skipped", "stopped", "not_run"]


class _V(BaseModel):
    model_config = ConfigDict(frozen=True)


class Fact(_V):
    """A labelled traced value (or interval) shown in a step."""

    label: str
    item: Traced | TracedInterval | None
    note: str | None = None


class WindowBand(_V):
    label: str
    start: Traced
    end: Traced
    level: TracedInterval | None


class EventMark(_V):
    name: Traced
    event_date: Traced
    onset_date: Traced
    verdict: Traced


class TimelinePanel(_V):
    first: Traced | None
    last: Traced | None
    pre: WindowBand | None
    post: WindowBand | None
    best_date: Traced | None
    plausible: list[Traced]
    events: list[EventMark]
    excluded: list[Traced]
    caveat: str


class WaterfallRow(_V):
    label: Traced
    rank: Traced
    contribution: Traced
    rate_effect: Traced
    mix_effect: Traced
    q_value: Traced | None
    significant: Traced | None
    localized: bool


class WaterfallPanel(_V):
    partition: Traced
    rows: list[WaterfallRow]
    n_segments: Traced
    overall_change: Traced
    residual: Traced
    caveat: str


class PlaceboRow(_V):
    fake_date: Traced
    effect: TracedBand
    p_holm: Traced


class PlaceboPanel(_V):
    status: Traced
    criterion: Traced
    reason: Traced | None
    n_evaluated: Traced
    rows: list[PlaceboRow]
    real_effect: TracedInterval | None
    caveat: str


class EstimateBlock(_V):
    estimate: CausalEstimate
    placebo: PlaceboPanel | None


class StepView(_V):
    key: str
    number: int
    title: str
    intro: str
    status: StepStatus
    stage_notes: list[str] = Field(description="Why a stage stopped or was skipped, from the stage records.")
    calls: list[int]
    facts: list[Fact] = Field(default_factory=list)
    texts: list[Traced] = Field(default_factory=list, description="Tool-written text shown as written.")
    timeline: TimelinePanel | None = None
    waterfall: WaterfallPanel | None = None
    candidates: list[CandidateFinding] = Field(default_factory=list)
    alternatives: list[Traced] = Field(default_factory=list)
    estimates: list[EstimateBlock] = Field(default_factory=list)
    estimate_note: str | None = None
    unexplained: Unexplained | None = None
    experiment: ExperimentRecommendation | None = None
    experiment_note: str | None = None
    statements: list[str] = Field(default_factory=list, description="Reasoner text that passed validation against the log.")


class CallRow(_V):
    seq: int
    role: str
    stage: str
    tool: str
    failed: bool
    error: str | None
    prior_arguments: list[str] = Field(description="Arguments that were earlier results, as 'name <- call'.")
    argument_names: list[str]


class ConsoleView(_V):
    result: InvestigationResult
    steps: list[StepView]
    calls: list[CallRow]
    notes: list[str]


_DETECTIVE_TOOLS = {"check_data_quality": "detect", "confirm_change": "detect", "detect_change_point": "locate",
                    "match_event_candidates": "locate", "decompose_change": "localize"}


def _step_of(role: str, kind: str, tools: list[str]) -> str:
    """The step a validated statement belongs to: by the tool its first reference reads, for the Detective; by kind, for the others."""
    if role == "Detective":
        return next((_DETECTIVE_TOOLS[t] for t in tools if t in _DETECTIVE_TOOLS), "detect")
    if role == "Scientist":
        return "estimate" if kind in ("causal_claim", "caveat") else "test"
    return "recommend"


def _stage_status(result: InvestigationResult, key: str) -> tuple[StepStatus, list[str], list[int]]:
    records = [r for r in result.stages if r.stage in STEP_STAGES[key]]
    calls = [c for r in records for c in r.calls]
    notes = [f"{r.stage}: {r.reason}" for r in records if r.reason]
    if not records:
        return "not_run", notes, calls
    outcomes = {r.outcome for r in records}
    if "stopped" in outcomes:
        return "stopped", notes, calls
    if outcomes == {"skipped"}:
        return "skipped", notes, calls
    return "done", notes, calls


def _timeline(t: Tracer, result: InvestigationResult) -> TimelinePanel:
    confirm = result.confirmed_change
    bands: dict[str, WindowBand | None] = {"pre": None, "post": None}
    if confirm is not None and confirm.pre_conversion is not None and confirm.post_conversion is not None:
        c = confirm.status.call
        for key, interval in (("pre", confirm.pre_conversion), ("post", confirm.post_conversion)):
            bands[key] = WindowBand(
                label="before" if key == "pre" else "after", level=interval,
                start=t.value(c, f"/{key}/start", f"{key} window start", "date"), end=t.value(c, f"/{key}/end", f"{key} window end", "date"),
            )
    point = result.change_point
    return TimelinePanel(
        first=result.dataset.first_date if result.dataset else None, last=result.dataset.last_date if result.dataset else None,
        pre=bands["pre"], post=bands["post"], best_date=point.best_date if point else None,
        plausible=[point.plausible_dates[0], point.plausible_dates[-1]] if point and point.plausible_dates else [],
        events=[EventMark(name=c.event, event_date=c.event_date, onset_date=c.onset_date, verdict=c.verdict) for c in result.candidates],
        excluded=list(result.data_quality.excluded_dates) if result.data_quality else [],
        caveat="No MCP tool returns daily values, so this shows the level of conversion in each window (with its interval), not a daily series. "
               "Dates are those the tools returned.",
    )


def _waterfall(t: Tracer, result: InvestigationResult) -> WaterfallPanel | None:
    loc = result.localization
    if loc is None or not loc.localized or loc.partition is None:
        return None
    d = loc.status.call
    doc = t.state.call(d).result
    assert doc is not None
    index = next((i for i, p in enumerate(doc["partitions"]) if p["name"] == loc.partition.value), None)
    if index is None:
        return None
    part = doc["partitions"][index]
    selected = {s.value for s in loc.segments}
    order = sorted(range(len(part["segments"])), key=lambda i: part["segments"][i]["rank"])[:MAX_WATERFALL_ROWS]
    rows = []
    for i in order:
        b = f"/partitions/{index}/segments/{i}"
        seg = part["segments"][i]
        tested = seg.get("test")
        rows.append(WaterfallRow(
            label=t.value(d, f"{b}/label", "segment", "text"), rank=t.value(d, f"{b}/rank", "rank", "count"),
            contribution=t.value(d, f"{b}/contribution_pp", "contribution to the change", "pp"),
            rate_effect=t.value(d, f"{b}/rate_effect_pp", "rate effect", "pp"), mix_effect=t.value(d, f"{b}/mix_effect_pp", "mix effect", "pp"),
            q_value=t.maybe(d, f"{b}/test/q_value", "FDR-adjusted q", "p_value") if tested else None,
            significant=t.maybe(d, f"{b}/test/significant", "significant after FDR", "flag") if tested else None,
            localized=seg["label"] in selected,
        ))
    return WaterfallPanel(
        partition=loc.partition, rows=rows, n_segments=t.value(d, f"/partitions/{index}/n_segments", "segments in the partition", "count"),
        overall_change=t.value(d, "/change_pp", "overall change", "pp"), residual=t.value(d, f"/partitions/{index}/residual_pp", "residual", "pp"),
        caveat="Contributions are the tool's rate and mix effects for each segment of this one partition; they are not added across partitions. "
               "Segments are listed by rank.",
    )


def _placebo(t: Tracer, estimate: CausalEstimate) -> PlaceboPanel | None:
    call = estimate.claim.placebo_call
    doc = t.state.call(call).result
    if doc is None:
        return None
    rows = [
        PlaceboRow(
            fake_date=t.value(call, f"/placebos/{i}/fake_date", "placebo date", "date"),
            effect=t.band(call, f"/placebos/{i}/effect_pp", "placebo effect", "pp"),
            p_holm=t.value(call, f"/placebos/{i}/p_value_holm", "Holm-adjusted p", "p_value"),
        )
        for i in range(len(doc["placebos"]))
    ]
    return PlaceboPanel(
        status=estimate.placebo_status, criterion=t.value(call, "/criterion", "placebo criterion", "text"),
        reason=t.maybe(call, "/reason", "placebo reason", "text"), n_evaluated=estimate.placebo_n_evaluated, rows=rows,
        real_effect=estimate.effect_pp,
        caveat="A placebo is the same comparison at a fake date inside the pre-period. The tool does not report a sample size per placebo date.",
    )


def _facts_detect(result: InvestigationResult) -> tuple[list[Fact], list[Traced]]:
    facts: list[Fact] = []
    texts: list[Traced] = []
    dq = result.data_quality
    if dq is not None:
        facts.append(Fact(label="Days checked", item=dq.n_days_checked))
        texts.append(dq.exclusion_statement)
    c = result.confirmed_change
    if c is not None:
        facts += [
            Fact(label="Conversion before", item=c.pre_conversion), Fact(label="Conversion after", item=c.post_conversion),
            Fact(label="Absolute change", item=c.abs_change_pp), Fact(label="Relative change", item=c.rel_change),
            Fact(label="Verdict", item=c.verdict),
        ]
        if c.noise_p_value is not None:
            facts += [Fact(label="Effect size (pre-period standard deviations)", item=c.effect_size_pre_sd),
                      Fact(label="Noise-test p-value (read with the effect above)", item=c.noise_p_value)]
        facts += [Fact(label="Data needed", item=item) for item in c.data_needed]
        texts.append(c.summary)
    return [f for f in facts if f.item is not None], texts


def _facts_locate(result: InvestigationResult) -> tuple[list[Fact], list[Traced]]:
    p = result.change_point
    if p is None:
        return [], []
    facts = [Fact(label="Status", item=p.status), Fact(label="Most plausible onset", item=p.best_date), Fact(label="Direction", item=p.direction),
             Fact(label="Level of the plausible set", item=p.plausible_level)]
    facts += [Fact(label="Plausible onset date", item=d) for d in p.plausible_dates]
    facts += [Fact(label="Event near the onset (date coincidence only)", item=e) for e in p.matched_events]
    texts = [p.summary] + ([p.match_statement] if p.match_statement else [])
    return [f for f in facts if f.item is not None], texts


def _facts_localize(result: InvestigationResult) -> tuple[list[Fact], list[Traced]]:
    loc = result.localization
    if loc is None:
        return [], []
    facts = [Fact(label="Overall change", item=loc.overall_change_pp), Fact(label="Partition", item=loc.partition),
             Fact(label="Segments tested", item=loc.n_tests_scanned), Fact(label="FDR family", item=loc.fdr_family), Fact(label="alpha", item=loc.alpha)]
    facts += [Fact(label="Localized segment", item=s) for s in loc.segments]
    facts += [Fact(label="Share of the overall change (of the whole set)", item=loc.share_of_change),
              Fact(label="Share of post-window sessions (of the whole set)", item=loc.traffic_share_post),
              Fact(label="All localized segments significant after FDR", item=loc.all_segments_significant)]
    texts = [x for x in (loc.statement, loc.reason_if_not_localized) if x is not None]
    return [f for f in facts if f.item is not None], texts


def build_view(run: Investigation) -> ConsoleView:
    """The six steps of an investigation, with every number traced to the log and re-verified."""
    result, state = run.result, run.state
    t = Tracer(state)
    narrative: dict[str, list[str]] = {k: [] for k in STEP_KEYS}
    for s in result.statements:
        narrative[_step_of(s.role, s.kind, [state.call(r.call).tool for r in s.statement.refs.values()])].append(s.text)

    steps: list[StepView] = []
    for number, key in enumerate(STEP_KEYS, start=1):
        status, notes, calls = _stage_status(result, key)
        step = StepView(key=key, number=number, title=STEP_TITLES[key], intro=STEP_INTRO[key], status=status, stage_notes=notes, calls=calls,
                        statements=narrative[key])
        update: dict[str, object] = {}
        if key == "detect":
            update["facts"], update["texts"] = _facts_detect(result)
        elif key == "locate":
            update["facts"], update["texts"] = _facts_locate(result)
            if result.confirmed_change is not None:
                update["timeline"] = _timeline(t, result)
        elif key == "localize":
            update["facts"], update["texts"] = _facts_localize(result)
            update["waterfall"] = _waterfall(t, result)
            update["unexplained"] = result.unexplained
        elif key == "test":
            update["candidates"] = result.candidates
            update["alternatives"] = result.alternatives_considered
        elif key == "estimate":
            update["estimates"] = [EstimateBlock(estimate=e, placebo=_placebo(t, e)) for e in result.causal_estimates]
            update["estimate_note"] = result.causal_estimate_note
        elif key == "recommend":
            update["experiment"] = result.recommended_experiment
            update["experiment_note"] = result.experiment_note
        steps.append(step.model_copy(update=update))

    calls = [
        CallRow(
            seq=c.seq, role=c.role, stage=c.stage, tool=c.tool, failed=c.is_error, error=c.error,
            prior_arguments=[f"{k} <- call {v['$prior']}" for k, v in c.arguments.items() if isinstance(v, dict) and "$prior" in v],
            argument_names=sorted(k for k, v in c.arguments.items() if not (isinstance(v, dict) and "$prior" in v)),
        )
        for c in state.log
    ]
    notes = [*state.notes, *(f"Rejected reasoner output: {m}" for m in result.rejected_statements)]
    view = ConsoleView(result=result, steps=steps, calls=calls, notes=notes)
    verify_traces(view, state)
    return view

