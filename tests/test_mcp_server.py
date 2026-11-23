"""The MCP adapter over Streamable HTTP, tested with the official client against the production app (Task 7).

Every call here crosses a real HTTP connection: initialization, tool discovery, schema validation, calls with typed
prior results, equivalence with the direct analytics call, determinism, errors, read-only behaviour and timing.
"""
from __future__ import annotations

import inspect
import math
import os
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import pytest
from jsonschema import Draft202012Validator
from mcp_reference import reference_files
from mcp_support import McpSession, error_text, serve, structured
from scenarios import CHECKOUT

import causelens.analytics as A
from causelens.analytics.loaders import load_events, load_metrics
from causelens.mcp import DatasetConfig, DatasetRegistry
from causelens.mcp.tools import TOOL_NAMES

PRE = ("2026-08-01", "2026-10-13")
POST = ("2026-10-14", "2026-10-31")
FOCAL = {"device": "mobile", "browser": "safari"}
DATA_TOOLS = [t for t in TOOL_NAMES if t not in ("list_datasets", "power_analysis")]


def window(w: tuple[str, str]) -> dict[str, str]:
    return {"start": w[0], "end": w[1]}


def as_json(model: Any) -> Any:
    return model.model_dump(mode="json")


# ------------------------------------------------------------------ recording of what the server touches

_AUDIT: list[tuple[str, Any, Any]] = []
_RECORDING = [False]
_WRITE_FLAGS = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_APPEND | os.O_TRUNC


def _audit(event: str, args: tuple) -> None:
    if not _RECORDING[0]:
        return
    if event == "open":
        _AUDIT.append(("open", str(args[0]), args[2] if len(args) > 2 and isinstance(args[2], int) else 0))
    elif event == "socket.connect":
        _AUDIT.append(("connect", args[1], None))


sys.addaudithook(_audit)


# ------------------------------------------------------------------ fixtures


@pytest.fixture(scope="module")
def files(tmp_path_factory):
    return reference_files(tmp_path_factory)


@pytest.fixture(scope="module")
def registry(files):
    metrics, events = files
    return DatasetRegistry([DatasetConfig("reference", metrics, events, "reference data"), DatasetConfig("bare", metrics)])


@pytest.fixture(scope="module")
def server(registry):
    with serve(registry) as s:
        yield s


@pytest.fixture(scope="module")
def mcp(server):
    with McpSession(server.url) as session:
        yield session


@pytest.fixture(scope="module")
def tools(mcp):
    return {t.name: t for t in mcp.list_tools().tools}


@pytest.fixture(scope="module")
def data(files):
    return load_metrics(files[0]), load_events(files[1])


@pytest.fixture(scope="module")
def prior(data):
    """The prior results a caller would hold, computed by the analytics directly."""
    m, e = data
    q = A.check_data_quality(m, e)
    cp = A.detect_change_point(m, e)
    dec = A.decompose_change(m, PRE, POST, e, quality=q)
    tc = A.test_candidates(m, e, PRE, POST, decomposition=dec, quality=q)
    did = A.estimate_did(m, FOCAL, "2026-10-14", events=e, quality=q)
    return {"q": q, "cp": cp, "dec": dec, "tc": tc, "did": did}


@dataclass
class Case:
    name: str
    tool: str
    arguments: dict[str, Any]
    direct: Callable[[], Any]


@pytest.fixture(scope="module")
def cases(data, prior, registry) -> dict[str, Case]:
    m, e = data
    q, cp, dec, tc, did = (prior[k] for k in ("q", "cp", "dec", "tc", "did"))
    ds = {"dataset_id": "reference"}
    lst = [
        Case("list_datasets", "list_datasets", {}, lambda: registry.catalog()),
        Case("check_data_quality", "check_data_quality", ds, lambda: A.check_data_quality(m, e)),
        Case(
            "confirm_change", "confirm_change",
            {**ds, "pre_window": window(PRE), "post_window": window(POST), "segment": FOCAL, "quality": as_json(q), "n_bootstrap": 300, "seed": 7},
            lambda: A.confirm_change(m, PRE, POST, e, segment=FOCAL, quality=q, n_bootstrap=300, seed=7),
        ),
        Case(
            "detect_change_point", "detect_change_point",
            {**ds, "quality": as_json(q), "search_window": window(("2026-09-01", "2026-10-31")), "min_segment_days": 5},
            lambda: A.detect_change_point(m, e, quality=q, search_window=("2026-09-01", "2026-10-31"), min_segment_days=5),
        ),
        Case("daily_series", "daily_series", {**ds, "segment": FOCAL, "quality": as_json(q)}, lambda: A.daily_series(m, e, segment=FOCAL, quality=q)),
        Case(
            "match_event_candidates", "match_event_candidates", {**ds, "change_point": as_json(cp), "lead_days": 5},
            lambda: A.match_event_candidates(cp, e, lead_days=5),
        ),
        Case(
            "decompose_change", "decompose_change",
            {**ds, "pre_window": window(PRE), "post_window": window(POST), "dimensions": ["device", "browser"], "include_pairs": False,
             "quality": as_json(q), "fdr_family": "partition"},
            lambda: A.decompose_change(m, PRE, POST, e, dimensions=("device", "browser"), include_pairs=False, quality=q, fdr_family="partition"),
        ),
        Case(
            "test_candidates", "test_candidates",
            {**ds, "pre_window": window(PRE), "post_window": window(POST), "decomposition": as_json(dec), "quality": as_json(q),
             "lag_days": {CHECKOUT: 0}, "sensitivity_lags": [0, 1, 2]},
            lambda: A.test_candidates(m, e, PRE, POST, decomposition=dec, quality=q, lag_days={CHECKOUT: 0}, sensitivity_lags=(0, 1, 2)),
        ),
        Case(
            "test_event_scope", "test_event_scope",
            {**ds, "event": CHECKOUT, "pre_window": window(PRE), "post_window": window(POST), "decomposition": as_json(dec),
             "quality": as_json(q), "expected_patterns": {CHECKOUT: {"kind": "unspecified"}}},
            lambda: A.test_event_scope(CHECKOUT, m, e, PRE, POST, decomposition=dec, quality=q,
                                       expected_patterns={CHECKOUT: A.ExpectedPattern(kind="unspecified")}),
        ),
        Case(
            "estimate_did", "estimate_did",
            {**ds, "focal": FOCAL, "event_date": "2026-10-14", "quality": as_json(q), "cov": "cluster", "weights": "none", "level": 0.9},
            lambda: A.estimate_did(m, FOCAL, "2026-10-14", events=e, quality=q, cov="cluster", weights="none", level=0.9),
        ),
        Case(
            "check_pre_trends", "check_pre_trends", {**ds, "focal": [FOCAL], "event_date": "2026-10-14", "quality": as_json(q)},
            lambda: A.check_pre_trends(m, [FOCAL], "2026-10-14", events=e, quality=q),
        ),
        Case(
            "run_placebo", "run_placebo",
            {**ds, "focal": FOCAL, "event_date": "2026-10-14", "quality": as_json(q), "dates": ["2026-09-05", "2026-09-25"]},
            lambda: A.run_placebo(m, FOCAL, "2026-10-14", events=e, quality=q, dates=["2026-09-05", "2026-09-25"]),
        ),
        Case(
            "project_impact", "project_impact", {**ds, "did": as_json(did), "focal": FOCAL, "quality": as_json(q)},
            lambda: A.project_impact(did, A.build_panel(m, FOCAL, events=e, quality=q)),
        ),
        Case(
            "power_analysis", "power_analysis",
            {"baseline": 0.05, "mde_pp": -0.7, "alpha": 0.05, "power": 0.9, "allocation_ratio": 2.0, "n_comparisons": 2},
            lambda: A.power_analysis(0.05, -0.7, alpha=0.05, power=0.9, allocation_ratio=2.0, n_comparisons=2),
        ),
        Case(
            "design_experiment", "design_experiment",
            {**ds, "candidate_testing": as_json(tc), "post_window": window(POST), "quality": as_json(q)},
            lambda: A.design_experiment(m, tc, POST, events=e, quality=q),
        ),
        Case(
            "design_experiment_each_candidate", "design_experiment",
            {**ds, "candidate_testing": as_json(tc), "post_window": window(POST), "quality": as_json(q), "arms": "each_candidate", "mde_pp": 0.4},
            lambda: A.design_experiment(m, tc, POST, events=e, quality=q, arms="each_candidate", mde_pp=0.4),
        ),
    ]
    return {c.name: c for c in lst}


CASE_NAMES = [
    "list_datasets", "check_data_quality", "confirm_change", "detect_change_point", "daily_series", "match_event_candidates", "decompose_change",
    "test_candidates", "test_event_scope", "estimate_did", "check_pre_trends", "run_placebo", "project_impact", "power_analysis",
    "design_experiment", "design_experiment_each_candidate",
]


def test_every_tool_has_a_case(cases):
    assert set(CASE_NAMES) == set(cases)
    assert {c.tool for c in cases.values()} == set(TOOL_NAMES)


# ------------------------------------------------------------------ initialization and discovery


def test_initialization_reports_the_server_and_its_instructions(mcp):
    client = mcp.client
    assert client.server_info.name == "causelens" and client.server_info.version
    assert client.server_capabilities.tools is not None
    assert client.protocol_version
    assert "dataset_id" in client.instructions and "percentage points" in client.instructions and "deterministic" in client.instructions


@pytest.mark.parametrize("mode", ["auto", "legacy"])
def test_both_handshakes_connect_and_see_the_same_tools(server, tools, mode):
    with McpSession(server.url, mode=mode) as session:
        assert session.client.server_info.name == "causelens"
        assert {t.name for t in session.list_tools().tools} == set(tools)


def test_discovery_lists_exactly_the_documented_tools(tools):
    assert set(tools) == set(TOOL_NAMES)
    expected = {"check_data_quality", "confirm_change", "detect_change_point", "decompose_change", "test_event_scope", "estimate_did",
                "check_pre_trends", "run_placebo", "project_impact", "power_analysis", "design_experiment"}
    assert expected <= set(tools), "every tool in the design's analytics table is exposed"
    assert {"test_candidates", "match_event_candidates"} <= set(tools)


def test_every_tool_is_described_read_only_and_typed(tools):
    for name, t in tools.items():
        assert t.description and len(t.description) > 40, name
        a = t.annotations
        assert a.read_only_hint is True and a.destructive_hint is False and a.idempotent_hint is True and a.open_world_hint is False, name
        assert t.input_schema["type"] == "object" and t.output_schema["type"] == "object" and t.output_schema["properties"], name
        Draft202012Validator.check_schema(t.input_schema)
        Draft202012Validator.check_schema(t.output_schema)


def test_data_tools_need_a_dataset_id_and_scalar_tools_do_not(tools):
    for name in DATA_TOOLS:
        assert "dataset_id" in tools[name].input_schema["required"], name
    assert "dataset_id" not in tools["power_analysis"].input_schema["properties"]
    assert set(tools["power_analysis"].input_schema["required"]) == {"baseline", "mde_pp"}
    assert tools["list_datasets"].input_schema.get("properties", {}) == {}


def test_no_tool_accepts_a_path_or_a_url(tools):
    banned = {"path", "file", "filename", "metrics_path", "events_path", "url", "uri", "directory", "metrics", "events"}
    for name, t in tools.items():
        assert not banned & set(t.input_schema["properties"]), name


def test_every_input_schema_rejects_unknown_properties(tools):
    for name, t in tools.items():
        assert t.input_schema.get("additionalProperties") is False, name


def test_strict_argument_models_do_not_leak_into_other_servers():
    import asyncio

    from mcp.server.mcpserver import MCPServer
    from mcp.server.mcpserver.utilities.func_metadata import ArgModelBase

    assert ArgModelBase.model_config.get("extra") is None  # restored after the tools were registered
    other = MCPServer("other")

    @other.tool()
    def echo(x: int = 1) -> int:
        return x

    (listed,) = asyncio.run(other.list_tools())
    assert listed.input_schema.get("additionalProperties") is not False


OUTPUT_MODELS = {
    "list_datasets": "DatasetCatalog", "check_data_quality": A.DataQualityReport, "confirm_change": A.ConfirmChangeResult,
    "detect_change_point": A.ChangePointResult, "match_event_candidates": A.CandidateMatchResult, "decompose_change": A.DecompositionResult,
    "test_candidates": A.CandidateTestingResult, "test_event_scope": A.CandidateVerdict, "estimate_did": A.DidResult,
    "check_pre_trends": A.PreTrendResult, "run_placebo": A.PlaceboResult, "project_impact": A.ImpactResult, "power_analysis": A.PowerResult,
    "design_experiment": A.ExperimentDesign,
}


def test_output_schemas_are_generated_from_the_analytics_models(tools):
    for name, model in OUTPUT_MODELS.items():
        props = set(tools[name].output_schema["properties"])
        if isinstance(model, str):  # the adapter's own catalog model
            assert props == {"datasets"}
        else:
            assert props == set(model.model_fields), name


def test_prior_results_are_accepted_as_typed_models(tools):
    sources = {
        "quality": A.DataQualityReport, "decomposition": A.DecompositionResult, "change_point": A.ChangePointResult,
        "candidate_testing": A.CandidateTestingResult, "did": A.DidResult,
    }
    for tool_name, t in tools.items():
        for param, model in sources.items():
            if param in t.input_schema["properties"]:
                text = str(t.input_schema["properties"][param])
                assert model.__name__ in text or model.__name__ in str(t.input_schema.get("$defs", {}).keys()), (tool_name, param)


def test_published_defaults_match_the_analytics_defaults(tools):
    """A default written in the adapter that differs from the analytics default would silently change results."""
    functions = {name: getattr(A, name) for name in TOOL_NAMES if name not in ("list_datasets", "test_event_scope")}
    functions["test_event_scope"] = A.test_candidates  # same keyword arguments
    checked = 0
    for name, fn in functions.items():
        sig = inspect.signature(fn).parameters
        for param, schema in tools[name].input_schema["properties"].items():
            if "default" not in schema or param not in sig or sig[param].default is inspect.Parameter.empty:
                continue
            default = sig[param].default
            default = list(default) if isinstance(default, tuple) else default
            assert schema["default"] == default, (name, param, schema["default"], default)
            checked += 1
    assert checked > 40


def test_every_analytics_parameter_is_exposed_by_its_tool(tools):
    skipped = {"metrics", "events", "panel", "kwargs"}
    renamed = {"check_data_quality": {"params": "detector_params"}}  # every tool calls the detector settings detector_params
    for name in TOOL_NAMES:
        if name in ("list_datasets", "test_event_scope"):
            continue
        params = {renamed.get(name, {}).get(p, p) for p in inspect.signature(getattr(A, name)).parameters} - skipped
        assert params <= set(tools[name].input_schema["properties"]), (name, params - set(tools[name].input_schema["properties"]))
    scope = set(inspect.signature(A.test_candidates).parameters) - skipped
    assert (scope - {"candidates"}) | {"event"} <= set(tools["test_event_scope"].input_schema["properties"])


# ------------------------------------------------------------------ results: equivalence, schemas, determinism


@pytest.mark.parametrize("name", CASE_NAMES)
def test_a_tool_returns_exactly_what_the_analytics_returns(mcp, cases, name):
    case = cases[name]
    result = mcp.call(case.tool, case.arguments)
    assert structured(result) == as_json(case.direct()), name


@pytest.mark.parametrize("name", CASE_NAMES)
def test_arguments_and_results_validate_against_the_published_schemas(mcp, tools, cases, name):
    case = cases[name]
    schema = tools[case.tool]
    Draft202012Validator(schema.input_schema).validate(case.arguments)
    Draft202012Validator(schema.output_schema).validate(structured(mcp.call(case.tool, case.arguments)))


def _walk(value: Any):
    if isinstance(value, dict):
        for v in value.values():
            yield from _walk(v)
    elif isinstance(value, list):
        for v in value:
            yield from _walk(v)
    else:
        yield value


@pytest.mark.parametrize("name", CASE_NAMES)
def test_results_are_json_safe_numbers_and_dates(mcp, cases, name):
    for leaf in _walk(structured(mcp.call(cases[name].tool, cases[name].arguments))):
        assert not (isinstance(leaf, float) and not math.isfinite(leaf)), name


def test_results_are_deterministic_across_calls_and_across_server_instances(mcp, cases, registry):
    first = {n: structured(mcp.call(c.tool, c.arguments)) for n, c in cases.items()}
    again = {n: structured(mcp.call(c.tool, c.arguments)) for n, c in cases.items()}
    assert first == again
    with serve(registry) as other, McpSession(other.url) as fresh:
        assert {n: structured(fresh.call(c.tool, c.arguments)) for n, c in cases.items()} == first


def test_the_seed_is_passed_through_and_is_what_makes_the_bootstrap_repeatable(mcp, data):
    m, e = data
    args = {"dataset_id": "reference", "pre_window": window(PRE), "post_window": window(POST), "n_bootstrap": 200}
    by_seed = {s: structured(mcp.call("confirm_change", {**args, "seed": s})) for s in (1, 2)}
    for s, got in by_seed.items():
        assert got == as_json(A.confirm_change(m, PRE, POST, e, n_bootstrap=200, seed=s))
    assert by_seed[1] != by_seed[2]
    assert structured(mcp.call("confirm_change", {**args, "seed": 1})) == by_seed[1]


def test_units_and_assumptions_travel_with_the_results(mcp, cases):
    did = structured(mcp.call("estimate_did", cases["estimate_did"].arguments))
    assert did["assumptions"] and did["weights"] == "none" and did["cov"] == "cluster"  # the options asked for are recorded
    assert did["effect_pp"]["level"] == 0.9  # the requested level is preserved, not replaced by a default
    power = structured(mcp.call("power_analysis", cases["power_analysis"].arguments))
    assert power["achieved_power_label"] == "achieved power under the normal approximation" and power["mde_pp"] == -0.7
    design = structured(mcp.call("design_experiment", cases["design_experiment"].arguments))
    assert design["assumptions"] and design["baseline"]["requested_window"] == [POST[0], POST[1]]
    quality = structured(mcp.call("check_data_quality", cases["check_data_quality"].arguments))
    assert quality["exclusion_statement"] and quality["excluded_dates"] == ["2026-09-20"]


def test_the_quality_report_and_the_exclusion_switch_change_the_analysis_and_are_honoured(mcp, data, prior):
    """A report that excludes nothing, and apply_exclusions=false, must give the results the direct call gives, which differ
    from the default. A server that ignored either argument would return the default result and fail here."""
    m, e = data
    lenient = A.check_data_quality(m, e, A.DetectorParams(k=1000.0, sessions_k=1000.0))
    assert lenient.excluded_dates == [] and prior["q"].excluded_dates != []
    base = {"dataset_id": "reference", "focal": FOCAL, "event_date": "2026-10-14"}
    default = structured(mcp.call("estimate_did", base))
    via_report = structured(mcp.call("estimate_did", {**base, "quality": as_json(lenient)}))
    via_switch = structured(mcp.call("estimate_did", {**base, "apply_exclusions": False}))
    assert via_report == as_json(A.estimate_did(m, FOCAL, "2026-10-14", events=e, quality=lenient))
    assert via_switch == as_json(A.estimate_did(m, FOCAL, "2026-10-14", events=e, apply_exclusions=False))
    assert default["excluded_dates"] == ["2026-09-20"] and via_report["excluded_dates"] == [] and via_switch["excluded_dates"] == []
    assert via_report != default and via_switch != default
    detector = structured(mcp.call("check_data_quality", {"dataset_id": "reference", "detector_params": as_json(A.DetectorParams(k=1000.0, sessions_k=1000.0))}))
    assert detector["excluded_dates"] == [] and detector == as_json(lenient)


def _variant_calls(m, e):
    """For each tool that takes ``quality`` / ``apply_exclusions``: (arguments without them, direct call given them)."""
    ds = {"dataset_id": "reference"}
    win = {"pre_window": window(PRE), "post_window": window(POST)}
    did_args = {"focal": FOCAL, "event_date": "2026-10-14"}

    def tc_of(opts):
        return A.test_candidates(m, e, PRE, POST, **opts)

    return {
        "confirm_change": ({**ds, **win}, lambda o: A.confirm_change(m, PRE, POST, e, **o)),
        "detect_change_point": (ds, lambda o: A.detect_change_point(m, e, **o)),
        "decompose_change": ({**ds, **win}, lambda o: A.decompose_change(m, PRE, POST, e, **o)),
        "test_candidates": ({**ds, **win}, tc_of),
        "test_event_scope": ({**ds, **win, "event": CHECKOUT}, lambda o: A.test_event_scope(CHECKOUT, m, e, PRE, POST, **o)),
        "estimate_did": ({**ds, **did_args}, lambda o: A.estimate_did(m, FOCAL, "2026-10-14", events=e, **o)),
        "check_pre_trends": ({**ds, **did_args}, lambda o: A.check_pre_trends(m, FOCAL, "2026-10-14", events=e, **o)),
        "run_placebo": ({**ds, **did_args}, lambda o: A.run_placebo(m, FOCAL, "2026-10-14", events=e, **o)),
    }


@pytest.mark.parametrize("variant", ["lenient_quality_report", "apply_exclusions_false"])
def test_every_tool_honours_the_quality_report_and_the_exclusion_switch(mcp, data, variant):
    """Each tool must pass these through. Ignoring either returns the default result, which differs, so this fails."""
    m, e = data
    lenient = A.check_data_quality(m, e, A.DetectorParams(k=1000.0, sessions_k=1000.0))
    given = {"quality": as_json(lenient)} if variant == "lenient_quality_report" else {"apply_exclusions": False}
    direct_opts = {"quality": lenient} if variant == "lenient_quality_report" else {"apply_exclusions": False}
    different = 0
    for tool, (base, direct) in _variant_calls(m, e).items():
        got = structured(mcp.call(tool, {**base, **given}))
        assert got == as_json(direct(direct_opts)), (tool, variant)
        different += got != structured(mcp.call(tool, base))
    assert different >= 6, "the exclusion of 2026-09-20 should change most results, otherwise this test proves little"

    # the two tools that rebuild a population from a prior result take the same settings
    tc = A.test_candidates(m, e, PRE, POST, **direct_opts)
    design = structured(mcp.call("design_experiment", {"dataset_id": "reference", "candidate_testing": as_json(tc), "post_window": window(POST), **given}))
    assert design == as_json(A.design_experiment(m, tc, POST, events=e, **direct_opts))
    did = A.estimate_did(m, FOCAL, "2026-10-14", events=e, **direct_opts)
    impact = structured(mcp.call("project_impact", {"dataset_id": "reference", "did": as_json(did), "focal": FOCAL, **given}))
    assert impact == as_json(A.project_impact(did, A.build_panel(m, FOCAL, events=e, **direct_opts)))


def test_an_explicit_comparison_population_is_used_by_every_tool_that_takes_one(mcp, data):
    m, e = data
    cmp_seg = [{"device": "desktop", "browser": "chrome"}, {"device": "desktop", "browser": "safari"}]
    base = {"dataset_id": "reference", "focal": FOCAL, "event_date": "2026-10-14"}
    direct = {
        "estimate_did": lambda c: A.estimate_did(m, FOCAL, "2026-10-14", comparison=c, events=e),
        "check_pre_trends": lambda c: A.check_pre_trends(m, FOCAL, "2026-10-14", comparison=c, events=e),
        "run_placebo": lambda c: A.run_placebo(m, FOCAL, "2026-10-14", comparison=c, events=e),
    }
    for tool, fn in direct.items():
        got = structured(mcp.call(tool, {**base, "comparison": cmp_seg}))
        assert got == as_json(fn(cmp_seg)), tool
        assert got != structured(mcp.call(tool, base)), tool  # the comparison matters, so ignoring it would be visible
    did = A.estimate_did(m, FOCAL, "2026-10-14", comparison=cmp_seg, events=e)
    args = {"dataset_id": "reference", "did": as_json(did), "focal": FOCAL}
    ok = structured(mcp.call("project_impact", {**args, "comparison": cmp_seg}))
    assert ok == as_json(A.project_impact(did, A.build_panel(m, FOCAL, comparison=cmp_seg, events=e)))
    refused = error_text(mcp.call("project_impact", args))  # the default comparison is a different population
    assert "not computed on this dataset and population" in refused and "comparison_sessions_post" in refused


def test_a_chain_of_calls_passes_each_result_to_the_next_unchanged(mcp, data):
    m, e = data
    ds = {"dataset_id": "reference"}
    q = structured(mcp.call("check_data_quality", ds))
    dec = structured(mcp.call("decompose_change", {**ds, "pre_window": window(PRE), "post_window": window(POST), "quality": q}))
    tc = structured(mcp.call("test_candidates", {**ds, "pre_window": window(PRE), "post_window": window(POST), "decomposition": dec, "quality": q}))
    design = structured(mcp.call("design_experiment", {**ds, "candidate_testing": tc, "post_window": window(POST), "quality": q}))
    did = structured(mcp.call("estimate_did", {**ds, "focal": tc["focal"]["segments"], "event_date": "2026-10-14", "quality": q}))
    impact = structured(mcp.call("project_impact", {**ds, "did": did, "focal": tc["focal"]["segments"], "quality": q}))
    q0 = A.check_data_quality(m, e)
    dec0 = A.decompose_change(m, PRE, POST, e, quality=q0)
    tc0 = A.test_candidates(m, e, PRE, POST, decomposition=dec0, quality=q0)
    assert tc == as_json(tc0) and tc["leading"] == CHECKOUT
    assert design == as_json(A.design_experiment(m, tc0, POST, events=e, quality=q0))
    did0 = A.estimate_did(m, tc0.focal.segments, "2026-10-14", events=e, quality=q0)
    assert did == as_json(did0)
    assert impact == as_json(A.project_impact(did0, A.build_panel(m, tc0.focal.segments, events=e, quality=q0)))


# ------------------------------------------------------------------ insufficient data is a result, not an error


def test_insufficient_data_results_are_returned_not_raised(mcp, data, prior):
    m, e = data
    short = ("2026-10-14", "2026-10-16")
    r = mcp.call("confirm_change", {"dataset_id": "reference", "pre_window": window(("2026-10-10", "2026-10-13")), "post_window": window(short)})
    assert not r.is_error and structured(r)["status"] == "insufficient_data"
    assert structured(r) == as_json(A.confirm_change(m, ("2026-10-10", "2026-10-13"), short, e))

    r = mcp.call("estimate_did", {"dataset_id": "reference", "focal": FOCAL, "event_date": "2026-10-30"})
    did = structured(r)
    assert did["status"] == "insufficient" and did["effect_pp"] is None and did["reason"]
    assert did == as_json(A.estimate_did(m, FOCAL, "2026-10-30", events=e))

    r = mcp.call("project_impact", {"dataset_id": "reference", "did": did, "focal": FOCAL})
    assert structured(r)["status"] == "unavailable"

    r = mcp.call("design_experiment", {"dataset_id": "reference", "candidate_testing": as_json(prior["tc"]), "post_window": window(("2026-10-14", "2026-10-18")),
                                       "quality": as_json(prior["q"])})
    design = structured(r)
    assert design["status"] == "needs_input" and design["needs"] == ["post_window"] and design["plan"] is None


# ------------------------------------------------------------------ invalid input gives a clear error

ERROR_CASES = [
    ("unknown dataset", "estimate_did", {"dataset_id": "nope", "focal": FOCAL, "event_date": "2026-10-14"}, "unknown dataset_id 'nope'; configured datasets: bare, reference"),
    ("no event log", "test_candidates", {"dataset_id": "bare", "pre_window": window(PRE), "post_window": window(POST)}, "no events file configured"),
    ("reversed windows", "confirm_change", {"dataset_id": "reference", "pre_window": window(POST), "post_window": window(PRE)}, "must end before"),
    ("a bad date", "confirm_change", {"dataset_id": "reference", "pre_window": {"start": "2026-13-01", "end": "2026-10-13"}, "post_window": window(POST)}, "real calendar date"),
    ("a missing argument", "confirm_change", {"dataset_id": "reference", "pre_window": window(PRE)}, "post_window"),
    ("a misspelt argument", "power_analysis", {"baseline": 0.05, "mde_pp": 1.0, "mde": 2.0}, "Extra inputs are not permitted"),
    ("unknown candidate", "test_candidates", {"dataset_id": "reference", "pre_window": window(PRE), "post_window": window(POST), "candidates": ["No such event"]}, "unknown candidate"),
    ("negative lag", "test_candidates", {"dataset_id": "reference", "pre_window": window(PRE), "post_window": window(POST), "lag_days": {CHECKOUT: -1}}, "non-negative"),
    ("fractional lag", "test_candidates", {"dataset_id": "reference", "pre_window": window(PRE), "post_window": window(POST), "lag_days": {CHECKOUT: 1.5}}, "integer"),
    ("boolean lag", "test_candidates", {"dataset_id": "reference", "pre_window": window(PRE), "post_window": window(POST), "lag_days": {CHECKOUT: True}}, "integer"),
    ("a numeric string for a count", "confirm_change", {"dataset_id": "reference", "pre_window": window(PRE), "post_window": window(POST), "n_bootstrap": "5"}, "integer"),
    ("unknown dimension", "estimate_did", {"dataset_id": "reference", "focal": {"colour": "red"}, "event_date": "2026-10-14"}, "colour"),
    ("a segment with no rows", "estimate_did", {"dataset_id": "reference", "focal": {"device": "toaster"}, "event_date": "2026-10-14"}, "no rows match"),
    ("a bad decomposition dimension", "decompose_change", {"dataset_id": "reference", "pre_window": window(PRE), "post_window": window(POST), "dimensions": ["colour"]}, "colour"),
    ("baseline as a percentage", "power_analysis", {"baseline": 5.0, "mde_pp": 1.0}, "fraction"),
    ("effect as a fraction of the baseline", "power_analysis", {"baseline": 0.05, "mde_pp": -5.0}, "percentage points"),
]


@pytest.mark.parametrize("label, tool, arguments, expected", ERROR_CASES, ids=[c[0] for c in ERROR_CASES])
def test_invalid_input_is_a_clear_tool_error(mcp, files, label, tool, arguments, expected):
    text = error_text(mcp.call(tool, arguments))
    assert expected.lower() in text.lower(), (label, text)
    assert "Traceback" not in text and str(files[0].parent) not in text


def test_an_unknown_choice_for_an_option_names_the_allowed_values(mcp, prior):
    text = error_text(mcp.call("design_experiment", {"dataset_id": "reference", "candidate_testing": as_json(prior["tc"]), "post_window": window(POST), "arms": "all"}))
    assert "leading" in text and "each_candidate" in text


def test_an_unknown_tool_is_an_error(mcp):
    try:
        text = error_text(mcp.call("not_a_tool", {}))
    except Exception as exc:  # noqa: BLE001 - the SDK may raise a protocol error for an unknown tool
        text = str(exc)
    assert "not_a_tool" in text or "unknown" in text.lower()


def test_a_result_for_another_population_is_refused_not_silently_reused(mcp, prior):
    did = as_json(prior["did"])
    ds = {"dataset_id": "reference", "did": did, "quality": as_json(prior["q"])}
    text = error_text(mcp.call("project_impact", {**ds, "focal": {"device": "desktop", "browser": "chrome"}}))
    assert "not computed on this dataset and population" in text and "focal_sessions_post" in text
    text = error_text(mcp.call("project_impact", {**ds, "focal": FOCAL, "apply_exclusions": False}))
    assert "excluded_dates" in text
    assert not mcp.call("project_impact", {**ds, "focal": FOCAL}).is_error  # the matching population is accepted


def test_a_candidate_result_made_with_other_exclusions_is_refused(mcp, prior):
    args = {"dataset_id": "reference", "candidate_testing": as_json(prior["tc"]), "post_window": window(POST), "quality": as_json(prior["q"])}
    assert not mcp.call("design_experiment", args).is_error
    text = error_text(mcp.call("design_experiment", {**args, "apply_exclusions": False}))
    assert "was not computed on this dataset and population" in text and "exclusions" in text and "apply_exclusions" in text


def test_a_decomposition_for_other_windows_is_refused(mcp, prior):
    args = {"dataset_id": "reference", "pre_window": window(("2026-08-01", "2026-10-12")), "post_window": window(POST), "decomposition": as_json(prior["dec"])}
    text = error_text(mcp.call("test_candidates", args))
    assert "differs from the windows given" in text
    assert "differs from the windows given" in error_text(mcp.call("test_event_scope", {**args, "event": CHECKOUT}))


def test_a_malformed_prior_result_is_a_validation_error(mcp, prior):
    broken = as_json(prior["did"])
    del broken["event_date"]
    text = error_text(mcp.call("project_impact", {"dataset_id": "reference", "did": broken, "focal": FOCAL}))
    assert "event_date" in text


# ------------------------------------------------------------------ read-only, no answer key, no network


def _run_everything(mcp, cases) -> None:
    for case in cases.values():
        assert not mcp.call(case.tool, case.arguments).is_error, case.name


def test_the_answer_key_is_never_opened_and_nothing_is_written_to_the_data(mcp, cases, files):
    data_dir = str(files[0].parent)
    _AUDIT.clear()
    _RECORDING[0] = True
    try:
        _run_everything(mcp, cases)
    finally:
        _RECORDING[0] = False
    opened = [(kind, what, flags) for kind, what, flags in _AUDIT if kind == "open"]
    assert opened, "the audit hook saw no file opens, so this test would prove nothing"
    assert any(str(files[0]) in what for _, what, _ in opened), "the metrics file was read"
    assert not [what for _, what, _ in opened if "ground_truth" in what.lower()]
    assert not [what for _, what, flags in opened if what.startswith(data_dir) and flags & _WRITE_FLAGS]


def test_the_server_makes_no_network_connections_except_to_the_local_client(mcp, cases, server):
    _AUDIT.clear()
    _RECORDING[0] = True
    try:
        _run_everything(mcp, cases)
        with McpSession(server.url) as extra:  # a new connection is local too
            extra.call("power_analysis", {"baseline": 0.05, "mde_pp": 1.0})
    finally:
        _RECORDING[0] = False
    connects = [addr for kind, addr, _ in _AUDIT if kind == "connect"]
    assert connects, "the audit hook saw no connections, so this test would prove nothing"
    assert {addr[0] for addr in connects if isinstance(addr, tuple)} <= {"127.0.0.1", "::1", "localhost"}


def test_the_data_files_are_unchanged_by_every_tool(mcp, cases, files):
    import hashlib

    paths = [files[0], files[1]]
    before = [(hashlib.sha256(p.read_bytes()).hexdigest(), p.stat().st_mtime_ns) for p in paths]
    listing = sorted(os.listdir(files[0].parent))
    _run_everything(mcp, cases)
    assert [(hashlib.sha256(p.read_bytes()).hexdigest(), p.stat().st_mtime_ns) for p in paths] == before
    assert sorted(os.listdir(files[0].parent)) == listing


# ------------------------------------------------------------------ transport options


def test_stateless_json_responses_return_the_same_results(registry, cases, mcp):
    with serve(registry, stateless=True, json_response=True) as s, McpSession(s.url) as plain:
        for name in ("power_analysis", "check_data_quality", "estimate_did"):
            c = cases[name]
            assert structured(plain.call(c.tool, c.arguments)) == structured(mcp.call(c.tool, c.arguments))


def test_the_endpoint_path_is_configurable(registry):
    with serve(registry, path="/analytics") as s, McpSession(s.url) as session:
        assert s.url.endswith("/analytics") and structured(session.call("power_analysis", {"baseline": 0.05, "mde_pp": 1.0}))["n_control"] == 8158


# ------------------------------------------------------------------ timing (loading and serialization included)

TIME_LIMIT_S = 2.0  # Requirement 9.3, judged on the median of repeated calls
HANG_GUARD_S = 6.0  # no single call may take longer than this, however loaded the machine


def _median(values: list[float]) -> float:
    ordered = sorted(values)
    return ordered[len(ordered) // 2]


@pytest.mark.parametrize("name", CASE_NAMES)
def test_each_call_completes_in_under_two_seconds_over_http(mcp, cases, name):
    """Loading, the analytics and serialization are all inside the timed call. The requirement is judged on the median of five
    calls because a wall-clock limit on a shared machine is otherwise a test of the machine: pinned to one core with three
    busy loops competing, the heaviest tool's slowest call crossed 2 s while its median stayed within the limit. A single
    call over HANG_GUARD_S still fails."""
    c = cases[name]
    mcp.call(c.tool, c.arguments)  # warm the interpreter's imports once; the server still loads and serializes on every call
    timings = [mcp.timed_call(c.tool, c.arguments)[1] for _ in range(5)]
    assert _median(timings) < TIME_LIMIT_S, (name, timings)
    assert max(timings) < HANG_GUARD_S, (name, timings)


def test_a_cold_connection_and_the_heaviest_call_fit_in_two_seconds(server, cases):
    case = cases["test_candidates"]
    elapsed = []
    for _ in range(3):
        start = time.perf_counter()
        with McpSession(server.url) as fresh:
            result = fresh.call(case.tool, case.arguments)
            elapsed.append(time.perf_counter() - start)
        assert not result.is_error
    assert _median(elapsed) < TIME_LIMIT_S and max(elapsed) < HANG_GUARD_S, elapsed


def test_the_arguments_that_are_typed_dicts_survive_a_json_string_round_trip(mcp):
    """Some clients send object arguments as JSON strings; the SDK parses them before validation."""
    import json

    r = mcp.call("estimate_did", {"dataset_id": "reference", "focal": json.dumps(FOCAL), "event_date": "2026-10-14"})
    assert not r.is_error and structured(r)["status"] == "ok"


# ------------------------------------------------------------------ result content and concurrency


def test_the_text_block_repeats_the_structured_content_for_older_clients(mcp, cases):
    import json

    for name in ("power_analysis", "estimate_did", "design_experiment"):
        c = cases[name]
        result = mcp.call(c.tool, c.arguments)
        assert json.loads(result.content[0].text) == result.structured_content == as_json(c.direct())


def test_concurrent_clients_get_the_same_results_as_sequential_calls(server, mcp, cases):
    import threading

    names = ("estimate_did", "check_pre_trends", "run_placebo", "test_candidates", "decompose_change", "project_impact")
    expected = {n: structured(mcp.call(cases[n].tool, cases[n].arguments)) for n in names}
    got: dict[tuple[int, str], Any] = {}
    errors: list[BaseException] = []

    def work(k: int) -> None:
        try:
            with McpSession(server.url) as session:
                for n in names:
                    got[(k, n)] = structured(session.call(cases[n].tool, cases[n].arguments))
        except BaseException as exc:  # noqa: BLE001 - reported below
            errors.append(exc)

    threads = [threading.Thread(target=work, args=(k,)) for k in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=120)
    assert not errors, errors
    assert len(got) == 4 * len(names) and all(got[(k, n)] == expected[n] for k in range(4) for n in names)
