"""Regression tests for the PR #7 review: prior-result binding, strict scalars, symlinked answer key, malformed events.

Each group was reproduced against commit 0b288c7 first (the failing runs are recorded in KIRO_NOTES.md).
The datasets here are in-memory variants of the reference data (tests/mcp_variants.py); the real answer key is never used.
"""
from __future__ import annotations

import copy
import json
import os
import sys
from pathlib import Path
from typing import Any

import pytest
from mcp_reference import reference_files
from mcp_support import McpSession, error_text, serve, structured
from mcp_variants import plain, rising, swapped, twin
from scenarios import CHECKOUT

import causelens.analytics as A
from causelens.analytics.loaders import load_events, load_metrics
from causelens.mcp import DatasetConfig, DatasetConfigError, DatasetRegistry
from causelens.mcp.__main__ import main

PRE = ("2026-08-01", "2026-10-13")
POST = ("2026-10-14", "2026-10-31")
FOCAL = {"device": "mobile", "browser": "safari"}
CHROME = {"device": "mobile", "browser": "chrome"}


def window(w: tuple[str, str]) -> dict[str, str]:
    return {"start": w[0], "end": w[1]}


def as_json(model: Any) -> Any:
    return model.model_dump(mode="json")


# ------------------------------------------------------------------ the datasets


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    metrics_file, events_file = reference_files(tmp_path_factory)
    base = load_metrics(metrics_file)
    out = tmp_path_factory.mktemp("variants")
    frames = {"reference": base, "rising": rising(base), "twin": twin(base), "plain": plain(base), "swapped": swapped(base)}
    configs = []
    for name, frame in frames.items():
        path = metrics_file if name == "reference" else out / f"{name}.csv"
        if name != "reference":
            frame.to_csv(path, index=False)
        configs.append(DatasetConfig(name, path, events_file))
    return {"registry": DatasetRegistry(configs), "frames": {k: load_metrics(Path(c.metrics)) for k, c in zip(frames, configs, strict=True)},
            "events": load_events(events_file), "metrics_file": metrics_file, "events_file": events_file}


@pytest.fixture(scope="module")
def mcp(world):
    with serve(world["registry"]) as server, McpSession(server.url) as session:
        yield session


def pipeline(frame, events):
    """The direct analytics chain a caller would hold results from."""
    q = A.check_data_quality(frame, events)
    dec = A.decompose_change(frame, PRE, POST, events, quality=q)
    tc = A.test_candidates(frame, events, PRE, POST, decomposition=dec, quality=q)
    did = A.estimate_did(frame, FOCAL, "2026-10-14", events=events, quality=q)
    return {"q": q, "dec": dec, "tc": tc, "did": did}


@pytest.fixture(scope="module")
def ref(world):
    return pipeline(world["frames"]["reference"], world["events"])


@pytest.fixture(scope="module")
def rise(world):
    return pipeline(world["frames"]["rising"], world["events"])


# ------------------------------------------------------------------ 1. prior results are bound to their source


def test_the_variants_are_what_the_tests_say_they_are(world, ref, rise):
    assert ref["did"].effect_pp.estimate < -0.6 and rise["did"].effect_pp.estimate > 0.6  # the opposite effect ...
    assert ref["did"].focal_sessions_post == rise["did"].focal_sessions_post  # ... on the same sessions
    assert ref["did"].comparison_sessions_post == rise["did"].comparison_sessions_post and ref["did"].n_days_post == rise["did"].n_days_post
    assert ref["q"].excluded_dates and not pipeline_quality(world, "plain").excluded_dates


def pipeline_quality(world, name):
    return A.check_data_quality(world["frames"][name], world["events"])


def test_a_decline_from_one_dataset_is_not_accepted_for_a_rise_in_another(mcp, ref, rise):
    args = {"focal": FOCAL, "quality": as_json(ref["q"])}
    text = error_text(mcp.call("project_impact", {"dataset_id": "rising", "did": as_json(ref["did"]), **args}))
    assert "not computed on this dataset and population" in text and "effect_pp" in text
    # the matching pairs are accepted, and give the sign each dataset really has
    ok_ref = structured(mcp.call("project_impact", {"dataset_id": "reference", "did": as_json(ref["did"]), **args}))
    ok_rise = structured(mcp.call("project_impact", {"dataset_id": "rising", "did": as_json(rise["did"]), "focal": FOCAL, "quality": as_json(rise["q"])}))
    assert ok_ref["overall_pp"]["estimate"] < 0 < ok_rise["overall_pp"]["estimate"]


def test_an_equal_sized_wrong_population_is_refused(mcp, world):
    twins, events = world["frames"]["twin"], world["events"]
    safari = A.estimate_did(twins, FOCAL, "2026-10-14", events=events)
    chrome = A.estimate_did(twins, CHROME, "2026-10-14", events=events)
    assert (safari.focal_sessions_post, safari.comparison_sessions_post, safari.n_days_pre, safari.excluded_dates) == (
        chrome.focal_sessions_post, chrome.comparison_sessions_post, chrome.n_days_pre, chrome.excluded_dates)
    assert abs(safari.effect_pp.estimate - chrome.effect_pp.estimate) > 0.5  # the populations differ in effect only
    text = error_text(mcp.call("project_impact", {"dataset_id": "twin", "did": as_json(safari), "focal": CHROME}))
    assert "not computed on this dataset and population" in text
    assert not mcp.call("project_impact", {"dataset_id": "twin", "did": as_json(safari), "focal": FOCAL}).is_error
    assert not mcp.call("project_impact", {"dataset_id": "twin", "did": as_json(chrome), "focal": CHROME}).is_error


@pytest.mark.parametrize("field, edit", [
    ("effect_pp", lambda d: d["effect_pp"].update(estimate=d["effect_pp"]["estimate"] * 1.5)),
    ("p_value", lambda d: d.update(p_value=0.5)),
    ("std_error_pp", lambda d: d.update(cov="cluster")),  # the result is recomputed with the cov it claims, so its numbers disagree
    ("effect_pp", lambda d: d.update(weights="none")),
    ("excluded_dates", lambda d: d.update(excluded_dates=[])),
])
def test_an_edited_did_result_is_refused(mcp, ref, field, edit):
    did = copy.deepcopy(as_json(ref["did"]))
    edit(did)
    text = error_text(mcp.call("project_impact", {"dataset_id": "reference", "did": did, "focal": FOCAL, "quality": as_json(ref["q"])}))
    assert "not computed on this dataset and population" in text and field in text


def test_a_candidate_result_for_another_dataset_is_refused_and_the_direction_is_the_data_s(mcp, ref, rise):
    stale = error_text(mcp.call("design_experiment", {"dataset_id": "rising", "candidate_testing": as_json(ref["tc"]), "post_window": window(POST),
                                                      "quality": as_json(rise["q"])}))
    assert "candidate-testing result was not computed on this dataset" in stale
    fresh = structured(mcp.call("design_experiment", {"dataset_id": "rising", "candidate_testing": as_json(rise["tc"]), "post_window": window(POST),
                                                      "quality": as_json(rise["q"])}))
    old = structured(mcp.call("design_experiment", {"dataset_id": "reference", "candidate_testing": as_json(ref["tc"]), "post_window": window(POST),
                                                    "quality": as_json(ref["q"])}))
    assert fresh["mde"]["mde_pp"] < 0 < old["mde"]["mde_pp"]  # a rollback undoes a rise and a decline in opposite directions
    assert " lower conversion" in fresh["hypothesis"] and " raise conversion" in old["hypothesis"]


@pytest.mark.parametrize("edit, where", [
    (lambda r: r["candidates"][1]["differential_effect"]["effect_pp"].update(lower=-0.01, upper=0.02), "effect_pp"),
    (lambda r: r["timing"].update(pooled_sd_pp=r["timing"]["pooled_sd_pp"] * 2), "pooled_sd_pp"),
    (lambda r: r["candidates"][1].update(onset_date="2026-10-15"), "timing"),
    (lambda r: r["exclusions"].update(statement="x"), "exclusions"),
])
def test_an_edited_candidate_result_is_refused(mcp, ref, edit, where):
    tc = copy.deepcopy(as_json(ref["tc"]))
    assert tc["candidates"][1]["event"] == CHECKOUT and tc["candidates"][1]["differential_effect"] is not None
    edit(tc)
    text = error_text(mcp.call("design_experiment", {"dataset_id": "reference", "candidate_testing": tc, "post_window": window(POST), "quality": as_json(ref["q"])}))
    assert "was not computed on this dataset" in text and where in text


@pytest.mark.parametrize("tool, extra", [
    ("confirm_change", {"pre_window": window(PRE), "post_window": window(POST)}),
    ("detect_change_point", {}),
    ("decompose_change", {"pre_window": window(PRE), "post_window": window(POST)}),
    ("test_candidates", {"pre_window": window(PRE), "post_window": window(POST)}),
    ("test_event_scope", {"pre_window": window(PRE), "post_window": window(POST), "event": CHECKOUT}),
    ("estimate_did", {"focal": FOCAL, "event_date": "2026-10-14"}),
    ("check_pre_trends", {"focal": FOCAL, "event_date": "2026-10-14"}),
    ("run_placebo", {"focal": FOCAL, "event_date": "2026-10-14"}),
])
def test_a_quality_report_for_another_dataset_is_refused_by_every_tool_that_takes_one(mcp, ref, tool, extra):
    args = {"dataset_id": "plain", **extra, "quality": as_json(ref["q"])}  # the reference report excludes 2026-09-20; plain has no outage
    text = error_text(mcp.call(tool, args))
    assert "quality report was not produced from this dataset" in text and "excluded_dates" in text
    fine = mcp.call(tool, {**args, "quality": as_json(pipeline_quality_cached(mcp, "plain"))})
    assert not fine.is_error, error_text(fine) if fine.is_error else ""


def pipeline_quality_cached(mcp, name):
    return A.DataQualityReport.model_validate(structured(mcp.call("check_data_quality", {"dataset_id": name})))


def test_a_hand_edited_quality_report_is_refused(mcp, ref):
    q = copy.deepcopy(as_json(ref["q"]))
    q["excluded_dates"] = []
    text = error_text(mcp.call("estimate_did", {"dataset_id": "reference", "focal": FOCAL, "event_date": "2026-10-14", "quality": q}))
    assert "quality report was not produced from this dataset" in text and "apply_exclusions" in text and "detector_params" in text


def test_a_quality_report_made_with_other_detector_settings_is_accepted_when_it_reproduces(mcp, world):
    lenient = A.check_data_quality(world["frames"]["reference"], world["events"], A.DetectorParams(k=1000.0, sessions_k=1000.0))
    assert not mcp.call("estimate_did", {"dataset_id": "reference", "focal": FOCAL, "event_date": "2026-10-14", "quality": as_json(lenient)}).is_error


def test_a_decomposition_for_another_dataset_is_refused(mcp, ref, rise):
    args = {"pre_window": window(PRE), "post_window": window(POST), "decomposition": as_json(ref["dec"]), "quality": as_json(rise["q"])}
    for tool, extra in (("test_candidates", {}), ("test_event_scope", {"event": CHECKOUT})):
        text = error_text(mcp.call(tool, {"dataset_id": "rising", **args, **extra}))
        assert "decomposition was not computed on this dataset" in text and "orders" in text, tool
        assert not mcp.call(tool, {"dataset_id": "rising", **args, "decomposition": as_json(rise["dec"]), **extra}).is_error


def test_binding_changes_no_value_for_matching_inputs(mcp, ref, world):
    """The check only refuses. A result that reproduces comes back exactly as the analytics return it."""
    events = world["events"]
    frame = world["frames"]["reference"]
    out = structured(mcp.call("project_impact", {"dataset_id": "reference", "did": as_json(ref["did"]), "focal": FOCAL, "quality": as_json(ref["q"])}))
    assert out == as_json(A.project_impact(ref["did"], A.build_panel(frame, FOCAL, events=events, quality=ref["q"])))
    out = structured(mcp.call("design_experiment", {"dataset_id": "reference", "candidate_testing": as_json(ref["tc"]), "post_window": window(POST),
                                                    "quality": as_json(ref["q"])}))
    assert out == as_json(A.design_experiment(frame, ref["tc"], POST, events=events, quality=ref["q"]))


def test_a_result_that_a_different_process_serialized_still_matches(mcp, ref):
    """Round-tripping through JSON text must not break the comparison (floats are compared to 1e-9 relative)."""
    did = json.loads(json.dumps(as_json(ref["did"])))
    assert not mcp.call("project_impact", {"dataset_id": "reference", "did": did, "focal": FOCAL, "quality": json.loads(json.dumps(as_json(ref["q"])))}).is_error


# ------------------------------------------------------------------ 2. strict scalars

STRICT_CASES = [
    ("power_analysis", {"baseline": 0.05, "mde_pp": True}, "mde_pp"),
    ("power_analysis", {"baseline": 0.05, "mde_pp": "0.5"}, "mde_pp"),
    ("power_analysis", {"baseline": "0.05", "mde_pp": 1.0}, "baseline"),
    ("power_analysis", {"baseline": 0.05, "mde_pp": 1.0, "allocation_ratio": True}, "allocation_ratio"),
    ("power_analysis", {"baseline": 0.05, "mde_pp": 1.0, "allocation_ratio": "2"}, "allocation_ratio"),
    ("power_analysis", {"baseline": 0.05, "mde_pp": 1.0, "alpha": "0.05"}, "alpha"),
    ("power_analysis", {"baseline": 0.05, "mde_pp": 1.0, "power": True}, "power"),
    ("power_analysis", {"baseline": 0.05, "mde_pp": 1.0, "n_comparisons": True}, "n_comparisons"),
    ("power_analysis", {"baseline": 0.05, "mde_pp": 1.0, "n_comparisons": "2"}, "n_comparisons"),
    ("power_analysis", {"baseline": 0.05, "mde_pp": 1.0, "n_comparisons": 2.0}, "n_comparisons"),
    ("confirm_change", {"dataset_id": "reference", "pre_window": window(PRE), "post_window": window(POST), "level": "0.9"}, "level"),
    ("confirm_change", {"dataset_id": "reference", "pre_window": window(PRE), "post_window": window(POST), "alpha": True}, "alpha"),
    ("confirm_change", {"dataset_id": "reference", "pre_window": window(PRE), "post_window": window(POST), "apply_exclusions": "false"}, "apply_exclusions"),
    ("confirm_change", {"dataset_id": "reference", "pre_window": window(PRE), "post_window": window(POST), "apply_exclusions": 0}, "apply_exclusions"),
    ("detect_change_point", {"dataset_id": "reference", "adjust_weekdays": "no"}, "adjust_weekdays"),
    ("detect_change_point", {"dataset_id": "reference", "plausible_level": "0.95"}, "plausible_level"),
    ("decompose_change", {"dataset_id": "reference", "pre_window": window(PRE), "post_window": window(POST), "explain_threshold": True}, "explain_threshold"),
    ("decompose_change", {"dataset_id": "reference", "pre_window": window(PRE), "post_window": window(POST), "max_localized_traffic_share": "0.5"}, "max_localized_traffic_share"),
    ("decompose_change", {"dataset_id": "reference", "pre_window": window(PRE), "post_window": window(POST), "include_pairs": 1}, "include_pairs"),
    ("estimate_did", {"dataset_id": "reference", "focal": FOCAL, "event_date": "2026-10-14", "level": True}, "level"),
    ("estimate_did", {"dataset_id": "reference", "focal": FOCAL, "event_date": 20261014}, "event_date"),
    ("estimate_did", {"dataset_id": "reference", "focal": FOCAL, "event_date": True}, "event_date"),
    ("check_pre_trends", {"dataset_id": "reference", "focal": FOCAL, "event_date": "2026-10-14", "apply_exclusions": "true"}, "apply_exclusions"),
    ("confirm_change", {"dataset_id": "reference", "pre_window": {"start": 1, "end": "2026-10-13"}, "post_window": window(POST)}, "start"),
    # nested typed models are validated strictly too
    ("check_data_quality", {"dataset_id": "reference", "detector_params": {"k": True}}, "k"),
    ("check_data_quality", {"dataset_id": "reference", "detector_params": {"k": "5"}}, "k"),
    ("check_data_quality", {"dataset_id": "reference", "detector_params": {"window_days": 15.5}}, "window_days"),
    ("test_candidates", {"dataset_id": "reference", "pre_window": window(PRE), "post_window": window(POST),
                         "expected_patterns": {CHECKOUT: {"kind": "restricted", "scope": {"device": "mobile"}, "equivalence_margin_pp": True}}}, "equivalence_margin_pp"),
]


@pytest.mark.parametrize("tool, arguments, field", STRICT_CASES, ids=[f"{t}-{f}" for t, _, f in STRICT_CASES])
def test_json_values_are_not_coerced_into_other_types(mcp, tool, arguments, field):
    result = mcp.call(tool, arguments)
    assert result.is_error, f"{tool}({arguments}) was accepted"
    assert field in error_text(result)


def test_a_prior_result_with_a_coerced_value_is_refused(mcp, ref):
    q = copy.deepcopy(as_json(ref["q"]))
    q["n_rows"] = str(q["n_rows"])
    assert "n_rows" in error_text(mcp.call("estimate_did", {"dataset_id": "reference", "focal": FOCAL, "event_date": "2026-10-14", "quality": q}))
    q["n_rows"] = float(q["n_rows"]) + 0.5
    assert "n_rows" in error_text(mcp.call("estimate_did", {"dataset_id": "reference", "focal": FOCAL, "event_date": "2026-10-14", "quality": q}))


def test_design_experiment_numbers_are_strict(mcp, ref):
    base = {"dataset_id": "reference", "candidate_testing": as_json(ref["tc"]), "post_window": window(POST), "quality": as_json(ref["q"])}
    for field, value in (("mde_pp", True), ("mde_pp", "0.4"), ("alpha", "0.05"), ("power", True), ("allocation_ratio", True), ("apply_exclusions", "yes")):
        assert field in error_text(mcp.call("design_experiment", {**base, field: value})), (field, value)


def test_honest_numbers_still_work_including_integers_for_floats(mcp):
    ints = structured(mcp.call("power_analysis", {"baseline": 0.05, "mde_pp": 1, "alpha": 0.05, "power": 0.8, "allocation_ratio": 1, "n_comparisons": 1}))
    assert ints == as_json(A.power_analysis(0.05, 1.0))
    assert structured(mcp.call("power_analysis", {"baseline": 0.05, "mde_pp": -1.0}))["mde_pp"] == -1.0


def test_the_published_schemas_describe_the_same_types(mcp):
    tools = {t.name: t for t in mcp.list_tools().tools}
    props = tools["power_analysis"].input_schema["properties"]
    assert props["mde_pp"]["type"] == "number" and props["allocation_ratio"]["type"] == "number" and props["n_comparisons"]["type"] == "integer"
    assert tools["confirm_change"].input_schema["properties"]["apply_exclusions"]["type"] == "boolean"


# ------------------------------------------------------------------ 3. the answer key through a symlink

_OPENED: list[str] = []
_WATCH = [False]


def _hook(event: str, args: tuple) -> None:
    if _WATCH[0] and event == "open":
        _OPENED.append(str(args[0]))


sys.addaudithook(_hook)


@pytest.fixture
def scratch(tmp_path, world):
    """A scratch file that stands in for the answer key. The real ground_truth.json is never referenced."""
    key = tmp_path / "ground_truth.json"
    key.write_text(json.dumps({"stand_in": "for the answer key"}))
    metrics = tmp_path / "real_metrics.csv"
    metrics.write_bytes(Path(world["metrics_file"]).read_bytes())
    return tmp_path, key, metrics


def test_a_symlink_to_the_answer_key_is_refused_without_opening_it(scratch):
    tmp, key, _ = scratch
    link = tmp / "metrics.csv"
    link.symlink_to(key)
    _OPENED.clear()
    _WATCH[0] = True
    try:
        with pytest.raises(DatasetConfigError, match="answer key"):
            DatasetRegistry([DatasetConfig("a", link)])
    finally:
        _WATCH[0] = False
    assert not [p for p in _OPENED if "ground_truth" in p.lower()]


def test_every_kind_of_indirection_to_the_answer_key_is_refused(scratch):
    tmp, key, metrics = scratch
    (tmp / "hop2").symlink_to(key)
    (tmp / "hop1.csv").symlink_to(tmp / "hop2")
    (tmp / "relative.csv").symlink_to(os.path.relpath(key, tmp))
    (tmp / "subdir").mkdir()
    (tmp / "subdir" / "up.csv").symlink_to(Path("..") / "ground_truth.json")
    os.link(key, tmp / "hardlink.csv")
    for name in ("hop1.csv", "relative.csv", "subdir/up.csv", "hardlink.csv"):
        with pytest.raises(DatasetConfigError, match="answer key"):
            DatasetRegistry([DatasetConfig("a", tmp / name)])
        with pytest.raises(DatasetConfigError, match="answer key"):
            DatasetRegistry([DatasetConfig("a", metrics, tmp / name)])  # as the events file too


def test_an_ordinary_symlink_to_real_data_still_works(scratch):
    tmp, _, metrics = scratch
    link = tmp / "metrics_link.csv"
    link.symlink_to(metrics)
    registry = DatasetRegistry([DatasetConfig("linked", link)])
    assert len(registry.load("linked").metrics) == 5888


def test_a_symlink_swapped_after_startup_cannot_redirect_the_read(scratch):
    tmp, key, metrics = scratch
    link = tmp / "metrics_link.csv"
    link.symlink_to(metrics)
    registry = DatasetRegistry([DatasetConfig("linked", link)])
    link.unlink()
    link.symlink_to(key)  # retargeted after the check
    _OPENED.clear()
    _WATCH[0] = True
    try:
        assert len(registry.load("linked").metrics) == 5888  # the checked target is what is read
    finally:
        _WATCH[0] = False
    assert not [p for p in _OPENED if "ground_truth" in p.lower()]


def test_the_command_line_and_config_file_apply_the_same_check(scratch, capsys):
    tmp, key, _ = scratch
    link = tmp / "metrics.csv"
    link.symlink_to(key)
    assert main(["--transport", "stdio", "--dataset", "a", str(link)]) == 2
    cfg = tmp / "datasets.json"
    cfg.write_text(json.dumps({"datasets": [{"id": "a", "metrics": "metrics.csv"}]}))
    assert main(["--transport", "stdio", "--config", str(cfg)]) == 2
    out, err = capsys.readouterr()
    assert out == "" and err.count("answer key") == 2


# ------------------------------------------------------------------ 4. a malformed events entry does not drop the event log


@pytest.mark.parametrize("bad", [["events.csv"], 5, True, {"path": "events.csv"}, "", ["a", "b"]])
def test_a_malformed_events_value_is_rejected_not_dropped(scratch, bad):
    tmp, _, metrics = scratch
    cfg = tmp / "datasets.json"
    cfg.write_text(json.dumps({"datasets": [{"id": "a", "metrics": metrics.name, "events": bad}]}))
    with pytest.raises(DatasetConfigError, match="events"):
        DatasetRegistry.from_sources(config_file=cfg)


def test_a_missing_or_null_events_value_means_no_event_log(scratch):
    tmp, _, metrics = scratch
    cfg = tmp / "datasets.json"
    cfg.write_text(json.dumps({"datasets": [{"id": "omitted", "metrics": metrics.name}, {"id": "null", "metrics": metrics.name, "events": None}]}))
    registry = DatasetRegistry.from_sources(config_file=cfg)
    assert registry.config("omitted").events is None and registry.config("null").events is None


@pytest.mark.parametrize("entry", [{"metrics": 5}, {"metrics": ""}, {"metrics": ["m.csv"]}, {"description": ["x"]}, {"description": 5}])
def test_other_malformed_entries_are_rejected(scratch, entry):
    tmp, _, metrics = scratch
    base = {"id": "a", "metrics": metrics.name}
    cfg = tmp / "datasets.json"
    cfg.write_text(json.dumps({"datasets": [{**base, **entry}]}))
    with pytest.raises(DatasetConfigError):
        DatasetRegistry.from_sources(config_file=cfg)


def test_unknown_top_level_keys_in_the_config_file_are_rejected(scratch):
    tmp, _, metrics = scratch
    cfg = tmp / "datasets.json"
    cfg.write_text(json.dumps({"datasets": [{"id": "a", "metrics": metrics.name}], "event": "events.csv"}))
    with pytest.raises(DatasetConfigError, match="unknown"):
        DatasetRegistry.from_sources(config_file=cfg)


def test_a_malformed_events_value_exits_2_on_the_command_line(scratch, capsys):
    tmp, _, metrics = scratch
    cfg = tmp / "datasets.json"
    cfg.write_text(json.dumps({"datasets": [{"id": "a", "metrics": metrics.name, "events": ["events.csv"]}]}))
    assert main(["--transport", "stdio", "--config", str(cfg)]) == 2
    out, err = capsys.readouterr()
    assert out == "" and "events" in err


# ====================================================================================================================
# Round 2 of the PR #7 review (commit f71024a): per-segment decomposition counts, the interval level, ISO dates
# ====================================================================================================================


@pytest.fixture(scope="module")
def swap(world):
    return pipeline(world["frames"]["swapped"], world["events"])


# ------------------------------------------------------------------ 5. a decomposition is bound to its segments, not only its totals


def test_the_swapped_variant_has_the_same_window_totals_but_a_different_segment(ref, swap):
    for window_name in ("pre", "post"):
        a, b = getattr(ref["dec"], window_name), getattr(swap["dec"], window_name)
        assert (a.sessions, a.orders, a.excluded_dates) == (b.sessions, b.orders, b.excluded_dates)
    assert ref["dec"].localization.segments == ["device=mobile, browser=safari"]
    assert swap["dec"].localization.segments == ["device=desktop, browser=chrome"]


@pytest.mark.parametrize("tool, extra", [("test_candidates", {}), ("test_event_scope", {"event": CHECKOUT})])
def test_a_stale_decomposition_with_the_same_totals_is_refused(mcp, ref, swap, tool, extra):
    base = {"dataset_id": "swapped", "pre_window": window(PRE), "post_window": window(POST), "quality": as_json(swap["q"]), **extra}
    text = error_text(mcp.call(tool, {**base, "decomposition": as_json(ref["dec"])}))
    assert "decomposition was not computed on this dataset" in text and "device=mobile" in text
    fresh = mcp.call(tool, {**base, "decomposition": as_json(swap["dec"])})
    assert not fresh.is_error
    if tool == "test_candidates":
        assert structured(fresh)["focal"]["label"] == "device=desktop, browser=chrome" and structured(fresh)["status"] == "ok"


def test_the_stale_decomposition_used_to_investigate_the_wrong_segment(world, ref, swap):
    """What the old totals-only check let through, from the direct analytics (the adapter now refuses it)."""
    frame, events = world["frames"]["swapped"], world["events"]
    wrong = A.test_candidates(frame, events, PRE, POST, decomposition=ref["dec"], quality=swap["q"])
    assert wrong.status == "no_candidates" and wrong.focal.label == "device=mobile, browser=safari"
    assert swap["tc"].status == "ok" and swap["tc"].focal.label == "device=desktop, browser=chrome"


@pytest.mark.parametrize("edit, expect", [
    (lambda d: d["partitions"][0]["segments"][0].update(orders_post=d["partitions"][0]["segments"][0]["orders_post"] + 1), "orders"),
    (lambda d: d["partitions"][0]["segments"][0].update(sessions_pre=d["partitions"][0]["segments"][0]["sessions_pre"] - 1), "sessions"),
    (lambda d: d["partitions"][1]["segments"].pop(), "missing from the result"),
    (lambda d: d["partitions"][0]["segments"][0]["labels"].update(device="toaster"), "this dataset gives 0"),
])
def test_an_edited_decomposition_segment_is_refused(mcp, ref, edit, expect):
    dec = copy.deepcopy(as_json(ref["dec"]))
    edit(dec)
    args = {"dataset_id": "reference", "pre_window": window(PRE), "post_window": window(POST), "decomposition": dec, "quality": as_json(ref["q"])}
    assert expect in error_text(mcp.call("test_candidates", args))


def test_every_partition_of_a_matching_decomposition_is_checked_and_accepted(mcp, ref):
    """All ten partitions (singles and pairs) of the reference decomposition reproduce, so it is accepted unchanged."""
    assert len(ref["dec"].partitions) == 10
    args = {"dataset_id": "reference", "pre_window": window(PRE), "post_window": window(POST), "decomposition": as_json(ref["dec"]), "quality": as_json(ref["q"])}
    assert structured(mcp.call("test_candidates", args)) == as_json(ref["tc"])


def test_a_decomposition_over_a_subset_of_dimensions_is_checked_too(mcp, world, ref):
    frame, events = world["frames"]["reference"], world["events"]
    small = A.decompose_change(frame, PRE, POST, events, quality=ref["q"], dimensions=("device", "browser"), include_pairs=False)
    args = {"dataset_id": "reference", "pre_window": window(PRE), "post_window": window(POST), "decomposition": as_json(small), "quality": as_json(ref["q"])}
    assert not mcp.call("test_candidates", args).is_error
    bad = copy.deepcopy(as_json(small))
    bad["partitions"][1]["segments"][0]["orders_pre"] += 5
    assert "orders" in error_text(mcp.call("test_candidates", {**args, "decomposition": bad}))


# ------------------------------------------------------------------ 6. the interval level is recovered from what the result stores


@pytest.mark.parametrize("level", [0.8, 0.9, 0.99])
def test_a_candidate_result_made_at_another_level_is_accepted_unchanged(mcp, world, ref, level):
    frame, events = world["frames"]["reference"], world["events"]
    tc = A.test_candidates(frame, events, PRE, POST, decomposition=ref["dec"], quality=ref["q"], level=level)
    args = {"dataset_id": "reference", "candidate_testing": as_json(tc), "post_window": window(POST), "quality": as_json(ref["q"])}
    result = mcp.call("design_experiment", args)
    assert not result.is_error, error_text(result) if result.is_error else ""
    assert structured(result) == as_json(A.design_experiment(frame, tc, POST, events=events, quality=ref["q"]))


def test_a_90_percent_result_with_no_interval_to_read_the_level_from_is_accepted(mcp, world, ref):
    """Every onset is past the data, so no step, share interval or DiD exists. The level survives only in each share set."""
    frame, events = world["frames"]["reference"], world["events"]
    tc = A.test_candidates(frame, events, PRE, POST, decomposition=ref["dec"], quality=ref["q"], level=0.9,
                           lag_days={"Homepage hero refresh": 400, CHECKOUT: 400})
    assert tc.status == "ok" and tc.timing is not None and tc.timing.status == "not_evaluable"
    assert tc.timing.total_step_pp is None and all(c.timing.step_pp is None for c in tc.candidates)
    assert {c.share_set.level for c in tc.timing.candidates.values()} == {0.9}
    args = {"dataset_id": "reference", "candidate_testing": as_json(tc), "post_window": window(POST), "quality": as_json(ref["q"])}
    result = mcp.call("design_experiment", args)
    assert not result.is_error, error_text(result) if result.is_error else ""
    assert structured(result) == as_json(A.design_experiment(frame, tc, POST, events=events, quality=ref["q"]))


def test_a_result_whose_stored_level_was_edited_is_still_refused(mcp, ref):
    tc = copy.deepcopy(as_json(ref["tc"]))
    for cand in tc["timing"]["candidates"].values():
        cand["share_set"]["level"] = 0.5
    text = error_text(mcp.call("design_experiment", {"dataset_id": "reference", "candidate_testing": tc, "post_window": window(POST), "quality": as_json(ref["q"])}))
    assert "was not computed on this dataset" in text and "level" in text


# ------------------------------------------------------------------ 7. dates are ISO strings, never timestamps

BAD_DATES = [0, "0", 86400, "86400", True, 1.5, "2026-10-14T00:00:00", "20261014", "2026-10-14 ", "2026-02-30", "2026-13-01", "10/14/2026", "", None]


@pytest.mark.parametrize("bad", BAD_DATES, ids=[repr(b) for b in BAD_DATES])
def test_a_bare_date_argument_must_be_an_iso_date_string(mcp, bad):
    args = {"dataset_id": "reference", "focal": FOCAL, "event_date": bad}
    for tool in ("estimate_did", "check_pre_trends", "run_placebo"):
        text = error_text(mcp.call(tool, args))
        assert "event_date" in text, (tool, bad, text[:200])


@pytest.mark.parametrize("bad", [0, "0", 86400, "86400", True, "2026-10-14T00:00:00", "20261014"], ids=repr)
def test_a_window_date_must_be_an_iso_date_string(mcp, bad):
    for key in ("start", "end"):
        window_bad = {"start": "2026-08-01", "end": "2026-10-13", key: bad}
        text = error_text(mcp.call("confirm_change", {"dataset_id": "reference", "pre_window": window_bad, "post_window": window(POST)}))
        assert key in text, (key, bad, text[:200])
    text = error_text(mcp.call("detect_change_point", {"dataset_id": "reference", "search_window": {"start": bad, "end": "2026-10-31"}}))
    assert "start" in text


def test_a_placebo_date_list_must_hold_iso_date_strings(mcp):
    for bad in (0, "0", 86400, "2026-09-05T00:00:00"):
        text = error_text(mcp.call("run_placebo", {"dataset_id": "reference", "focal": FOCAL, "event_date": "2026-10-14", "dates": ["2026-09-05", bad]}))
        assert "dates" in text, bad
    assert not mcp.call("run_placebo", {"dataset_id": "reference", "focal": FOCAL, "event_date": "2026-10-14", "dates": ["2026-09-05", "2026-09-25"]}).is_error


def test_a_timestamp_no_longer_becomes_the_epoch_date(mcp):
    """Before: event_date 0 and "0" ran as 1970-01-01, and a window starting at 0 spanned 20,727 days."""
    for bad in (0, "0"):
        assert mcp.call("estimate_did", {"dataset_id": "reference", "focal": FOCAL, "event_date": bad}).is_error
    r = mcp.call("confirm_change", {"dataset_id": "reference", "pre_window": {"start": 0, "end": "2026-10-13"}, "post_window": window(POST)})
    assert r.is_error


def test_dates_inside_prior_results_are_strict_too(mcp, ref):
    q = copy.deepcopy(as_json(ref["q"]))
    q["date_range"] = ["0", "0"]
    assert "date_range" in error_text(mcp.call("estimate_did", {"dataset_id": "reference", "focal": FOCAL, "event_date": "2026-10-14", "quality": q}))
    q["date_range"] = [0, 86400]
    assert "date_range" in error_text(mcp.call("estimate_did", {"dataset_id": "reference", "focal": FOCAL, "event_date": "2026-10-14", "quality": q}))
    dec = copy.deepcopy(as_json(ref["dec"]))
    dec["pre"]["start"] = 0
    args = {"dataset_id": "reference", "pre_window": window(PRE), "post_window": window(POST), "decomposition": dec}
    assert "start" in error_text(mcp.call("test_candidates", args))


def test_honest_iso_dates_still_work_and_the_schema_says_date(mcp):
    ok = structured(mcp.call("estimate_did", {"dataset_id": "reference", "focal": FOCAL, "event_date": "2026-10-14"}))
    assert ok["status"] == "ok" and ok["event_date"] == "2026-10-14"
    tools = {t.name: t for t in mcp.list_tools().tools}
    assert tools["estimate_did"].input_schema["properties"]["event_date"]["format"] == "date"
    assert tools["confirm_change"].input_schema["$defs"]["DateWindow"]["properties"]["start"]["format"] == "date"
