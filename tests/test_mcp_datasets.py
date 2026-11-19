"""The dataset registry and the server's command-line configuration (Task 7)."""
from __future__ import annotations

import hashlib
import json
from datetime import date
from pathlib import Path

import pytest
from mcp_reference import reference_files

from causelens.mcp import (
    DatasetConfig,
    DatasetConfigError,
    DatasetError,
    DatasetRegistry,
)
from causelens.mcp.__main__ import main


@pytest.fixture(scope="module")
def files(tmp_path_factory) -> tuple[Path, Path]:
    return reference_files(tmp_path_factory)


def test_a_registry_resolves_an_id_to_validated_frames(files):
    metrics, events = files
    reg = DatasetRegistry([DatasetConfig("reference", metrics, events, "the reference data")])
    data = reg.load("reference")
    assert len(data.metrics) == 5888 and data.events is not None and len(data.events) == 5
    assert reg.ids == ["reference"]


def test_events_are_optional_unless_a_tool_requires_them(files):
    metrics, _ = files
    reg = DatasetRegistry([DatasetConfig("bare", metrics)])
    assert reg.load("bare").events is None
    with pytest.raises(DatasetError, match="no events file configured"):
        reg.load("bare", events="required")


def test_an_unknown_id_names_the_configured_ones(files):
    metrics, events = files
    reg = DatasetRegistry([DatasetConfig("a", metrics, events), DatasetConfig("b", metrics)])
    with pytest.raises(DatasetError, match="unknown dataset_id 'zzz'; configured datasets: a, b"):
        reg.load("zzz")


def test_the_catalog_gives_fingerprints_and_never_paths(files):
    metrics, events = files
    info = DatasetRegistry([DatasetConfig("reference", metrics, events)]).catalog().datasets[0]
    assert info.metrics_sha256 == hashlib.sha256(metrics.read_bytes()).hexdigest()
    assert info.events_sha256 == hashlib.sha256(events.read_bytes()).hexdigest()
    assert (info.n_rows, info.n_events, info.date_range) == (5888, 5, (date(2026, 8, 1), date(2026, 10, 31)))
    assert info.metrics_file == metrics.name and str(metrics.parent) not in info.model_dump_json()
    assert set(info.dimension_values) == {"device", "browser", "channel", "customer_type"} and "safari" in info.dimension_values["browser"]


@pytest.mark.parametrize("bad", ["", " a", "a b", "a/b", "../x", "x" * 65, "-lead", "a;b"])
def test_invalid_ids_are_rejected_at_startup(files, bad):
    with pytest.raises(DatasetConfigError, match="invalid"):
        DatasetRegistry([DatasetConfig(bad, files[0])])


def test_duplicate_ids_missing_files_and_an_empty_registry_are_rejected(files, tmp_path):
    metrics, _ = files
    with pytest.raises(DatasetConfigError, match="configured twice"):
        DatasetRegistry([DatasetConfig("a", metrics), DatasetConfig("a", metrics)])
    with pytest.raises(DatasetConfigError, match="does not exist"):
        DatasetRegistry([DatasetConfig("a", tmp_path / "nope.csv")])
    with pytest.raises(DatasetConfigError, match="does not exist"):
        DatasetRegistry([DatasetConfig("a", metrics, tmp_path / "nope.csv")])
    with pytest.raises(DatasetConfigError, match="no datasets are configured"):
        DatasetRegistry([])


def test_the_answer_key_can_never_be_configured_as_a_dataset(files, tmp_path):
    metrics, _ = files
    key = tmp_path / "ground_truth.json"
    key.write_text("{}")
    for cfg in (DatasetConfig("a", key), DatasetConfig("a", metrics, key), DatasetConfig("a", tmp_path / "Ground_Truth.csv")):
        with pytest.raises(DatasetConfigError, match="answer key"):
            DatasetRegistry([cfg])


def test_a_dataset_that_fails_validation_is_reported_not_partially_loaded(tmp_path):
    bad = tmp_path / "metrics.csv"
    bad.write_text("date,device\n2026-08-01,mobile\n")
    reg = DatasetRegistry([DatasetConfig("bad", bad)])
    with pytest.raises(DatasetError, match="dataset 'bad' failed validation") as info:
        reg.load("bad")
    assert str(tmp_path) not in str(info.value) and "metrics.csv" in str(info.value)  # the file name, not the server's path


def test_a_file_that_disappears_after_startup_is_a_clear_error(files, tmp_path):
    copy = tmp_path / "m.csv"
    copy.write_bytes(files[0].read_bytes())
    reg = DatasetRegistry([DatasetConfig("gone", copy)])
    copy.unlink()
    with pytest.raises(DatasetError, match="could not be read") as info:
        reg.load("gone")
    assert str(tmp_path) not in str(info.value)


def test_a_config_file_resolves_paths_relative_to_itself(files, tmp_path):
    metrics, events = files
    (tmp_path / "m.csv").write_bytes(metrics.read_bytes())
    (tmp_path / "e.csv").write_bytes(events.read_bytes())
    cfg = tmp_path / "datasets.json"
    cfg.write_text(json.dumps({"datasets": [{"id": "ref", "metrics": "m.csv", "events": "e.csv", "description": "d"}]}))
    reg = DatasetRegistry.from_sources(config_file=cfg)
    assert reg.ids == ["ref"] and reg.config("ref").description == "d" and reg.load("ref").events is not None


@pytest.mark.parametrize(
    "content, match",
    [("not json", "not valid JSON"), ("[]", "must be"), ('{"datasets": []}', "must be"), ('{"datasets": [{"id": "a"}]}', "needs a 'metrics' file name"),
     ('{"datasets": [{"id": "a", "metrics": "m.csv", "bogus": 1}]}', "unknown keys")],
)
def test_a_malformed_config_file_is_rejected(tmp_path, content, match):
    cfg = tmp_path / "c.json"
    cfg.write_text(content)
    with pytest.raises(DatasetConfigError, match=match):
        DatasetRegistry.from_sources(config_file=cfg)


def test_command_line_specs_need_two_or_three_values(files):
    with pytest.raises(DatasetConfigError, match="ID METRICS_CSV"):
        DatasetRegistry.from_sources([["only-an-id"]])


def test_the_cli_reports_configuration_errors_on_stderr_and_exits_2(files, capsys):
    assert main(["--dataset", "a", "/no/such/file.csv"]) == 2
    out, err = capsys.readouterr()
    assert out == "" and "does not exist" in err
    assert main([]) == 2
    assert capsys.readouterr().out == ""
