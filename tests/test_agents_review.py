"""Regressions for the PR #9 review of ``f62a8b4`` (Task 8). Each test is named for the finding it reproduces."""
from __future__ import annotations

import asyncio

import pytest
from agents_support import POST, PRE, request, run, server_over
from mcp_reference import reference_files
from mcp_support import RunningServer
from pydantic import ValidationError

from causelens.agents import (
    DETECTIVE,
    SCIENTIST,
    Context,
    Investigation,
    InvestigationRequest,
    InvestigationState,
    LoggedCall,
    McpToolClient,
    Ref,
    Statement,
    TemplateReasoner,
    Toolbox,
    ToolCallError,
    Window,
    investigate,
    verify_traces,
)
from causelens.agents.toolbox import RawOutcome
from causelens.analytics import ExpectedPattern
from causelens.mcp import DatasetConfig, DatasetRegistry

CHECKOUT, HERO = "Checkout v4.2", "Homepage hero refresh"


@pytest.fixture(scope="module")
def files(tmp_path_factory):
    return reference_files(tmp_path_factory)


@pytest.fixture(scope="module")
def reference_run(files) -> Investigation:
    return run(*files)


class Scripted(TemplateReasoner):
    """A reasoner whose interpretation is whatever it is given; everything else is the template reasoner's."""

    def __init__(self, statements: list[Statement]):
        self.statements = statements

    def interpret(self, role, context: Context) -> list[Statement]:
        return [s for s in self.statements if s.role == role.name]


def investigate_with(files, statements: list[Statement]) -> Investigation:
    async def go():
        async with McpToolClient(server_over(*files)) as client:
            return await investigate(request(), client, Scripted(statements))

    return asyncio.run(go())


# --------------------------------------------------------------------------- 1. a rejected event cannot receive a causal claim

def claim_refs(run_: Investigation, event_index: int) -> dict[str, Ref]:
    """The refs of a well-formed claim for the DiD of the supported candidate, but naming the candidate at ``event_index``."""
    state = run_.state
    testing, did = state.last("test_candidates"), state.last("estimate_did")
    n_assumptions = len(did.result["assumptions"])
    refs = {
        "event": Ref(call=testing.seq, path=f"/candidates/{event_index}/event"),
        "effect": Ref(call=did.seq, path="/effect_pp/estimate", fmt="pp"), "lower": Ref(call=did.seq, path="/effect_pp/lower", fmt="pp"),
        "upper": Ref(call=did.seq, path="/effect_pp/upper", fmt="pp"), "level": Ref(call=did.seq, path="/effect_pp/level", fmt="level"),
        "n": Ref(call=did.seq, path="/n_obs", fmt="count"), "lag": Ref(call=testing.seq, path=f"/candidates/{event_index}/lag_days", fmt="count"),
        "onset": Ref(call=testing.seq, path=f"/candidates/{event_index}/onset_date", fmt="date"),
        "focal": Ref(call=testing.seq, path="/focal/label"),
    }
    refs.update({f"a{i}": Ref(call=did.seq, path=f"/assumptions/{i}") for i in range(n_assumptions)})
    return refs


def claim_text(refs: dict[str, Ref]) -> str:
    assumptions = " ".join("{" + k + "}" for k in refs if k.startswith("a"))
    return ("By difference-in-differences, the change in {focal} attributed to {event} (onset {onset}, lag {lag}) is about {effect} "
            "(interval {lower} to {upper} at level {level}, {n} observations), if these assumptions hold: " + assumptions)


def test_finding1_a_rejected_event_cannot_borrow_the_supported_events_estimate(files, reference_run):
    index = {c.event.value: i for i, c in enumerate(reference_run.result.candidates)}
    refs = claim_refs(reference_run, index[HERO])
    forged = Statement(role="Scientist", kind="causal_claim", text=claim_text(refs), refs=refs)
    inv = investigate_with(files, [forged])
    assert [s for s in inv.result.statements if s.kind == "causal_claim"] == []
    assert any("not the event" in m or "bound to" in m for m in inv.result.rejected_statements), inv.result.rejected_statements


def test_finding1_the_event_cannot_be_typed_into_the_sentence_either(files, reference_run):
    index = {c.event.value: i for i, c in enumerate(reference_run.result.candidates)}
    refs = claim_refs(reference_run, index[CHECKOUT])
    del refs["event"]
    text = claim_text({**refs, "event": Ref(call=1, path="/datasets/0/id")}).replace("{event}", f"{HERO}")
    forged = Statement(role="Scientist", kind="causal_claim", text=text, refs=refs)
    inv = investigate_with(files, [forged])
    assert [s for s in inv.result.statements if s.kind == "causal_claim"] == []
    assert inv.result.rejected_statements


@pytest.mark.parametrize("drop", ["effect", "lower", "upper", "level", "n", "lag", "onset", "event", "a0"])
def test_finding1_a_claim_must_carry_its_whole_structure(files, reference_run, drop):
    index = {c.event.value: i for i, c in enumerate(reference_run.result.candidates)}
    refs = claim_refs(reference_run, index[CHECKOUT])
    text = claim_text(refs).replace("{" + drop + "}", "something")
    refs.pop(drop)
    inv = investigate_with(files, [Statement(role="Scientist", kind="causal_claim", text=text, refs=refs)])
    assert [s for s in inv.result.statements if s.kind == "causal_claim"] == []
    assert inv.result.rejected_statements


def test_finding1_the_genuine_claim_for_the_supported_event_is_still_accepted(files, reference_run):
    index = {c.event.value: i for i, c in enumerate(reference_run.result.candidates)}
    refs = claim_refs(reference_run, index[CHECKOUT])
    good = Statement(role="Scientist", kind="causal_claim", text=claim_text(refs), refs=refs)
    inv = investigate_with(files, [good])
    claims = [s for s in inv.result.statements if s.kind == "causal_claim"]
    assert len(claims) == 1 and CHECKOUT in claims[0].text and inv.result.rejected_statements == []


# --------------------------------------------------------------------------- 2. the LLM layer (hooks exist)

def test_finding2_the_reasoner_has_a_parameter_hook_and_a_model_backed_implementation():
    from causelens import agents

    assert hasattr(TemplateReasoner, "choose_parameters") and hasattr(agents, "ModelReasoner")


# --------------------------------------------------------------------------- 3. a combined statistic is not credited to one segment

def two_segment_state() -> InvestigationState:
    state = InvestigationState(request=request())
    localization = {
        "partition": "device × browser", "segments": ["device=mobile, browser=safari", "device=mobile, browser=chrome"],
        "selected_share_of_change": 1.0006, "traffic_share_post": 0.2, "all_selected_significant": True, "statement": "x",
    }
    state.log.append(LoggedCall(seq=1, role="Detective", stage="localize", tool="decompose_change", arguments={},
                                result={"status": "ok", "localization": localization, "partitions": []}))
    return state


def test_finding3_the_localization_sentence_names_every_selected_segment():
    state = two_segment_state()
    statements = TemplateReasoner().interpret(DETECTIVE, Context(state, ()))
    sentence = next(s for s in statements if "concentrated" in s.text)
    from causelens.agents.trace import render

    text = render(sentence, state)
    assert "device=mobile, browser=safari" in text and "device=mobile, browser=chrome" in text
    assert "together" in text


# --------------------------------------------------------------------------- 4 and 5. the rejection ground and the confidence set

def test_finding4_a_pattern_rejection_is_not_described_as_a_sole_source_rejection(files):
    inv = run(*files, expected_patterns={CHECKOUT: ExpectedPattern(kind="uniform", scale="relative")})
    checkout = next(c for c in inv.result.candidates if c.event.value == CHECKOUT)
    assert checkout.verdict.value == "rejected"
    assert "declared" in checkout.why and "sole" not in checkout.why
    assert [g.value for g in checkout.rejection_grounds] == ["contradicted"]
    assert checkout.sole_source_rejected is not None and checkout.sole_source_rejected.value is False
    verify_traces(inv.result, inv.state)


def test_finding4_the_reference_rejection_of_hero_is_a_sole_source_rejection(reference_run):
    hero = next(c for c in reference_run.result.candidates if c.event.value == HERO)
    assert "sole" in hero.why and hero.sole_source_rejected.value is True


def test_finding5_a_rejected_candidate_carries_its_share_confidence_set(reference_run):
    hero = next(c for c in reference_run.result.candidates if c.event.value == HERO)
    cs = hero.share_confidence_set
    assert cs is not None and cs.available.value is True and cs.level.value == 0.95
    tool = reference_run.state.last("test_candidates").result["candidates"]
    raw = next(c for c in tool if c["event"] == HERO)["timing"]["share_set"]
    assert [(p.lower and p.lower.value, p.upper and p.upper.value) for p in cs.pieces] == [tuple(piece) for piece in raw["pieces"]]
    assert -0.5 < cs.pieces[0].lower.value < -0.3 and 0.6 < cs.pieces[0].upper.value < 0.8


def test_finding5_an_unavailable_set_states_its_reason(files):
    inv = run(*files, lag_days={HERO: 1})  # Hero at lag 1 shares Checkout's onset: not identifiable
    hero = next(c for c in inv.result.candidates if c.event.value == HERO)
    assert hero.share_confidence_set.available.value is False and hero.share_confidence_set.reason.value
    assert hero.share_confidence_set.pieces == []


# --------------------------------------------------------------------------- 6. transport failures are logged and stop the run

def test_finding6_a_closed_server_gives_a_logged_failure_and_a_stopped_result(files):
    registry = DatasetRegistry([DatasetConfig("reference", files[0], files[1])])
    server = RunningServer(registry)

    async def go():
        async with McpToolClient(server.url) as client:
            server.stop()  # the connection dies after the session is open
            return await investigate(request(), client)

    inv = asyncio.run(go())
    assert inv.result.status == "stopped_tool_error" and inv.result.stopped_call == 1
    failed = inv.state.call(1)
    assert failed.tool == "list_datasets" and failed.is_error and failed.result is None and failed.error
    assert inv.result.evidence.strength == "insufficient"


def test_finding6_an_ordinary_exception_from_the_client_is_logged_and_raised_as_a_call_error():
    class Broken:
        async def call_tool(self, name, arguments):
            raise ConnectionResetError("peer reset")

    state = InvestigationState(request=request())
    with pytest.raises(ToolCallError):
        asyncio.run(Toolbox(Broken(), state).call(DETECTIVE, "validate", "list_datasets"))
    assert state.log[0].is_error and "ConnectionResetError" in state.log[0].error and "peer reset" in state.log[0].error


def test_finding6_cancellation_is_not_swallowed():
    class Cancelled:
        async def call_tool(self, name, arguments):
            raise asyncio.CancelledError

    state = InvestigationState(request=request())
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(Toolbox(Cancelled(), state).call(DETECTIVE, "validate", "list_datasets"))


# --------------------------------------------------------------------------- 7. malformed reasoner text is rejected, not fatal

@pytest.mark.parametrize("text", ["The effect is {effect", "The effect is effect}", "The effect is {}", "The effect is {0}",
                                  "The effect is {effect.real}", "The effect is {effect[0]}", "The effect is {{effect}"])
def test_finding7_malformed_templates_are_rejected_and_recorded(files, text):
    bad = Statement(role="Detective", kind="finding", text=text, refs={"effect": Ref(call=1, path="/datasets/0/n_rows", fmt="count")})
    inv = investigate_with(files, [bad])
    assert inv.result.status == "completed" and inv.result.statements == []
    assert len(inv.result.rejected_statements) == 1


# --------------------------------------------------------------------------- 8. logged payloads are snapshots

def recorder_box(result):
    class Recorder:
        async def call_tool(self, name, arguments):
            self.sent = arguments
            return RawOutcome(result, False, None)

    client = Recorder()
    return Toolbox(client, InvestigationState(request=request())), client


def test_finding8_changing_the_callers_inputs_after_a_call_does_not_rewrite_the_log():
    toolbox, _ = recorder_box({"status": "ok", "nested": {"a": [1, 2]}})
    focal = [{"device": "mobile", "browser": "safari"}]
    asyncio.run(toolbox.call(SCIENTIST, "estimate", "estimate_did", dataset_id="reference", focal=focal, event_date="2026-10-14"))
    focal[0]["device"] = "desktop"
    focal.append({"device": "tablet"})
    assert toolbox.state.log[0].arguments["focal"] == [{"device": "mobile", "browser": "safari"}]


def test_finding8_the_servers_result_is_a_snapshot_too():
    payload = {"status": "ok", "nested": {"a": [1, 2]}}
    toolbox, _ = recorder_box(payload)
    asyncio.run(toolbox.call(SCIENTIST, "estimate", "estimate_did", dataset_id="reference"))
    payload["nested"]["a"].append(3)
    payload["status"] = "changed"
    assert toolbox.state.log[0].result == {"status": "ok", "nested": {"a": [1, 2]}}


def test_finding8_a_stored_entry_cannot_be_edited_in_place():
    toolbox, _ = recorder_box({"status": "ok", "nested": {"a": [1, 2]}})
    asyncio.run(toolbox.call(SCIENTIST, "estimate", "estimate_did", dataset_id="reference", focal=[{"device": "mobile"}]))
    entry = toolbox.state.log[0]
    for edit in (lambda: entry.result.__setitem__("status", "x"), lambda: entry.result["nested"]["a"].append(3),
                 lambda: entry.arguments["focal"][0].__setitem__("device", "x"), lambda: entry.arguments.pop("dataset_id"),
                 lambda: setattr(entry, "tool", "other")):
        with pytest.raises((TypeError, AttributeError, ValidationError)):
            edit()
    assert entry.result == {"status": "ok", "nested": {"a": [1, 2]}} and entry.tool == "estimate_did"


def test_finding8_a_prior_sent_to_the_server_is_not_the_stored_object():
    toolbox, client = recorder_box({"excluded_dates": []})
    from causelens.agents import Prior

    asyncio.run(toolbox.call(DETECTIVE, "validate", "check_data_quality", dataset_id="reference"))
    asyncio.run(toolbox.call(DETECTIVE, "confirm", "confirm_change", dataset_id="reference", quality=Prior(1)))
    client.sent["quality"]["excluded_dates"].append("2026-01-01")  # a client that edits what it was given
    assert toolbox.state.log[0].result == {"excluded_dates": []}


def test_finding8_the_log_still_serializes_and_round_trips(reference_run):
    again = InvestigationState.model_validate_json(reference_run.state.model_dump_json())
    assert again.model_dump_json() == reference_run.state.model_dump_json()
    verify_traces(reference_run.result, again)


# --------------------------------------------------------------------------- 9. strict request parsing

@pytest.mark.parametrize("bad", [0, 86400, "86400", "0", "2026-10-14T00:00:00", "10/14/2026", 20261014, True, None])
def test_finding9_windows_accept_only_iso_dates(bad):
    with pytest.raises(ValidationError):
        Window(start=bad, end="2026-10-31")
    with pytest.raises(ValidationError):
        Window(start="2026-10-14", end=bad)


@pytest.mark.parametrize("bad", [True, False, "1", 1.0, 0.5, None])
def test_finding9_lag_days_must_be_integers(bad):
    with pytest.raises(ValidationError):
        InvestigationRequest(question="q", dataset_id="d", pre_window=PRE, post_window=POST, lag_days={HERO: bad})


@pytest.mark.parametrize("field,value", [("question", 5), ("dataset_id", 5), ("candidates", [1]), ("candidates", "Checkout"),
                                         ("expected_patterns", {HERO: {"kind": "uniform", "scale": 1}}),
                                         ("expected_patterns", {HERO: {"kind": "restricted", "scope": None}})])
def test_finding9_other_request_fields_are_not_coerced(field, value):
    kwargs = {"question": "q", "dataset_id": "d", "pre_window": PRE, "post_window": POST, field: value}
    with pytest.raises(ValidationError):
        InvestigationRequest(**kwargs)


def test_finding9_valid_requests_still_parse_from_python_and_json():
    r = InvestigationRequest(question="q", dataset_id="d", pre_window={"start": "2026-08-01", "end": "2026-10-13"}, post_window=POST,
                             lag_days={HERO: 1}, expected_patterns={HERO: {"kind": "uniform", "scale": "relative"}})
    assert r.pre_window.start.isoformat() == "2026-08-01" and r.lag_days == {HERO: 1}
    assert InvestigationRequest.model_validate_json(r.model_dump_json()) == r


# --------------------------------------------------------------------------- 10. the history of a late failure is true

class RefuseDesign:
    """Delegates to the real client but refuses design_experiment, as a server error would."""

    def __init__(self, inner):
        self.inner = inner

    async def call_tool(self, name, arguments):
        if name == "design_experiment":
            return RawOutcome(None, True, "the design service is unavailable")
        return await self.inner.call_tool(name, arguments)


def late_failure(files) -> Investigation:
    async def go():
        async with McpToolClient(server_over(*files)) as client:
            return await investigate(request(), RefuseDesign(client))

    return asyncio.run(go())


def test_finding10_a_failure_after_testing_does_not_say_testing_never_ran(files):
    inv = late_failure(files)
    r = inv.result
    assert r.status == "stopped_tool_error" and inv.state.call(r.stopped_call).tool == "design_experiment"
    assert len(r.causal_estimates) == 1 and r.causal_estimate_note is None
    assert r.experiment_note and "failed" in r.experiment_note and "testing" not in r.experiment_note
    assert r.stopped_stage == "recommend"
    assert [s.outcome for s in r.stages][-1] == "stopped"


def test_finding10_the_evidence_of_completed_testing_is_kept_when_only_the_design_failed(files):
    r = late_failure(files).result
    assert r.evidence.strength == "moderate" and r.evidence.reasons and "design" in r.evidence.reason


def test_finding10_an_early_stop_still_says_testing_did_not_run(files, tmp_path):
    class RefuseConfirm:
        def __init__(self, inner):
            self.inner = inner

        async def call_tool(self, name, arguments):
            if name == "confirm_change":
                return RawOutcome(None, True, "boom")
            return await self.inner.call_tool(name, arguments)

    async def go():
        async with McpToolClient(server_over(*files)) as client:
            return await investigate(request(), RefuseConfirm(client))

    r = asyncio.run(go()).result
    assert r.causal_estimate_note and "stopped before candidate testing" in r.causal_estimate_note
    assert r.evidence.strength == "insufficient" and r.stopped_stage == "confirm"


def test_finding10_a_failure_during_estimation_does_not_report_a_complete_estimate(files):
    class RefusePlacebo:
        def __init__(self, inner):
            self.inner = inner

        async def call_tool(self, name, arguments):
            if name == "run_placebo":
                return RawOutcome(None, True, "boom")
            return await self.inner.call_tool(name, arguments)

    async def go():
        async with McpToolClient(server_over(*files)) as client:
            return await investigate(request(), RefusePlacebo(client))

    r = asyncio.run(go()).result
    assert r.stopped_stage == "estimate" and r.causal_estimates == []
    assert r.causal_estimate_note and "did not complete" in r.causal_estimate_note
    assert r.evidence.strength == "insufficient"



# --------------------------------------------------------------------------- PR #9 re-review of ffc060d

def localization_sentence(segments: list[str]) -> str:
    from causelens.agents.trace import render

    state = two_segment_state()
    result = dict(state.log[0].result)
    result["localization"] = {**result["localization"], "segments": segments}
    state.log[0] = state.log[0].model_copy(update={"result": result})
    sentence = next(s for s in TemplateReasoner().interpret(DETECTIVE, Context(state, ())) if "concentrated" in s.text)
    return render(sentence, state)


def test_rereview1_the_segment_list_holds_segments_only_for_two_segments():
    text = localization_sentence(["device=a", "device=b"])
    assert "concentrated in the set of segments device=a, device=b, which together account for 100.06% " in text
    assert text.count("100.06%") == 1 and text.count("20.00%") == 1


def test_rereview1_the_segment_list_holds_segments_only_for_one_segment():
    text = localization_sentence(["device=a"])
    assert "concentrated in device=a, which accounts for 100.06% of the overall change while carrying 20.00% " in text
    assert "together" not in text


def test_rereview1_three_segments_are_all_listed_and_nothing_else():
    text = localization_sentence(["device=a", "device=b", "device=c"])
    assert "device=a, device=b, device=c, which together" in text


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_rereview2_non_finite_parameters_are_refused(bad):
    from causelens.agents import ParameterChoice, ParameterError
    from causelens.agents.parameters import validate_choice

    with pytest.raises(ParameterError, match="finite"):
        validate_choice("decompose_change", ParameterChoice(arguments={"explain_threshold": bad}, rationale="Tighter."))


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_rereview2_a_non_finite_choice_falls_back_to_defaults_and_the_run_completes(files, bad):
    from causelens.agents import ParameterChoice

    class Chooser(TemplateReasoner):
        def choose_parameters(self, role, tool, context):
            return ParameterChoice(arguments={"explain_threshold": bad}, rationale="Tighter.") if tool == "decompose_change" else None

    async def go():
        async with McpToolClient(server_over(*files)) as client:
            return await investigate(request(), client, Chooser())

    inv = asyncio.run(go())
    assert inv.result.status == "completed"
    assert "explain_threshold" not in inv.state.last("decompose_change").arguments
    (note,) = inv.result.parameter_choices
    assert not note.accepted and note.call is None and "finite" in note.problem


def test_rereview2_a_model_that_replies_with_nan_is_refused_too(files):
    import json

    from causelens.agents import ModelReasoner

    class Model:
        def complete(self, system, user):
            payload = json.loads(user[: user.rindex("\n\n")])
            if payload["task"] == "choose_parameters" and payload["tool"] == "decompose_change":
                return '{"arguments": {"explain_threshold": NaN}, "rationale": "Tighter."}'
            if payload["task"] == "choose_parameters":
                return '{"arguments": {}, "rationale": "default"}'
            return '{"statements": []}'

    async def go():
        async with McpToolClient(server_over(*files)) as client:
            return await investigate(request(), client, ModelReasoner(Model()))

    inv = asyncio.run(go())
    assert inv.result.status == "completed" and [n.accepted for n in inv.result.parameter_choices] == [False]
