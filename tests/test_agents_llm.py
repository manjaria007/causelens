"""The LLM layer (design, overview): parameter choice, interpretation after each role, and a model-backed reasoner.

The model here is a scripted object behind the ``TextModel`` protocol. It stands in for a provider; nothing hosted was run. What is
under test is everything around the model: the prompt it is given, how its output is read, and that none of it can put a number,
a cause or a protected parameter into the investigation.
"""
from __future__ import annotations

import asyncio
import json

import pytest
from agents_support import request, server_over
from mcp_reference import reference_files

from causelens.agents import (
    SCIENTIST,
    TUNABLE,
    Context,
    Investigation,
    McpToolClient,
    ModelOutputError,
    ModelReasoner,
    ParameterChoice,
    ParameterError,
    TemplateReasoner,
    investigate,
    verify_traces,
)
from causelens.agents.parameters import describe, validate_choice
from causelens.agents.reasoner import digest
from causelens.agents.roles import scientist_rules

CHECKOUT = "Checkout v4.2"


@pytest.fixture(scope="module")
def files(tmp_path_factory):
    return reference_files(tmp_path_factory)


@pytest.fixture(scope="module")
def default_run(files):
    return go(files, TemplateReasoner())


def go(files, reasoner) -> Investigation:
    async def main():
        async with McpToolClient(server_over(*files)) as client:
            return await investigate(request(), client, reasoner)

    return asyncio.run(main())


class Chooser(TemplateReasoner):
    """Chooses parameters as scripted, ``{tool: ParameterChoice}``, and otherwise behaves as the template reasoner."""

    def __init__(self, choices: dict[str, ParameterChoice]):
        self.choices = choices

    def choose_parameters(self, role, tool, context):
        return self.choices.get(tool)


# --------------------------------------------------------------------------- choosing parameters

def test_a_permitted_choice_reaches_the_call_and_is_recorded_with_its_rationale(files):
    choices = {
        "confirm_change": ParameterChoice(arguments={"n_bootstrap": 500, "min_days_per_window": 10}, rationale="A lighter bootstrap is enough for a clear change."),
        "decompose_change": ParameterChoice(arguments={"explain_threshold": 0.9, "include_pairs": True}, rationale="Ask for a tighter explaining set."),
        "match_event_candidates": ParameterChoice(arguments={"lead_days": 5}, rationale="Look a little further back for releases."),
        "test_candidates": ParameterChoice(arguments={"lead_days": 4}, rationale="Test the same events the matcher lists."),
    }
    inv = go(files, Chooser(choices))
    assert inv.result.status == "completed"
    args = {c.tool: c.arguments for c in inv.state.log}
    assert args["confirm_change"]["n_bootstrap"] == 500 and args["confirm_change"]["min_days_per_window"] == 10
    assert args["decompose_change"]["explain_threshold"] == 0.9 and args["match_event_candidates"]["lead_days"] == 5
    assert args["test_candidates"]["lead_days"] == 4
    assert inv.state.last("confirm_change").result["n_bootstrap"] == 500  # the tool ran with it
    notes = {n.tool: n for n in inv.result.parameter_choices}
    assert set(notes) == set(choices) and all(n.accepted and n.problem is None for n in notes.values())
    assert notes["confirm_change"].names == ["min_days_per_window", "n_bootstrap"] and notes["confirm_change"].call == 3
    assert inv.result.rejected_statements == []
    verify_traces(inv.result, inv.state)


def test_the_default_reasoner_changes_nothing(files):
    inv = go(files, TemplateReasoner())
    assert inv.result.parameter_choices == []
    for call in inv.state.log:
        assert not set(call.arguments) & {"n_bootstrap", "min_days_per_window", "lead_days", "explain_threshold", "include_pairs"}


@pytest.mark.parametrize("arguments,rationale,fragment", [
    ({"n_bootstrap": True}, "More resamples.", "JSON integer"),
    ({"n_bootstrap": 2000.0}, "More resamples.", "JSON integer"),
    ({"n_bootstrap": "2000"}, "More resamples.", "JSON integer"),
    ({"n_bootstrap": 10}, "Fewer resamples.", "outside the permitted range"),
    ({"n_bootstrap": 10**9}, "Many resamples.", "outside the permitted range"),
    ({"min_days_per_window": 1}, "Allow short windows.", "outside the permitted range"),
    ({"seed": 7}, "Try another seed.", "not a tunable parameter"),
    ({"quality": {"excluded_dates": []}}, "Ignore the exclusions.", "not a tunable parameter"),
    ({"pre_window": {"start": "2026-01-01", "end": "2026-02-01"}}, "Use another baseline.", "not a tunable parameter"),
    ({"apply_exclusions": False}, "Keep the tracking break.", "not a tunable parameter"),
    ({"alpha": 0.5}, "Be lenient.", "not a tunable parameter"),
    ({"n_bootstrap": 600}, "Use 600 resamples.", "digits"),
    ({"n_bootstrap": 600}, "The release caused the drop, so be thorough.", "asserts a cause"),
    ({"n_bootstrap": 600}, "   ", "needs a rationale"),
])
def test_a_refused_choice_is_recorded_and_the_default_is_used(files, default_run, arguments, rationale, fragment):
    inv = go(files, Chooser({"confirm_change": ParameterChoice(arguments=arguments, rationale=rationale)}))
    call = inv.state.last("confirm_change")
    assert call.arguments == default_run.state.last("confirm_change").arguments  # exactly what an unadvised run sends
    (note,) = inv.result.parameter_choices
    assert not note.accepted and note.call is None and fragment in note.problem
    assert inv.result.status == "completed"
    verify_traces(inv.result, inv.state)


def test_a_bool_is_not_a_number_and_a_number_is_not_a_bool():
    with pytest.raises(ParameterError, match="boolean"):
        validate_choice("decompose_change", ParameterChoice(arguments={"include_pairs": 1}, rationale="Pairs."))
    with pytest.raises(ParameterError, match="number"):
        validate_choice("decompose_change", ParameterChoice(arguments={"explain_threshold": True}, rationale="Threshold."))
    assert validate_choice("decompose_change", ParameterChoice(arguments={"explain_threshold": 1 - 0.1}, rationale="Threshold.")) == {"explain_threshold": 0.9}
    with pytest.raises(ParameterError, match="no tunable"):
        validate_choice("power_analysis", ParameterChoice(arguments={"baseline": 0.5}, rationale="Baseline."))


def test_nothing_the_user_or_the_pipeline_owns_is_tunable():
    protected = {"dataset_id", "pre_window", "post_window", "quality", "apply_exclusions", "seed", "alpha", "level", "power", "mde_pp",
                 "expected_patterns", "lag_days", "candidates", "decomposition", "candidate_testing", "focal", "event_date", "did"}
    for tool, spec in TUNABLE.items():
        assert not set(spec) & protected, tool
        assert {p["name"] for p in describe(tool)} == set(spec)


def test_a_reasoner_that_fails_while_choosing_does_not_stop_the_investigation(files):
    class Broken(TemplateReasoner):
        def choose_parameters(self, role, tool, context):
            raise RuntimeError("model unavailable")

    inv = go(files, Broken())
    assert inv.result.status == "completed"
    assert any("the reasoner failed (RuntimeError: model unavailable)" in m for m in inv.result.rejected_statements)


# --------------------------------------------------------------------------- when the reasoner is consulted

class Spy(TemplateReasoner):
    def __init__(self):
        self.events: list[tuple] = []

    def choose_parameters(self, role, tool, context):
        self.events.append(("choose", role.name, tool, len(context.state.log)))

    def interpret(self, role, context):
        self.events.append(("interpret", role.name, len(context.state.log), len(context.claims)))
        return super().interpret(role, context)


def test_each_role_is_interpreted_right_after_its_stages_and_before_the_next_role_runs(files):
    spy = Spy()
    inv = go(files, spy)
    n = len(inv.state.log)
    assert spy.events == [
        ("choose", "Detective", "confirm_change", 2), ("choose", "Detective", "match_event_candidates", 4),
        ("choose", "Detective", "decompose_change", 5), ("interpret", "Detective", 6, 0),
        ("choose", "Scientist", "test_candidates", 6), ("interpret", "Scientist", n - 1, 1),
        ("interpret", "Experiment designer", n, 1),
    ]
    detective_calls = {c.seq for c in inv.state.log if c.role == "Detective"}
    assert max(detective_calls) == 6


def test_a_role_that_hit_a_failure_still_reports_what_it_found(files):
    class RefusePlacebo:
        def __init__(self, inner):
            self.inner = inner

        async def call_tool(self, name, arguments):
            from causelens.agents.toolbox import RawOutcome

            if name == "run_placebo":
                return RawOutcome(None, True, "boom")
            return await self.inner.call_tool(name, arguments)

    spy = Spy()

    async def main():
        async with McpToolClient(server_over(*files)) as client:
            return await investigate(request(), RefusePlacebo(client), spy)

    inv = asyncio.run(main())
    assert inv.result.stopped_stage == "estimate"
    assert [e[:2] for e in spy.events if e[0] == "interpret"] == [("interpret", "Detective"), ("interpret", "Scientist")]
    assert any("headline" in s.text or "supported" in s.text for s in inv.result.statements if s.role == "Scientist")


# --------------------------------------------------------------------------- a model behind the same protocol

class ScriptedModel:
    """Answers each request from a function of ``(task, user_json)``. Records what it was asked."""

    def __init__(self, answer):
        self.answer, self.asked = answer, []

    def complete(self, system: str, user: str) -> str:
        payload = json.loads(user[: user.rindex("\n\n")])
        self.asked.append((system, payload, user))
        return self.answer(payload)


def find(payload, tool, pointer):
    call = next(e["call"] for e in payload["evidence"] if e["tool"] == tool)
    assert pointer in next(e for e in payload["evidence"] if e["call"] == call)["values"]
    return {"call": call, "path": pointer}


def good_answer(payload):
    if payload["task"] == "choose_parameters":
        return json.dumps({"arguments": {}, "rationale": "default"})
    if payload["role"] == "Detective":
        ref = find(payload, "check_data_quality", "/excluded_dates/0")
        return json.dumps({"statements": [{"kind": "caveat", "text": "The data check excluded {day}.", "refs": {"day": {**ref, "fmt": "date"}}}]})
    if payload["role"] == "Scientist":
        (claim,) = [c for c in payload["allowed_claims"] if c["allowed"]]
        refs = {name: ref for name, ref in claim["required_refs"].items()}
        listed = " ".join("{" + k + "}" for k in refs if k.startswith("a"))
        text = ("By difference-in-differences, the change attributed to {event} (lag {lag}, onset {onset}) is {effect} "
                "({lower} to {upper} at {level}, {n} observations), if: " + listed)
        return json.dumps({"statements": [{"kind": "causal_claim", "text": text, "refs": refs}]})
    return json.dumps({"statements": []})


def test_a_model_reasoner_gets_the_rules_the_evidence_and_the_allowed_claims(files):
    model = ScriptedModel(good_answer)
    inv = go(files, ModelReasoner(model))
    assert inv.result.status == "completed" and inv.result.rejected_statements == []
    systems = {payload.get("role"): system for system, payload, _ in model.asked if payload["task"] == "interpret"}
    assert set(systems) == {"Detective", "Scientist", "Experiment designer"}
    for role, system in systems.items():
        assert scientist_rules() in system and f"You are the {role}" in system
    scientist = next(p for _, p, _ in model.asked if p["task"] == "interpret" and p["role"] == "Scientist")
    assert [c["allowed"] for c in scientist["allowed_claims"]] == [True] and scientist["allowed_claims"][0]["required_refs"]["event"]["fmt"] == "text"
    assert {e["tool"] for e in scientist["evidence"]} >= {"check_data_quality", "test_candidates", "estimate_did"}
    detective = next(p for _, p, _ in model.asked if p["task"] == "interpret" and p["role"] == "Detective")
    assert {e["tool"] for e in detective["evidence"]} == {"list_datasets", "check_data_quality", "confirm_change", "detect_change_point",
                                                         "match_event_candidates", "decompose_change"}  # a role sees what has run so far
    texts = [s.text for s in inv.result.statements]
    assert "The data check excluded 2026-09-20." in texts
    (claim,) = [s for s in inv.result.statements if s.kind == "causal_claim"]
    assert CHECKOUT in claim.text and "difference-in-differences" in claim.text
    verify_traces(inv.result, inv.state)


def test_the_model_can_choose_parameters_and_they_are_validated(files):
    def answer(payload):
        if payload["task"] == "choose_parameters" and payload["tool"] == "confirm_change":
            assert {p["name"] for p in payload["parameters"]} == {"n_bootstrap", "min_days_per_window"}
            return 'Sure. {"arguments": {"n_bootstrap": 800}, "rationale": "A smaller bootstrap is enough here."}'
        if payload["task"] == "choose_parameters" and payload["tool"] == "decompose_change":
            return json.dumps({"arguments": {"seed": 1, "explain_threshold": 0.9}, "rationale": "Be tighter."})
        return good_answer(payload)

    inv = go(files, ModelReasoner(ScriptedModel(answer)))
    assert inv.state.last("confirm_change").arguments["n_bootstrap"] == 800
    assert "explain_threshold" not in inv.state.last("decompose_change").arguments  # the whole choice was refused, not trimmed
    accepted = {n.tool: n.accepted for n in inv.result.parameter_choices}
    assert accepted == {"confirm_change": True, "decompose_change": False}


@pytest.mark.parametrize("reply", [
    "I think the data are fine.",  # no JSON
    "{not json}",
    '{"statements": [{"kind": "poem", "text": "x", "refs": {}}]}',
    '{"statements": [{"kind": "finding", "text": "x", "refs": {"a": {"call": 1, "path": "/x", "fmt": "roman"}}}]}',
    '{"statements": "none"}',
    '{"statements": [], "extra": 1}',
])
def test_unreadable_model_output_is_recorded_and_the_investigation_continues(files, reply):
    def answer(payload):
        return json.dumps({"arguments": {}, "rationale": "default"}) if payload["task"] == "choose_parameters" else reply

    inv = go(files, ModelReasoner(ScriptedModel(answer)))
    assert inv.result.status == "completed" and inv.result.statements == []
    assert len(inv.result.rejected_statements) == 3 and all("the reasoner failed (ModelOutputError" in m for m in inv.result.rejected_statements)
    assert inv.result.evidence.strength == "moderate" and inv.result.causal_estimates  # the structured result does not depend on the text
    verify_traces(inv.result, inv.state)


def test_a_hostile_model_cannot_get_a_number_a_cause_or_another_events_estimate_into_the_report(files):
    def answer(payload):
        if payload["task"] == "choose_parameters":
            return json.dumps({"arguments": {"n_bootstrap": 2000}, "rationale": "Keep 2000 resamples."})  # digits in the rationale
        if payload["role"] == "Detective":
            ref = find(payload, "confirm_change", "/verdict")
            return json.dumps({"statements": [
                {"kind": "finding", "text": "Conversion fell by 0.2 points.", "refs": {}},
                {"kind": "finding", "text": "The hero refresh caused the fall.", "refs": {}},
                {"kind": "finding", "text": "The verdict is {v} and p was {p}.", "refs": {"v": {**ref, "fmt": "text"}, "p": {**ref, "fmt": "p"}}},
                {"kind": "finding", "text": "The verdict is {v}.", "refs": {"v": {**ref, "fmt": "text"}}},
            ]})
        if payload["role"] == "Scientist":
            (claim,) = [c for c in payload["allowed_claims"] if c["allowed"]]
            refs = dict(claim["required_refs"])
            refs["event"] = {"call": refs["event"]["call"], "path": "/candidates/0/event", "fmt": "text"}  # the rejected candidate
            listed = " ".join("{" + k + "}" for k in refs if k.startswith("a"))
            text = "By difference-in-differences, {event} {lag} {onset} {effect} {lower} {upper} {level} {n} " + listed
            return json.dumps({"statements": [{"kind": "causal_claim", "text": text, "refs": refs}]})
        return json.dumps({"statements": []})

    inv = go(files, ModelReasoner(ScriptedModel(answer)))
    r = inv.result
    assert [s.text for s in r.statements] == ["The verdict is real_change."]
    assert len(r.rejected_statements) == 4  # three Detective statements and the forged claim
    assert [n.accepted for n in r.parameter_choices] and not any(n.accepted for n in r.parameter_choices)
    assert not [s for s in r.statements if s.kind == "causal_claim"]


def test_a_statement_for_another_role_is_refused(files):
    def answer(payload):
        if payload["task"] == "choose_parameters":
            return json.dumps({"arguments": {}, "rationale": "default"})
        return json.dumps({"statements": [{"kind": "finding", "text": "Nothing to add.", "refs": {}}]})

    inv = go(files, ModelReasoner(ScriptedModel(answer)))
    assert [s.role for s in inv.result.statements] == ["Detective", "Scientist", "Experiment designer"]  # the role is the caller's, not the model's


def test_the_same_scripted_model_gives_the_same_report(files):
    a = go(files, ModelReasoner(ScriptedModel(good_answer)))
    b = go(files, ModelReasoner(ScriptedModel(good_answer)))
    assert a.result.model_dump_json() == b.result.model_dump_json()


# --------------------------------------------------------------------------- the digest a model reads

def test_the_digest_lists_pointers_to_scalar_values_and_caps_its_size(files):
    inv = go(files, TemplateReasoner())
    small = digest(inv.state, max_leaves=5)
    assert all(len(e["values"]) <= 5 for e in small if "values" in e)
    full = digest(inv.state)
    quality = next(e for e in full if e["tool"] == "check_data_quality")
    assert quality["values"]["/excluded_dates/0"] == "2026-09-20"
    assert all(p == "" or p.startswith("/") for e in full for p in e.get("values", {}))
    assert all(len(v) <= 240 for e in full for v in e.get("values", {}).values() if isinstance(v, str))


def test_model_reasoner_only_asks_for_parameters_of_tunable_tools():
    class Never:
        def complete(self, system, user):
            raise AssertionError("the model must not be asked")

    from causelens.agents import InvestigationState

    reasoner = ModelReasoner(Never())
    assert reasoner.choose_parameters(SCIENTIST, "estimate_did", Context(InvestigationState(request=request()), ())) is None


def test_model_output_error_is_a_value_error():
    assert issubclass(ModelOutputError, ValueError)
