"""End-to-end investigations through the real MCP server (Task 8; Requirements 8.1, 8.2, 8.4).

The answer key (``ground_truth``) is used only to grade. The orchestrator never reads it, and the server refuses to serve it.
"""
from __future__ import annotations

import asyncio
import json
import string
import warnings
from collections import Counter
from datetime import date

import pytest
from agents_support import POST, PRE, request, run, server_over, write_frames
from mcp_reference import reference_files
from scenarios import scenario_data

from causelens.agents import (
    ROLES,
    Context,
    Investigation,
    InvestigationRequest,
    McpToolClient,
    Ref,
    Statement,
    TemplateReasoner,
    Traced,
    TracedInterval,
    TraceError,
    assess_claim,
    investigate,
    stdio_target,
    verify_traces,
)
from causelens.agents.report import Facts, build_evidence
from causelens.agents.state import InvestigationState, LoggedCall
from causelens.agents.trace import CAUSAL_WORDS, Tracer, iter_traced
from causelens.analytics import ExpectedPattern

warnings.filterwarnings("ignore")

CHECKOUT, HERO = "Checkout v4.2", "Homepage hero refresh"
STRENGTHS = {"strong", "moderate", "weak", "insufficient"}


@pytest.fixture(scope="module")
def files(tmp_path_factory):
    return reference_files(tmp_path_factory)


@pytest.fixture(scope="module")
def reference_run(files) -> Investigation:
    return run(*files)


def values(items) -> list:
    return [t.value for t in items]


# --------------------------------------------------------------------------- the reference investigation

def test_the_reference_investigation_recovers_the_planted_answer(reference_run, ground_truth):
    r, grading = reference_run.result, ground_truth["grading"]
    assert r.status == "completed" and r.stopped_reason is None
    supported = [c.event.value for c in r.candidates if c.verdict.value == "supported"]
    rejected = [c.event.value for c in r.candidates if c.verdict.value == "rejected"]
    assert supported == [grading["must_identify_event"]]
    assert set(rejected) == set(grading["must_reject"])
    assert r.localization is not None and r.localization.localized
    assert values(r.localization.segments) == ["device=mobile, browser=safari"]
    assert values(r.data_quality.excluded_dates) == grading["must_flag_data_quality"]
    estimate = r.causal_estimates[0].effect_pp
    truth = ground_truth["planted_cause"]["true_effect_in_segment_pp"]
    assert estimate.lower <= truth <= estimate.upper


def test_the_stages_run_in_order_under_the_right_roles(reference_run):
    stages = reference_run.state.stages
    assert [(s.stage, s.role, s.outcome) for s in stages] == [
        ("validate", "Detective", "done"), ("confirm", "Detective", "done"), ("change_point", "Detective", "done"),
        ("localize", "Detective", "done"), ("test", "Scientist", "done"), ("estimate", "Scientist", "done"),
        ("recommend", "Experiment designer", "done"),
    ]
    assert [c.tool for c in reference_run.state.log] == [
        "list_datasets", "check_data_quality", "confirm_change", "detect_change_point", "match_event_candidates", "decompose_change",
        "test_candidates", "estimate_did", "check_pre_trends", "run_placebo", "project_impact", "design_experiment",
    ]
    covered = [n for s in stages for n in s.calls]
    assert covered == list(range(1, len(reference_run.state.log) + 1))


def test_every_logged_call_was_made_by_a_role_allowed_to_make_it(reference_run):
    for call in reference_run.state.log:
        assert call.tool in ROLES[call.role].tools
        assert call.stage in ROLES[call.role].stages
        assert not call.is_error and call.result is not None


def test_prior_results_are_logged_as_pointers_to_the_call_that_produced_them(reference_run):
    log = reference_run.state.log
    for call in log:
        for value in call.arguments.values():
            if isinstance(value, dict) and "$prior" in value:
                assert value["$prior"] < call.seq
                assert log[value["$prior"] - 1].tool in {"check_data_quality", "detect_change_point", "decompose_change", "test_candidates", "estimate_did"}
    assert Counter(c.arguments.get("quality", {}).get("$prior") for c in log if "quality" in c.arguments) == {2: 9}  # every analysis after the data check reuses its exclusions


# --------------------------------------------------------------------------- scientist rules

def test_every_number_in_the_result_traces_to_a_logged_tool_result(reference_run):
    checked = verify_traces(reference_run.result, reference_run.state)
    assert checked > 150
    for item in iter_traced(reference_run.result):
        assert 1 <= item.call <= len(reference_run.state.log)


def test_a_forged_number_in_the_result_is_caught(reference_run):
    estimate = reference_run.result.causal_estimates[0].effect_pp
    forged = estimate.model_copy(update={"estimate": estimate.estimate * 0.5})
    with pytest.raises(TraceError, match="estimate"):
        verify_traces(forged, reference_run.state)


def test_the_result_holds_no_untraced_numbers(reference_run):
    """Every int or float in the dumped result is inside a traced object (it has ``call`` and ``path``) or is a log position."""
    log_positions = {"n_calls", "stopped_call", "source_call", "did_call", "pre_trend_call", "placebo_call", "verdict_call", "calls"}
    stray: list[str] = []

    def walk(node, where=""):
        if isinstance(node, dict):
            if {"call", "path"} <= node.keys():
                return
            for k, v in node.items():
                if k not in log_positions:
                    walk(v, f"{where}/{k}")
        elif isinstance(node, list):
            for i, v in enumerate(node):
                walk(v, f"{where}/{i}")
        elif isinstance(node, int | float) and not isinstance(node, bool):
            stray.append(f"{where}={node}")

    walk(reference_run.result.model_dump(mode="json"))
    assert stray == []


def test_every_estimate_has_an_interval_with_its_sample_size(reference_run):
    intervals = [i for i in iter_traced(reference_run.result) if isinstance(i, TracedInterval)]
    assert len(intervals) >= 8
    for i in intervals:
        assert i.lower <= i.estimate <= i.upper, i.label
        assert 0.0 < i.level < 1.0 and i.n, i.label
        assert all(isinstance(n.value, int) and n.value >= 0 for n in i.n), i.label


def test_effect_sizes_and_p_values_travel_together(reference_run):
    c = reference_run.result.confirmed_change
    assert c.noise_p_value is not None and c.abs_change_pp is not None and c.effect_size_pre_sd is not None
    e = reference_run.result.causal_estimates[0]
    assert e.pre_trend_p_value is None or e.pre_trend_slope_pp_per_day is not None


def test_evidence_strength_is_reported_with_a_reason(reference_run):
    ev = reference_run.result.evidence
    assert ev.strength in STRENGTHS and ev.reason and ev.reasons
    assert ev.strength == "moderate" and ev.base_strength == "strong"  # capped at moderate by release ambiguity (tool-assessed)
    assert ev.weakest_event is not None and ev.weakest_event.value == CHECKOUT
    capped = {t.value for t in ev.reasons}
    assert any("ambiguous" in s for s in capped)


def test_a_causal_claim_carries_its_design_and_assumptions(reference_run):
    e = reference_run.result.causal_estimates[0]
    assert e.design == "difference-in-differences" and e.claim.allowed and e.matches_candidate_test
    assert e.pre_trend_status.value == "pass" and e.placebo_status.value == "pass"
    assert len(e.assumptions) >= 3 and any("comparison" in a.value.lower() for a in e.assumptions)
    assert "separate, labelled claim" in e.attribution_label
    claims = [s for s in reference_run.result.statements if s.kind == "causal_claim"]
    assert len(claims) == 1
    text = claims[0].text
    assert "difference-in-differences" in text and "assumptions hold" in text
    assert any(a.value in text for a in e.assumptions)  # the assumptions sit next to the number


def test_no_other_sentence_asserts_a_cause(reference_run):
    for s in reference_run.result.statements:
        literal = "".join(piece for piece, *_ in string.Formatter().parse(s.statement.text))
        if s.kind != "causal_claim":
            assert not CAUSAL_WORDS.search(literal), s.text
        assert not any(ch.isdigit() for ch in literal)


def test_at_least_one_alternative_is_considered_and_the_rejection_is_not_overstated(reference_run):
    r = reference_run.result
    assert len(r.alternatives_considered) >= 2
    hero = next(c for c in r.candidates if c.event.value == HERO)
    assert "sole" in hero.headline.value and "does not mean" in " ".join(values(hero.reasons)).lower() + hero.why.lower()
    assert hero.lag_sensitivity and {v.lag_days.value for v in hero.lag_sensitivity} == {0, 1}
    assert hero.attribution_ambiguous.value is True


def test_multiple_comparisons_and_the_unexplained_part_are_reported(reference_run):
    r = reference_run.result
    assert r.localization.n_tests_scanned.value > 10 and "false discovery rate" in r.localization.multiple_comparisons
    assert r.localization.all_segments_significant.value is True
    u = r.unexplained
    assert u is not None and u.remaining_share_of_change is not None and u.rate_effect_not_distinguishable_pp is not None
    assert u.remaining_share_of_change.value < 0  # the localized segment over-explains: shown as returned, not forced to a whole
    assert "not forced" in u.note


def test_data_quality_is_reported_before_behaviour(reference_run):
    q = reference_run.result.data_quality
    assert values(q.excluded_dates) == ["2026-09-20"] and "tracking break is a valid explanation" in q.note
    desktop = [f for f in q.flags if f.segment.value == "device=desktop"]
    assert len(desktop) == 1 and desktop[0].linked_event.value == "Analytics tag deploy"
    assert q.flags and all(f.kind.value == "possible_tracking_issue" for f in q.flags)
    assert reference_run.state.log[1].tool == "check_data_quality"  # first analysis, before any behavioural claim


def test_the_recommended_experiment_has_every_required_field(reference_run):
    x = reference_run.result.recommended_experiment
    assert x.status.value == "ok" and x.purpose.value == "settle"  # strength below strong
    for field in (x.hypothesis, x.randomization_unit, x.primary_metric, x.mde_pp, x.n_per_arm_control, x.duration_days):
        assert field is not None
    assert x.guardrails and x.arms and x.achieved_power is not None
    assert x.n_per_arm_control.unit == "sessions" and x.n_per_arm_control.call == reference_run.state.last("design_experiment").seq


def test_every_stage_summary_statement_is_validated_and_rendered_from_the_log(reference_run):
    r = reference_run.result
    assert r.rejected_statements == [] and reference_run.state.notes == []
    assert {s.role for s in r.statements} == {"Detective", "Scientist", "Experiment designer"}
    for s in r.statements:
        assert "{" not in s.text and s.text
        assert all(ref.call <= len(reference_run.state.log) for ref in s.statement.refs.values())
    assert any("concentrated in device=mobile, browser=safari" in s.text for s in r.statements)
    assert any("coincide in time" in s.text for s in r.statements)


def test_the_result_round_trips_through_json(reference_run):
    text = reference_run.result.model_dump_json()
    again = type(reference_run.result).model_validate_json(text)
    assert again == reference_run.result
    assert json.loads(reference_run.state.model_dump_json())["log"][0]["tool"] == "list_datasets"


def test_the_same_request_gives_the_same_log_and_result(files, reference_run):
    second = run(*files)
    assert second.state.model_dump_json() == reference_run.state.model_dump_json()
    assert second.result.model_dump_json() == reference_run.result.model_dump_json()


# --------------------------------------------------------------------------- declared inputs reach the tools

def test_declared_patterns_and_lags_are_passed_unchanged_and_never_inferred(files):
    pattern = ExpectedPattern(kind="uniform", scale="relative")
    inv = run(*files, expected_patterns={HERO: pattern}, lag_days={HERO: 1})
    call = inv.state.last("test_candidates")
    assert call.arguments["expected_patterns"] == {HERO: {"kind": "uniform", "scale": "relative"}}
    assert call.arguments["lag_days"] == {HERO: 1}
    hero = next(c for c in inv.result.candidates if c.event.value == HERO)
    assert hero.lag_days.value == 1
    plain = run(*files)
    assert "expected_patterns" not in plain.state.last("test_candidates").arguments


# --------------------------------------------------------------------------- stopping and gaps

def stopped_scenario(tmp_path):
    for seed in range(1, 6):
        m, e = scenario_data(seed, "none")
        inv = run(*write_frames(tmp_path, m, e))
        if inv.result.status == "stopped_change_not_confirmed":
            return inv
    raise AssertionError("no seed stopped at confirm_change")


def test_an_unconfirmed_change_stops_the_investigation_with_what_data_would_be_needed(tmp_path):
    inv = stopped_scenario(tmp_path)
    r = inv.result
    assert [c.tool for c in inv.state.log] == ["list_datasets", "check_data_quality", "confirm_change"]
    assert r.evidence.strength == "insufficient" and "not distinguishable from noise" in r.evidence.reason
    assert r.stopped_call == 3 and r.confirmed_change.verdict.value == "not_distinguishable_from_noise"
    assert r.change_point is None and r.localization is None and r.candidates == [] and r.causal_estimates == []
    assert r.recommended_experiment is None and "stopped" in r.experiment_note
    assert r.causal_estimate_note and r.confirmed_change.data_needed
    assert [(s.stage, s.outcome) for s in inv.state.stages] == [("validate", "done"), ("confirm", "stopped")]
    assert {s.role for s in r.statements} == {"Detective"}
    verify_traces(r, inv.state)


def test_unsupported_candidates_give_insufficient_evidence_and_an_experiment_for_each_open_candidate(tmp_path):
    for seed in range(1, 8):
        m, e = scenario_data(seed, "weak_6")
        inv = run(*write_frames(tmp_path, m, e))
        if inv.result.status == "completed" and inv.result.candidates:
            break
    else:
        pytest.skip("no weak_6 seed reached candidate testing")
    r = inv.result
    assert not any(c.verdict.value == "supported" for c in r.candidates)
    assert r.evidence.strength == "insufficient" and r.causal_estimates == [] and r.causal_estimate_note
    assert r.unexplained is not None and not r.unexplained.explained_by_a_supported_event
    assert [s.outcome for s in inv.state.stages if s.stage == "estimate"] == ["skipped"]
    x = r.recommended_experiment
    assert x.status.value == "ok" and x.purpose.value == "settle"
    assert inv.state.last("design_experiment").arguments["arms"] == "each_candidate"
    assert not [s for s in r.statements if s.kind == "causal_claim"]
    verify_traces(r, inv.state)


def test_a_dataset_without_an_event_log_skips_candidate_testing(files, tmp_path):
    inv = run(files[0], None)
    r = inv.result
    assert r.status == "completed" and r.candidates == [] and r.dataset.events_sha256 is None
    assert "no event log" in r.evidence.reason and r.evidence.strength == "insufficient"
    assert {c.tool for c in inv.state.log} == {"list_datasets", "check_data_quality", "confirm_change", "detect_change_point", "decompose_change"}
    assert [(s.stage, s.outcome) for s in inv.state.stages if s.outcome == "skipped"] == [("test", "skipped"), ("estimate", "skipped"), ("recommend", "skipped")]
    verify_traces(r, inv.state)


def test_an_unknown_dataset_stops_cleanly(files):
    inv = investigate_with(files, request("nope"))
    assert inv.result.status == "stopped_insufficient_data" and inv.result.dataset is None and inv.result.stopped_call == 1
    assert [c.tool for c in inv.state.log] == ["list_datasets"]


def test_a_failing_tool_call_is_logged_and_stops_the_run(files):
    bad = request(pre=POST, post=PRE)  # the windows are the wrong way round
    inv = investigate_with(files, bad)
    r = inv.result
    assert r.status == "stopped_tool_error" and r.stopped_call == 3 and r.evidence.strength == "insufficient"
    failed = inv.state.call(3)
    assert failed.is_error and failed.result is None and failed.error
    assert inv.state.stages[-1].outcome == "stopped"
    verify_traces(r, inv.state)


def investigate_with(files, req: InvestigationRequest) -> Investigation:
    async def go():
        async with McpToolClient(server_over(*files)) as client:
            return await investigate(req, client)

    return asyncio.run(go())


# --------------------------------------------------------------------------- the reasoner cannot get a number or a cause into the report

class Scripted(TemplateReasoner):
    """A stand-in for a model: interprets as scripted and keeps the tools' parameter defaults."""

    def __init__(self, statements: list[Statement]):
        self.statements = statements

    def interpret(self, role, context: Context) -> list[Statement]:
        return [s for s in self.statements if s.role == role.name]


def with_reasoner(files, statements: list[Statement]) -> Investigation:
    async def go():
        async with McpToolClient(server_over(*files)) as client:
            return await investigate(request(), client, Scripted(statements))

    return asyncio.run(go())


def test_statements_that_break_the_rules_are_dropped_and_recorded(files):
    good = Statement(role="Detective", kind="finding", text="The data check ran first: {v}.", refs={"v": Ref(call=2, path="/exclusion_statement")})
    hostile = [
        Statement(role="Detective", kind="finding", text="Conversion fell by 0.2 points.", refs={}),  # a number the model typed
        Statement(role="Detective", kind="finding", text="The hero refresh caused the drop.", refs={}),  # a cause, no design
        Statement(role="Detective", kind="finding", text="Effect is {v}.", refs={"v": Ref(call=3, path="/no/such/field")}),
        Statement(role="Scientist", kind="causal_claim", text="The release caused {v}.",
                  refs={"v": Ref(call=7, path="/candidates/1/lag_days", fmt="count")}),  # cites the candidate test, not a DiD
    ]
    inv = with_reasoner(files, [good, *hostile])
    r = inv.result
    assert [s.statement for s in r.statements] == [good]
    assert len(r.rejected_statements) == 4
    assert any("digits" in m for m in r.rejected_statements) and any("asserts a cause" in m for m in r.rejected_statements)
    assert any("does not resolve" in m for m in r.rejected_statements) and any("difference-in-differences" in m for m in r.rejected_statements)
    verify_traces(r, inv.state)


def test_a_causal_claim_is_withheld_when_a_check_did_not_pass(reference_run):
    state = reference_run.state.model_copy(deep=True)
    claim = reference_run.result.causal_estimates[0].claim
    assert claim.allowed
    for tool, status, fragment in (("check_pre_trends", "fail", "pre-trend"), ("run_placebo", "borderline", "placebo")):
        broken = state.model_copy(deep=True)
        call = broken.last(tool)
        broken.log[call.seq - 1] = call.model_copy(update={"result": {**call.result, "status": status}})
        again = assess_claim(broken, event=CHECKOUT, verdict_call=claim.verdict_call, verdict_path=claim.verdict_path,
                             did_call=claim.did_call, pre_trend_call=claim.pre_trend_call, placebo_call=claim.placebo_call)
        assert not again.allowed and fragment in again.reason
    zero = state.model_copy(deep=True)
    did = zero.call(claim.did_call)
    interval = {**did.result["effect_pp"], "lower": -0.01, "upper": 0.01}
    zero.log[did.seq - 1] = did.model_copy(update={"result": {**did.result, "effect_pp": interval}})
    again = assess_claim(zero, event=CHECKOUT, verdict_call=claim.verdict_call, verdict_path=claim.verdict_path,
                         did_call=claim.did_call, pre_trend_call=claim.pre_trend_call, placebo_call=claim.placebo_call)
    assert not again.allowed and "includes zero" in again.reason
    unsupported = state.model_copy(deep=True)
    test = unsupported.last("test_candidates")
    candidates = [dict(c) for c in test.result["candidates"]]
    index = int(claim.verdict_path.split("/")[2])
    candidates[index]["verdict"] = "inconclusive"
    unsupported.log[test.seq - 1] = test.model_copy(update={"result": {**test.result, "candidates": candidates}})
    again = assess_claim(unsupported, event=CHECKOUT, verdict_call=claim.verdict_call, verdict_path=claim.verdict_path,
                         did_call=claim.did_call, pre_trend_call=claim.pre_trend_call, placebo_call=claim.placebo_call)
    assert not again.allowed and "not supported" in again.reason


def test_the_template_reasoner_withholds_the_claim_sentence_when_not_allowed(reference_run):
    claim = reference_run.result.causal_estimates[0].claim
    blocked = claim.model_copy(update={"allowed": False})
    statements = TemplateReasoner().interpret(ROLES["Scientist"], Context(reference_run.state, (blocked,)))
    assert not [s for s in statements if s.kind == "causal_claim"]
    caveat = [s for s in statements if s.kind == "caveat"]
    assert len(caveat) == 1 and "No causal claim is made" in caveat[0].text


# --------------------------------------------------------------------------- over a real process

def test_the_same_investigation_runs_over_stdio_and_gives_the_same_numbers(files, reference_run):
    async def go():
        async with McpToolClient(stdio_target(*files)) as client:
            return await investigate(request(), client)

    over_stdio = asyncio.run(go())
    assert over_stdio.result.status == "completed"
    assert over_stdio.result.model_dump_json() == reference_run.result.model_dump_json()
    assert [c.model_dump_json() for c in over_stdio.state.log] == [c.model_dump_json() for c in reference_run.state.log]


def test_dates_in_the_result_are_iso_strings_from_the_tools(reference_run):
    d = reference_run.result.causal_estimates[0].onset_date
    assert isinstance(d, Traced) and date.fromisoformat(d.value).isoformat() == d.value == "2026-10-14"


# --------------------------------------------------------------------------- the weakest supported candidate sets the strength

def test_strength_is_that_of_the_weakest_supported_candidate():
    def candidate(name, strength):
        return {"event": name, "verdict": "supported", "evidence": {"strength": strength, "base_strength": strength, "reasons": [f"{strength} reason"]}}

    for order in (("strong", "weak"), ("weak", "strong")):
        state = InvestigationState(request=request())
        doc = {"status": "ok", "candidates": [candidate("A", order[0]), candidate("B", order[1])]}
        state.log.append(LoggedCall(seq=1, role="Scientist", stage="test", tool="test_candidates", arguments={}, result=doc))
        evidence = build_evidence(Tracer(state), Facts(testing=1), None)
        assert evidence.strength == "weak" and [r.value for r in evidence.reasons] == ["weak reason"]
        assert evidence.weakest_event.value == ("B" if order[0] == "strong" else "A")


# --------------------------------------------------------------------------- the standalone estimate is cross-checked

class Perturbing:
    """Delegates to the real client, but nudges the DiD embedded in the candidate test so it no longer equals the standalone one."""

    def __init__(self, inner):
        self.inner = inner

    async def call_tool(self, name, arguments):
        outcome = await self.inner.call_tool(name, arguments)
        if name == "test_candidates" and outcome.structured is not None:
            for cand in outcome.structured["candidates"]:
                if cand.get("differential_effect"):
                    cand["differential_effect"]["std_error_pp"] += 1.0
        return outcome


def test_a_standalone_estimate_that_differs_from_the_embedded_one_is_reported(files):
    async def go():
        async with McpToolClient(server_over(*files)) as client:
            return await investigate(request(), Perturbing(client))

    inv = asyncio.run(go())
    assert inv.result.causal_estimates[0].matches_candidate_test is False
    assert inv.state.notes and "differs from the one embedded" in inv.state.notes[0]
    # The server also refuses to build on the tampered result, so the run stops at the experiment design.
    assert inv.result.status == "stopped_tool_error" and inv.state.call(inv.result.stopped_call).tool == "design_experiment"


def test_the_same_investigation_runs_over_streamable_http_and_gives_the_same_numbers(files, reference_run):
    from mcp_support import RunningServer

    from causelens.mcp import DatasetConfig, DatasetRegistry

    server = RunningServer(DatasetRegistry([DatasetConfig("reference", files[0], files[1])]))
    try:
        async def go():
            async with McpToolClient(server.url) as client:
                return await investigate(request(), client)

        over_http = asyncio.run(go())
    finally:
        server.stop()
    assert over_http.result.status == "completed"
    assert over_http.result.model_dump_json() == reference_run.result.model_dump_json()
    assert [c.model_dump_json() for c in over_http.state.log] == [c.model_dump_json() for c in reference_run.state.log]
