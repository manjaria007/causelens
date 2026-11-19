"""The server as a process: the stdio transport keeps the protocol channel clean, and the HTTP command line works."""
from __future__ import annotations

import json
import os
import select
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest
from mcp import StdioServerParameters
from mcp_reference import ROOT, reference_files
from mcp_support import McpSession, structured

import causelens.analytics as A

POST = {"start": "2026-10-14", "end": "2026-10-31"}
PRE = {"start": "2026-08-01", "end": "2026-10-13"}


@pytest.fixture(scope="module")
def files(tmp_path_factory) -> tuple[Path, Path]:
    return reference_files(tmp_path_factory)


def command(files, *extra: str) -> list[str]:
    return [sys.executable, "-m", "causelens.mcp", "--dataset", "reference", str(files[0]), str(files[1]), *extra]


def spawn(args: list[str]) -> subprocess.Popen:
    env = {**os.environ, "PYTHONUNBUFFERED": "1"}
    return subprocess.Popen(args, cwd=ROOT, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)


def read_line(proc: subprocess.Popen, timeout: float = 30) -> str:
    ready, _, _ = select.select([proc.stdout], [], [], timeout)
    assert ready, "the server wrote nothing within the timeout"
    return proc.stdout.readline()


def send(proc: subprocess.Popen, message: dict) -> None:
    proc.stdin.write(json.dumps(message) + "\n")
    proc.stdin.flush()


# ------------------------------------------------------------------ stdio


def test_stdout_carries_only_protocol_messages_across_heavy_calls(files):
    proc = spawn(command(files, "--transport", "stdio"))
    lines: list[str] = []
    try:
        send(proc, {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "probe", "version": "0"}}})
        lines.append(read_line(proc))
        send(proc, {"jsonrpc": "2.0", "method": "notifications/initialized"})
        calls = [
            (2, "tools/list", {}),
            (3, "tools/call", {"name": "list_datasets", "arguments": {}}),
            (4, "tools/call", {"name": "check_data_quality", "arguments": {"dataset_id": "reference"}}),
            (5, "tools/call", {"name": "test_candidates", "arguments": {"dataset_id": "reference", "pre_window": PRE, "post_window": POST}}),
            (6, "tools/call", {"name": "power_analysis", "arguments": {"baseline": 5.0, "mde_pp": 1.0}}),  # an error result
        ]
        for rid, method, params in calls:
            send(proc, {"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
            lines.append(read_line(proc))
    finally:
        proc.stdin.close()
        try:
            proc.wait(timeout=20)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
    rest = proc.stdout.read()
    lines.extend(line for line in rest.splitlines() if line.strip())
    assert len(lines) >= 6
    for line in lines:
        message = json.loads(line)  # a stray print or log line would not parse
        assert message["jsonrpc"] == "2.0" and ("result" in message or "error" in message), line
    by_id = {json.loads(line).get("id"): json.loads(line) for line in lines}
    assert by_id[5]["result"]["structuredContent"]["leading"] == "Checkout v4.2"
    assert by_id[6]["result"]["isError"] is True
    assert proc.returncode == 0


def test_the_sdk_stdio_client_gets_the_same_results_as_the_direct_analytics(files):
    params = StdioServerParameters(command=sys.executable, args=command(files, "--transport", "stdio")[1:], cwd=str(ROOT))
    with McpSession(params) as session:
        assert session.client.server_info.name == "causelens"
        assert structured(session.call("power_analysis", {"baseline": 0.05, "mde_pp": 1.0})) == A.power_analysis(0.05, 1.0).model_dump(mode="json")
        quality = structured(session.call("check_data_quality", {"dataset_id": "reference"}))
        assert quality["excluded_dates"] == ["2026-09-20"]


def test_a_bad_configuration_exits_2_with_nothing_on_stdout(tmp_path):
    proc = subprocess.run(
        [sys.executable, "-m", "causelens.mcp", "--transport", "stdio", "--dataset", "x", str(tmp_path / "missing.csv")],
        cwd=ROOT, capture_output=True, text=True, timeout=60, input="", check=False,
    )
    assert proc.returncode == 2 and proc.stdout == "" and "does not exist" in proc.stderr


def test_the_answer_key_is_refused_on_the_command_line(files):
    key = ROOT / "data" / "ground_truth.json"
    proc = subprocess.run(
        [sys.executable, "-m", "causelens.mcp", "--transport", "stdio", "--dataset", "x", str(key)],
        cwd=ROOT, capture_output=True, text=True, timeout=60, input="", check=False,
    )
    assert proc.returncode == 2 and proc.stdout == "" and "answer key" in proc.stderr


# ------------------------------------------------------------------ HTTP through the command line


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_the_http_command_line_serves_the_endpoint_and_stops_cleanly(files):
    port = free_port()
    proc = spawn(command(files, "--port", str(port), "--path", "/mcp"))
    try:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            with socket.socket() as probe:
                probe.settimeout(0.2)
                if probe.connect_ex(("127.0.0.1", port)) == 0:
                    break
            assert proc.poll() is None, proc.stderr.read()
            time.sleep(0.1)
        else:
            pytest.fail("the server did not start listening")
        with McpSession(f"http://127.0.0.1:{port}/mcp") as session:
            names = {t.name for t in session.list_tools().tools}
            assert "estimate_did" in names
            started = time.perf_counter()
            did = structured(session.call("estimate_did", {"dataset_id": "reference", "focal": {"device": "mobile", "browser": "safari"}, "event_date": "2026-10-14"}))
            assert time.perf_counter() - started < 2.0 and did["status"] == "ok"
    finally:
        proc.send_signal(signal.SIGTERM)
        try:
            proc.wait(timeout=20)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
    assert "serving reference at http://127.0.0.1" in proc.stderr.read()


# ------------------------------------------------------------------ the documented configurations stay valid

DOCS = ROOT / "docs" / "mcp"


def test_the_kiro_examples_name_every_tool_and_use_real_options():
    from causelens.mcp.__main__ import build_parser
    from causelens.mcp.tools import TOOL_NAMES

    http = json.loads((DOCS / "kiro-http.json").read_text())["mcpServers"]["causelens"]
    stdio = json.loads((DOCS / "kiro-stdio.json").read_text())["mcpServers"]["causelens"]
    assert sorted(http["autoApprove"]) == sorted(stdio["autoApprove"]) == sorted(TOOL_NAMES)  # every tool is read-only
    assert http["url"].startswith("http://127.0.0.1:") and http["url"].endswith("/mcp")
    assert stdio["args"][:2] == ["-m", "causelens.mcp"]
    parsed = build_parser().parse_args(stdio["args"][2:])  # the documented flags are the real ones
    assert parsed.transport == "stdio" and parsed.dataset[0][0] == "reference"
    assert Path(stdio["env"]["PYTHONPATH"]) == Path(stdio["command"]).parents[2]


def test_the_example_dataset_config_resolves_to_the_reference_files(files, tmp_path):
    """The example config's relative paths, in a copy of the repository layout (so this does not need the generated CSV)."""
    import shutil

    from causelens.mcp import DatasetRegistry

    (tmp_path / "docs" / "mcp").mkdir(parents=True)
    (tmp_path / "data").mkdir()
    shutil.copy(DOCS / "datasets.example.json", tmp_path / "docs" / "mcp" / "datasets.example.json")
    shutil.copy(files[0], tmp_path / "data" / "metrics_daily.csv")
    shutil.copy(files[1], tmp_path / "data" / "events.csv")
    registry = DatasetRegistry.from_sources(config_file=tmp_path / "docs" / "mcp" / "datasets.example.json")
    assert registry.ids == ["reference"] and registry.load("reference").events is not None
