"""The investigation orchestrator (design, "Agents"): validate -> confirm -> change point -> localize -> test -> estimate -> recommend.

The orchestrator runs the stages in a fixed order and hands each role a ``Toolbox`` limited to that role's tools. Control
flow reads only logged results: it stops when the change is not confirmed (Requirement 2.3), skips candidate testing when the
dataset has no event log, and makes a causal estimate only for a candidate the tests support. It calls no model and computes no
statistic; ``Reasoner`` writes the interpretive text, and everything it writes is validated before it enters the report.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from causelens.agents import wording
from causelens.agents.claims import ClaimEligibility, assess_claim
from causelens.agents.reasoner import Context, Reasoner, TemplateReasoner
from causelens.agents.report import EstimateCalls, Facts, Stop, build_report
from causelens.agents.result import InvestigationResult
from causelens.agents.roles import DETECTIVE, EXPERIMENT_DESIGNER, SCIENTIST, Role
from causelens.agents.state import (
    InvestigationRequest,
    InvestigationState,
    LoggedCall,
    Prior,
    Stage,
)
from causelens.agents.toolbox import Toolbox, ToolCallError, ToolClient
from causelens.agents.trace import (
    RenderedStatement,
    TraceError,
    render,
    validate_statement,
    verify_traces,
)
from causelens.analytics import CandidateTestingResult, DidResult


@dataclass(frozen=True)
class Investigation:
    """The finished run: the log and the result built from it."""

    state: InvestigationState
    result: InvestigationResult


class _Run:
    def __init__(self, request: InvestigationRequest, client: ToolClient):
        self.state = InvestigationState(request=request)
        self.box = Toolbox(client, self.state)
        self.facts = Facts()
        self.claims: list[ClaimEligibility] = []
        self.stage: Stage = "validate"
        self.role: Role = DETECTIVE

    async def call(self, role: Role, stage: Stage, tool: str, **arguments: Any) -> LoggedCall:
        self.stage, self.role = stage, role
        return await self.box.call(role, stage, tool, **arguments)

    @property
    def windows(self) -> dict[str, Any]:
        r = self.state.request
        return {"pre_window": r.pre_window.as_arguments(), "post_window": r.post_window.as_arguments()}

    # ------------------------------------------------------------------ Detective

    async def detective(self) -> Stop | None:
        s, f, req = self.state, self.facts, self.state.request
        ds = req.dataset_id

        catalog = await self.call(DETECTIVE, "validate", "list_datasets")
        f.catalog = catalog.seq
        entry = next((d for d in (catalog.result or {})["datasets"] if d["id"] == ds), None)
        if entry is None:
            s.stage("validate", DETECTIVE.name, "stopped", "the dataset is not configured on the server")
            return Stop("stopped_insufficient_data", wording.STRENGTH_STOPPED_DATA, catalog.seq)
        f.events_available = entry.get("events_file") is not None
        quality = await self.call(DETECTIVE, "validate", "check_data_quality", dataset_id=ds)
        f.quality = quality.seq
        s.stage("validate", DETECTIVE.name, "done")

        confirm = await self.call(DETECTIVE, "confirm", "confirm_change", dataset_id=ds, quality=Prior(quality.seq), **self.windows)
        f.confirm = confirm.seq
        confirmed = s.model(confirm)
        if confirmed.status != "ok":
            s.stage("confirm", DETECTIVE.name, "stopped", "insufficient data to confirm the change")
            return Stop("stopped_insufficient_data", wording.STRENGTH_STOPPED_DATA, confirm.seq)
        if not confirmed.is_real or confirmed.stop_investigation:
            s.stage("confirm", DETECTIVE.name, "stopped", "the change is not distinguishable from noise")
            return Stop("stopped_change_not_confirmed", wording.STRENGTH_STOPPED_NOT_CONFIRMED, confirm.seq)
        s.stage("confirm", DETECTIVE.name, "done")

        point = await self.call(DETECTIVE, "change_point", "detect_change_point", dataset_id=ds, quality=Prior(quality.seq))
        f.change_point = point.seq
        if f.events_available and s.model(point).change_detected:
            match = await self.call(DETECTIVE, "change_point", "match_event_candidates", dataset_id=ds, change_point=Prior(point.seq))
            f.match = match.seq
        s.stage("change_point", DETECTIVE.name, "done")

        decomposition = await self.call(DETECTIVE, "localize", "decompose_change", dataset_id=ds, quality=Prior(quality.seq), **self.windows)
        f.decomposition = decomposition.seq
        if s.model(decomposition).status != "ok":
            s.stage("localize", DETECTIVE.name, "stopped", "insufficient data to decompose the change")
            return Stop("stopped_insufficient_data", wording.STRENGTH_STOPPED_DATA, decomposition.seq)
        s.stage("localize", DETECTIVE.name, "done")
        return None

    # ------------------------------------------------------------------ Scientist

    async def scientist(self) -> None:
        s, f, req = self.state, self.facts, self.state.request
        ds = req.dataset_id
        if not f.events_available:
            s.stage("test", SCIENTIST.name, "skipped", "the dataset has no event log")
            s.stage("estimate", SCIENTIST.name, "skipped", "no candidate was tested")
            return
        assert f.quality is not None and f.decomposition is not None  # the Detective stage completed
        patterns = None if req.expected_patterns is None else {k: v.model_dump(mode="json", exclude_none=True) for k, v in req.expected_patterns.items()}
        testing = await self.call(
            SCIENTIST, "test", "test_candidates", dataset_id=ds, decomposition=Prior(f.decomposition), quality=Prior(f.quality),
            candidates=req.candidates, expected_patterns=patterns, lag_days=req.lag_days, **self.windows,
        )
        f.testing = testing.seq
        tested: CandidateTestingResult = s.model(testing)
        s.stage("test", SCIENTIST.name, "done")

        supported = [(i, c) for i, c in enumerate(tested.candidates) if c.verdict == "supported"]
        assert tested.focal is not None or not supported
        for index, cand in supported:
            focal = tested.focal.segments  # type: ignore[union-attr]
            common: dict[str, Any] = {
                "dataset_id": ds, "focal": focal, "event_date": cand.onset_date.isoformat(), "quality": Prior(f.quality),
            }
            did = await self.call(SCIENTIST, "estimate", "estimate_did", **common)
            pre = await self.call(SCIENTIST, "estimate", "check_pre_trends", **common)
            placebo = await self.call(SCIENTIST, "estimate", "run_placebo", **common)
            impact = await self.call(
                SCIENTIST, "estimate", "project_impact", dataset_id=ds, did=Prior(did.seq), focal=focal, quality=Prior(f.quality),
            )
            standalone: DidResult = s.model(did)
            same = cand.differential_effect == standalone
            if not same:
                s.notes.append(f"The standalone difference-in-differences (call {did.seq}) differs from the one embedded in call {testing.seq} for candidate {index}.")
            f.estimates.append(EstimateCalls(index=index, did=did.seq, pre_trend=pre.seq, placebo=placebo.seq, impact=impact.seq, matches_embedded=same))
            self.claims.append(assess_claim(
                s, event=cand.event, verdict_call=testing.seq, verdict_path=f"/candidates/{index}/verdict",
                did_call=did.seq, pre_trend_call=pre.seq, placebo_call=placebo.seq,
            ))
        s.stage("estimate", SCIENTIST.name, "done" if supported else "skipped", None if supported else "no candidate was supported")

    # ------------------------------------------------------------------ Experiment designer

    async def designer(self) -> None:
        s, f, req = self.state, self.facts, self.state.request
        tested: CandidateTestingResult | None = s.model(f.testing) if f.testing else None
        if tested is None or tested.status != "ok":
            s.stage("recommend", EXPERIMENT_DESIGNER.name, "skipped", "candidate testing did not produce a focal segment to design for")
            return
        assert f.testing is not None and f.quality is not None
        # With no single leading candidate the tool needs either a named target or one arm per open candidate. Naming a
        # target would pick a winner the tests did not, so every open candidate gets an arm.
        each = tested.leading is None
        design = await self.call(
            EXPERIMENT_DESIGNER, "recommend", "design_experiment", dataset_id=req.dataset_id, candidate_testing=Prior(f.testing),
            post_window=req.post_window.as_arguments(), quality=Prior(f.quality), arms="each_candidate" if each else None,
        )
        f.design = design.seq
        s.stage("recommend", EXPERIMENT_DESIGNER.name, "done", "no single leading candidate: one arm per open candidate" if each else None)


def _statements(run: _Run, reasoner: Reasoner, roles: list[Role]) -> tuple[list[RenderedStatement], list[str]]:
    """Ask the reasoner for each role's text and keep only what passes validation."""
    state = run.state
    allowed = frozenset(c.did_call for c in run.claims if c.allowed)
    context = Context(state=state, claims=tuple(run.claims))
    kept: list[RenderedStatement] = []
    rejected: list[str] = []
    for role in roles:
        for statement in reasoner.interpret(role, context):
            try:
                validate_statement(statement, state, allowed_claim_calls=allowed)
                kept.append(RenderedStatement(role=statement.role, kind=statement.kind, text=render(statement, state), statement=statement))
            except TraceError as exc:
                rejected.append(f"{statement.role} {statement.kind}: {exc}")
    return kept, rejected


async def investigate(request: InvestigationRequest, client: ToolClient, reasoner: Reasoner | None = None) -> Investigation:
    """Run one investigation through ``client`` (an MCP session) and return the log and the structured result."""
    reasoner = reasoner or TemplateReasoner()
    run = _Run(request, client)
    stop: Stop | None = None
    try:
        stop = await run.detective()
        if stop is None:
            await run.scientist()
            await run.designer()
    except ToolCallError as exc:
        run.state.stage(run.stage, run.role.name, "stopped", "a tool call failed")
        stop = Stop("stopped_tool_error", wording.STRENGTH_TOOL_ERROR, exc.call.seq)
    roles = [DETECTIVE] + ([SCIENTIST, EXPERIMENT_DESIGNER] if stop is None else [])
    statements, rejected = _statements(run, reasoner, roles)
    result = build_report(run.state, run.facts, stop, run.claims, statements, rejected)
    verify_traces(result, run.state)
    return Investigation(state=run.state, result=result)


def investigate_sync(request: InvestigationRequest, target: Any, reasoner: Reasoner | None = None) -> Investigation:
    """``investigate`` for a script: opens the MCP session (URL, ``StdioServerParameters`` or in-process server) and closes it."""
    import asyncio

    from causelens.agents.toolbox import McpToolClient

    async def go() -> Investigation:
        async with McpToolClient(target) as client:
            return await investigate(request, client, reasoner)

    return asyncio.run(go())
