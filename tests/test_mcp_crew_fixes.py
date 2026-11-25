"""PR #13 fixes found in the first native Crew run: the change point an agent copied into match_event_candidates lost three empty containers,
and the full decompose_change output was too large for the client to read. Driven through the real server with the official SDK client."""
from __future__ import annotations

import asyncio
import copy
import json
from pathlib import Path

import pytest
from mcp import Client
from mcp_reference import reference_files

import causelens.analytics as A
from causelens.analytics.decompose_summary import (
    CausalAttribution,
    DecompositionSummary,
)
from causelens.mcp import DatasetConfig, DatasetRegistry, create_server
from causelens.mcp.models import restore_known_change_point_omissions

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "docs" / "crew-evidence" / "slot-causelens-detective.json"
PRE, POST = ("2026-08-01", "2026-10-13"), ("2026-10-14", "2026-10-31")
WINDOWS = {"pre_window": {"start": PRE[0], "end": PRE[1]}, "post_window": {"start": POST[0], "end": POST[1]}}
CLIENT_LIMIT = 30_000  # kiro-cli's tool-output limit observed in the native run


@pytest.fixture(scope="module")
def files(tmp_path_factory):
    return reference_files(tmp_path_factory)


def calls(files, *requests):
    """Make each (tool, arguments) call in one session; return (structured_content | None, text, is_error) per call."""
    server = create_server(DatasetRegistry([DatasetConfig("reference", files[0], files[1])]))

    async def go():
        out = []
        async with Client(server) as client:
            for name, arguments in requests:
                r = await client.call_tool(name, arguments)
                text = " ".join(getattr(b, "text", "") for b in r.content)
                out.append((r.structured_content, text, r.is_error))
        return out

    return asyncio.run(go())


def recorded_change_point() -> dict:
    """The change point exactly as the native Detective sent it to match_event_candidates (from the saved transcript)."""
    slot = json.loads(EVIDENCE.read_text())
    sent = [m for m in slot["messages"] if m.get("role") == "tool" and '"change_point"' in (m.get("meta", {}).get("input") or "")]
    assert len(sent) == 2, "the failed transcript holds both match_event_candidates attempts"
    arguments = json.loads(sent[0]["meta"]["input"])
    assert "refused" not in json.dumps(arguments)
    assert "MCP tool error" in sent[0]["meta"]["output"] and "segment: Field required" in sent[0]["meta"]["output"]
    return arguments["change_point"]


# --------------------------------------------------------------------------- 1. the three known omissions

def test_the_recorded_input_omits_exactly_the_three_known_fields(files):
    sent = recorded_change_point()
    (full, _, _), = calls(files, ("detect_change_point", {"dataset_id": "reference"}))
    assert "segment" not in sent and "missing_dates" not in sent["exclusions"] and "truncated_baseline_dates" not in sent["exclusions"]
    assert full["segment"] == {} and full["exclusions"]["missing_dates"] == [] and full["exclusions"]["truncated_baseline_dates"] == []
    restored = restore_known_change_point_omissions(sent)
    for key in ("segment",):
        assert restored[key] == full[key]
    for key in ("missing_dates", "truncated_baseline_dates"):
        assert restored["exclusions"][key] == full["exclusions"][key]


def test_the_recorded_input_is_now_accepted_and_matches_the_complete_change_point(files):
    sent = recorded_change_point()
    (full, _, _), = calls(files, ("detect_change_point", {"dataset_id": "reference"}))
    (from_recorded, text, err), (from_full, _, err2) = calls(
        files, ("match_event_candidates", {"dataset_id": "reference", "change_point": sent}),
        ("match_event_candidates", {"dataset_id": "reference", "change_point": full}),
    )
    assert not err and not err2, text
    assert from_recorded == from_full
    assert {c["name"] for c in from_recorded["candidates"]} == {"Checkout v4.2", "Homepage hero refresh"}


@pytest.mark.parametrize("edit,fragment", [
    (lambda cp: cp.__setitem__("segment", None), "segment"),
    (lambda cp: cp.__setitem__("segment", "all"), "segment"),
    (lambda cp: cp["exclusions"].__setitem__("missing_dates", None), "missing_dates"),
    (lambda cp: cp["exclusions"].__setitem__("missing_dates", "none"), "missing_dates"),
    (lambda cp: cp["exclusions"].__setitem__("truncated_baseline_dates", None), "truncated_baseline_dates"),
    (lambda cp: cp["exclusions"].__setitem__("truncated_baseline_dates", ["not a date"]), "truncated_baseline_dates"),
    (lambda cp: cp.pop("status"), "status"),
    (lambda cp: cp.pop("plausible_dates"), "plausible_dates"),
    (lambda cp: cp.pop("exclusions"), "exclusions"),  # the container itself is not invented
    (lambda cp: cp["exclusions"].pop("applied"), "applied"),
    (lambda cp: cp.__setitem__("segment", {"device": 3}), "segment"),
])
def test_wrong_types_nulls_and_missing_core_fields_are_still_refused(files, edit, fragment):
    cp = copy.deepcopy(recorded_change_point())
    edit(cp)
    (_, text, err), = calls(files, ("match_event_candidates", {"dataset_id": "reference", "change_point": cp}))
    assert err and fragment in text, text


def test_supplied_values_are_kept_and_nothing_else_is_restored():
    supplied = {"segment": {"device": "mobile"}, "exclusions": {"missing_dates": ["2026-09-05"], "truncated_baseline_dates": ["2026-10-30"]}, "x": 1}
    assert restore_known_change_point_omissions(copy.deepcopy(supplied)) == supplied
    assert restore_known_change_point_omissions({"segment": None}) == {"segment": None}  # explicit null kept, so strict validation refuses it
    assert restore_known_change_point_omissions({}) == {"segment": {}}  # exclusions is not invented
    assert restore_known_change_point_omissions("not an object") == "not an object"
    original = {"exclusions": {}}
    restore_known_change_point_omissions(original)
    assert original == {"exclusions": {}}  # the caller's object is not mutated


def test_other_prior_results_get_no_restoration(files):
    """Only the change point is repaired. A quality report with the same kind of omission is still refused."""
    (q, _, _), = calls(files, ("check_data_quality", {"dataset_id": "reference"}))
    cut = {k: v for k, v in q.items() if k != "missing_dates"}
    (_, text, err), = calls(files, ("confirm_change", {"dataset_id": "reference", **WINDOWS, "quality": cut}))
    assert err and "missing_dates" in text


# --------------------------------------------------------------------------- 2. decompose_change detail="summary"

@pytest.fixture(scope="module")
def both(files):
    (full, full_text, e1), (summary, summary_text, e2) = calls(
        files, ("decompose_change", {"dataset_id": "reference", **WINDOWS}),
        ("decompose_change", {"dataset_id": "reference", **WINDOWS, "detail": "summary"}),
    )
    assert not e1 and not e2
    return full, full_text, summary, summary_text


def test_full_stays_the_default_and_is_unchanged(files, both):
    full, full_text, _, _ = both
    direct = A.decompose_change(A.load_metrics(files[0]), PRE, POST, A.load_events(files[1]))
    assert full == json.loads(direct.model_dump_json())
    assert "detail" not in full and len(full["partitions"]) == 10
    assert len(full_text) > CLIENT_LIMIT  # why the summary exists


def test_the_summary_is_its_own_type_and_comfortably_below_the_client_limit(both):
    _, _, summary, summary_text = both
    DecompositionSummary.model_validate(summary)
    assert summary["detail"] == "summary"
    assert len(summary_text) < 15_000, len(summary_text)  # the text the client reads
    assert len(json.dumps(summary)) < 12_000


def test_every_summary_value_is_copied_from_the_full_result(both):
    full, _, s, _ = both
    for key in ("status", "metric", "pre", "post", "change_pp", "conversion_pre", "conversion_post", "weekday_mix_difference",
                "n_tests_scanned", "n_unreliable_tests", "localization", "localization_note", "exclusions", "assumptions", "summary"):
        assert s[key] == full[key], key
    assert s["settings"]["alpha"] == full["alpha"] and s["settings"]["fdr_family"] == full["fdr_family"] and s["settings"]["inference"] == full["inference"]
    part = next(p for p in full["partitions"] if p["name"] == full["localization"]["partition"])
    by_label = {seg["label"]: seg for seg in part["segments"]}
    assert [x["label"] for x in s["selected_segments"]] == full["localization"]["segments"] == ["device=mobile, browser=safari"]
    for x in s["selected_segments"]:
        assert x["effect"] == by_label[x["label"]] and x["labels"] == {"device": "mobile", "browser": "safari"}
        assert x["effect"]["test"]["change_pp"]["lower"] < x["effect"]["test"]["change_pp"]["upper"] and x["effect"]["sessions_post"] > 0
        assert x["effect"]["test"]["q_value"] is not None
    r = s["remainder"]
    assert r["partition"] == part["name"] and r["residual_pp"] == part["residual_pp"]
    assert r["rate_effect_not_distinguishable_pp"] == part["rate_effect_not_distinguishable_pp"]
    assert r["selected_set"] == part[r["selected_set_kind"]]
    assert r["selected_set"]["remaining_share_of_change"] < 0  # copied as is, negative on the reference data
    assert r["selected_set"]["selected_share_of_change"] == full["localization"]["selected_share_of_change"]
    assert s["partitions"] == [p["name"] for p in full["partitions"]]
    for other in s["other_top_segments"]:
        assert other["contribution_pp"] == by_label[other["label"]]["contribution_pp"]


def test_the_summary_states_no_causal_strength_of_its_own_making(both):
    _, _, s, _ = both
    assert s["causal_attribution"] == CausalAttribution().model_dump()
    assert s["causal_attribution"]["strength"] == "insufficient" and s["causal_attribution"]["checks_run"] == []
    assert "not why" in s["causal_attribution"]["reason"]


def test_a_summary_is_not_accepted_as_a_decomposition(files, both):
    _, _, s, _ = both
    (_, text, err), = calls(files, ("test_candidates", {"dataset_id": "reference", **WINDOWS, "decomposition": s}))
    assert err and "decomposition" in text.lower()


def test_without_a_localization_the_summary_carries_the_reasons(files, tmp_path):
    import sys

    sys.path.insert(0, str(ROOT / "tests"))
    from scenarios import simulate

    metrics, events = simulate(5, uniform=True)  # the effect hits everyone: no narrow localization
    metrics.to_csv(tmp_path / "m.csv", index=False)
    events.to_csv(tmp_path / "e.csv", index=False)
    (s, text, err), (full, _, _) = calls((tmp_path / "m.csv", tmp_path / "e.csv"),
                                         ("decompose_change", {"dataset_id": "reference", **WINDOWS, "detail": "summary"}),
                                         ("decompose_change", {"dataset_id": "reference", **WINDOWS}))
    assert not err and len(text) < 15_000
    assert s["localization"] is None and full["localization"] is None
    assert s["localization_note"] == full["localization_note"] and s["localization_note"]
    assert s["selected_segments"] == [] and s["remainder"] is None and s["unavailable_reasons"]


def test_summary_settings_echo_the_arguments(files):
    (s, _, err), (full, _, _) = calls(files, ("decompose_change", {"dataset_id": "reference", **WINDOWS, "detail": "summary",
                                                                   "dimensions": ["device", "browser"], "explain_threshold": 0.9}),
                                      ("decompose_change", {"dataset_id": "reference", **WINDOWS, "dimensions": ["device", "browser"],
                                                            "explain_threshold": 0.9}))
    assert not err
    assert s["settings"]["dimensions"] == ["device", "browser"] and s["settings"]["explain_threshold"] == 0.9
    assert s["localization"] == full["localization"] and s["partitions"] == [p["name"] for p in full["partitions"]]


# --------------------------------------------------------------------------- design_experiment small-input mode

def test_small_input_mode_equals_the_intact_prior_path(files):
    (tc, _, e0), = calls(files, ("test_candidates", {"dataset_id": "reference", **WINDOWS}))
    assert not e0
    (prior, t1, e1), (small, t2, e2) = calls(
        files, ("design_experiment", {"dataset_id": "reference", "candidate_testing": tc, "post_window": WINDOWS["post_window"]}),
        ("design_experiment", {"dataset_id": "reference", **WINDOWS}),
    )
    assert not e1 and not e2, (t1, t2)
    assert small == prior and small["status"] == "ok" and small["plan"]["power"]["n_control"] > 0


@pytest.mark.parametrize("settings,extra", [
    ({"lag_days": {"Homepage hero refresh": 1}}, {"arms": "each_candidate"}),
    ({"candidates": ["Checkout v4.2"], "level": 0.9, "did_cov": "cluster"}, {"mde_pp": 0.4}),
    ({"expected_patterns": {"Checkout v4.2": {"kind": "uniform", "scale": "relative"}}}, {}),
])
def test_small_input_settings_mean_what_test_candidates_means(files, settings, extra):
    (tc, _, e0), = calls(files, ("test_candidates", {"dataset_id": "reference", **WINDOWS, **settings}))
    assert not e0
    (prior, t1, e1), (small, t2, e2) = calls(
        files, ("design_experiment", {"dataset_id": "reference", "candidate_testing": tc, "post_window": WINDOWS["post_window"], **extra}),
        ("design_experiment", {"dataset_id": "reference", **WINDOWS, "candidate_settings": settings, **extra}),
    )
    assert not e1 and not e2, (t1, t2)
    assert small == prior


def test_both_modes_at_once_is_refused(files):
    (tc, _, _), = calls(files, ("test_candidates", {"dataset_id": "reference", **WINDOWS}))
    for extra in ({"pre_window": WINDOWS["pre_window"]}, {"candidate_settings": {}}, {"pre_window": WINDOWS["pre_window"], "candidate_settings": {"level": 0.9}}):
        (_, text, err), = calls(files, ("design_experiment", {"dataset_id": "reference", "candidate_testing": tc, "post_window": WINDOWS["post_window"], **extra}))
        assert err and "Ambiguous request" in text, text


def test_neither_mode_is_refused(files):
    (_, text, err), = calls(files, ("design_experiment", {"dataset_id": "reference", "post_window": WINDOWS["post_window"]}))
    assert err and "needs either candidate_testing" in text
    (_, text, err), = calls(files, ("design_experiment", {"dataset_id": "reference", "post_window": WINDOWS["post_window"], "candidate_settings": {}}))
    assert err and "needs either candidate_testing" in text


def test_an_invalid_prior_is_refused_and_never_replaced_by_a_recomputation(files):
    """The native Designer's copy lost two empty lists: still refused (no restoration here), and no design is returned in its place."""
    slot = json.loads((ROOT / "docs" / "crew-evidence" / "runs-0.1.2" / "experiment-designer-run2.json").read_text())
    sent = [json.loads(m["meta"]["input"]) for m in slot["messages"] if m.get("role") == "tool" and '"candidate_testing"' in (m["meta"].get("input") or "")]
    assert sent and all("MCP tool error" in m["meta"]["output"] for m in slot["messages"] if m.get("role") == "tool" and '"candidate_testing"' in (m["meta"].get("input") or ""))
    arguments = {k: v for k, v in sent[0].items() if k != "_meta"}
    (out, text, err), = calls(files, ("design_experiment", arguments))
    assert err and out is None and "missing_dates" in text
    (tc, _, _), = calls(files, ("test_candidates", {"dataset_id": "reference", **WINDOWS}))
    tampered = copy.deepcopy(tc)  # a stored estimate edited: the existing provenance guard recomputes it and refuses the copy
    supported = next(c for c in tampered["candidates"] if c["differential_effect"])
    supported["differential_effect"]["effect_pp"]["estimate"] -= 0.1
    (out, text, err), = calls(files, ("design_experiment", {"dataset_id": "reference", "candidate_testing": tampered, "post_window": WINDOWS["post_window"]}))
    assert err and out is None and "differential_effect" in text, text


def test_small_input_settings_are_strict(files):
    for settings, fragment in (({"level": "0.9"}, "level"), ({"lag_days": {"Checkout v4.2": True}}, "lag_days"), ({"nonsense": 1}, "nonsense"),
                               ({"sensitivity_lags": [0, 1.0]}, "sensitivity_lags")):
        (_, text, err), = calls(files, ("design_experiment", {"dataset_id": "reference", **WINDOWS, "candidate_settings": settings}))
        assert err and fragment in text, (settings, text)


def test_small_input_mode_on_data_with_no_event_log_is_refused(files):
    server = create_server(DatasetRegistry([DatasetConfig("reference", files[0], None)]))

    async def go():
        async with Client(server) as client:
            return await client.call_tool("design_experiment", {"dataset_id": "reference", **WINDOWS})

    r = asyncio.run(go())
    assert r.is_error and "no event log" in " ".join(getattr(b, "text", "") for b in r.content)
