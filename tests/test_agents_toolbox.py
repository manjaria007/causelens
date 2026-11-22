"""Roles, the call log and the MCP client binding. Role limits are checked against a recording client; the binding against the
real server."""
from __future__ import annotations

import asyncio
from typing import Any

import pytest
from agents_support import request, server_over
from mcp_reference import reference_files

from causelens.agents import (
    DETECTIVE,
    EXPERIMENT_DESIGNER,
    ROLES,
    SCIENTIST,
    InvestigationState,
    McpToolClient,
    Prior,
    Toolbox,
    ToolCallError,
    ToolNotAllowed,
)
from causelens.agents.roles import RULES_FILE, STEERING_DIR, scientist_rules
from causelens.agents.state import PRIOR_ARGUMENT_SOURCE, TOOL_MODELS
from causelens.agents.toolbox import RawOutcome
from causelens.mcp.tools import TOOL_NAMES


class Recorder:
    """Records what would be sent. Answers from a table of canned outcomes."""

    def __init__(self, answers: dict[str, RawOutcome]):
        self.answers, self.sent = answers, []

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> RawOutcome:
        self.sent.append((name, arguments))
        return self.answers[name]


def box(answers: dict[str, RawOutcome]) -> tuple[Toolbox, Recorder]:
    recorder = Recorder(answers)
    return Toolbox(recorder, InvestigationState(request=request())), recorder


def run(coro):
    return asyncio.run(coro)


def ok(result: dict) -> RawOutcome:
    return RawOutcome(result, False, None)


# --------------------------------------------------------------------------- roles

def test_the_three_roles_split_the_tools_and_cover_every_stage():
    assert [r.name for r in ROLES.values()] == ["Detective", "Scientist", "Experiment designer"]
    assert DETECTIVE.tools.isdisjoint(SCIENTIST.tools) and SCIENTIST.tools.isdisjoint(EXPERIMENT_DESIGNER.tools)
    covered = {s for r in ROLES.values() for s in r.stages}
    assert covered == {"validate", "confirm", "change_point", "localize", "test", "estimate", "recommend"}
    for r in ROLES.values():
        assert r.tools <= set(TOOL_NAMES)


def test_the_design_tools_are_present_in_the_right_roles():
    assert {"check_data_quality", "confirm_change", "detect_change_point", "decompose_change"} <= DETECTIVE.tools
    assert {"test_event_scope", "estimate_did", "run_placebo", "check_pre_trends", "project_impact"} <= SCIENTIST.tools
    assert EXPERIMENT_DESIGNER.tools == {"power_analysis", "design_experiment"}


def test_every_server_tool_has_a_result_model_and_a_role():
    assert set(TOOL_MODELS) == set(TOOL_NAMES)
    assert set().union(*(r.tools for r in ROLES.values())) == set(TOOL_NAMES)


def test_prompts_embed_the_scientist_rules_verbatim():
    rules = scientist_rules()
    assert rules == (STEERING_DIR / RULES_FILE).read_text(encoding="utf-8").strip()
    for role in ROLES.values():
        prompt = role.system_prompt()
        assert rules in prompt
        assert role.name in prompt and all(tool in prompt for tool in role.tools)
        assert "never compute" in prompt.lower()


# --------------------------------------------------------------------------- enforcement and the log

def test_a_tool_outside_the_role_is_refused_before_anything_is_sent():
    toolbox, recorder = box({})
    with pytest.raises(ToolNotAllowed, match="Detective"):
        run(toolbox.call(DETECTIVE, "test", "test_candidates", dataset_id="reference"))
    with pytest.raises(ToolNotAllowed):
        run(toolbox.call(EXPERIMENT_DESIGNER, "recommend", "estimate_did"))
    assert recorder.sent == [] and toolbox.state.log == []


def test_every_call_is_logged_with_its_role_stage_and_result():
    toolbox, recorder = box({"power_analysis": ok({"n_control": 8158}), "list_datasets": ok({"datasets": []})})
    a = run(toolbox.call(EXPERIMENT_DESIGNER, "recommend", "power_analysis", baseline=0.05, mde_pp=1.0))
    b = run(toolbox.call(DETECTIVE, "validate", "list_datasets"))
    assert [(c.seq, c.role, c.stage, c.tool) for c in toolbox.state.log] == [
        (1, "Experiment designer", "recommend", "power_analysis"), (2, "Detective", "validate", "list_datasets")]
    assert a.arguments == {"baseline": 0.05, "mde_pp": 1.0} and a.result == {"n_control": 8158} and b.result == {"datasets": []}
    assert recorder.sent[0] == ("power_analysis", {"baseline": 0.05, "mde_pp": 1.0})


def test_a_prior_result_is_sent_in_full_and_logged_as_a_pointer():
    big = {"excluded_dates": ["2026-09-20"], "flags": [{"x": 1}]}
    toolbox, recorder = box({"check_data_quality": ok(big), "confirm_change": ok({"verdict": "real_change"})})
    q = run(toolbox.call(DETECTIVE, "validate", "check_data_quality", dataset_id="reference"))
    c = run(toolbox.call(DETECTIVE, "confirm", "confirm_change", dataset_id="reference", quality=Prior(q.seq)))
    assert recorder.sent[1][1]["quality"] == big
    assert c.arguments["quality"] == {"$prior": 1}
    assert toolbox.state.log[1].model_dump_json().count("excluded_dates") == 0


def test_a_prior_must_come_from_the_tool_that_produces_that_argument():
    toolbox, _ = box({"list_datasets": ok({"datasets": []}), "confirm_change": ok({})})
    run(toolbox.call(DETECTIVE, "validate", "list_datasets"))
    with pytest.raises(ValueError, match="must be the result of check_data_quality"):
        run(toolbox.call(DETECTIVE, "confirm", "confirm_change", quality=Prior(1)))
    assert set(PRIOR_ARGUMENT_SOURCE.values()) <= set(TOOL_NAMES)


def test_a_failed_call_is_logged_and_raised():
    toolbox, _ = box({"check_data_quality": RawOutcome(None, True, "Unknown dataset 'nope'")})
    with pytest.raises(ToolCallError) as caught:
        run(toolbox.call(DETECTIVE, "validate", "check_data_quality", dataset_id="nope"))
    entry = toolbox.state.log[0]
    assert caught.value.call == entry and entry.is_error and entry.result is None and "nope" in (entry.error or "")


# --------------------------------------------------------------------------- the official client against the real server

@pytest.fixture(scope="module")
def files(tmp_path_factory):
    return reference_files(tmp_path_factory)


def test_the_official_client_lists_all_tools_and_returns_structured_results(files):
    async def go():
        async with McpToolClient(server_over(*files)) as client:
            names = await client.list_tool_names()
            ok_result = await client.call_tool("power_analysis", {"baseline": 0.05, "mde_pp": 1.0})
            refused = await client.call_tool("power_analysis", {"baseline": 0.05, "mde_pp": True})
            return names, ok_result, refused

    names, ok_result, refused = run(go())
    assert sorted(names) == sorted(TOOL_NAMES)
    assert not ok_result.is_error and ok_result.structured is not None and ok_result.structured["n_control"] == 8158
    assert refused.is_error and refused.structured is None and "valid number" in (refused.error or "")
