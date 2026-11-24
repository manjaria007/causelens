"""The installable Crew app under ``crew/causelens/`` (crew-app spec, tasks 2 and 3).

These tests check the files and the launcher, not a Crew session. A passing run here is not evidence that Crew installed or ran the app;
that evidence comes from the native steps recorded in ``KIRO_NOTES.md``.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from mcp import Client, StdioServerParameters

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import package_crew_app as packaging

from causelens.agents.roles import DETECTIVE, EXPERIMENT_DESIGNER, SCIENTIST
from causelens.mcp.tools import TOOL_NAMES
from causelens.sim.generate import generate

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "crew" / "causelens"
MANIFEST = json.loads((APP / "app.json").read_text())
TEXT_SUFFIXES = {".json", ".md", ".py", ".txt", ".mjs", ".ts", ".tsx", ".csv", ".html"}


def app_files() -> list[Path]:
    return [p for p in sorted(APP.rglob("*")) if p.is_file() and "node_modules" not in p.parts and "__pycache__" not in p.parts]


def agent(slug: str) -> dict:
    return json.loads((APP / "agents" / f"{slug}.json").read_text())


# --------------------------------------------------------------------------- generated parts

def test_the_generated_parts_match_the_sources():
    assert packaging.check() == []


def test_the_runtime_hashes_match_package_json():
    listed = json.loads((APP / "runtime" / "PACKAGE.json").read_text())["files"]
    actual = {p.relative_to(APP / "runtime").as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in (APP / "runtime" / "causelens").rglob("*.py")}
    assert listed == actual and len(listed) > 20


def test_the_packaged_data_is_the_observed_reference_data():
    metrics, _events, _ = generate(42)
    assert (APP / "data" / "metrics_daily.csv").read_text() == metrics.to_csv(index=False)
    assert (APP / "data" / "events.csv").read_bytes() == (ROOT / "data" / "events.csv").read_bytes()
    assert hashlib.sha256((APP / "data" / "metrics_daily.csv").read_bytes()).hexdigest() == "7c8fe7196f23a9ba5b65b48ad3de8cc458c469d72c2f1ebc997e33509649f641"


def test_the_answer_key_is_not_in_the_app():
    names = [p.relative_to(APP).as_posix() for p in app_files()]
    assert not [n for n in names if "ground_truth" in n]
    for path in app_files():
        if path.suffix in TEXT_SUFFIXES and path.name != "package-lock.json":
            text = path.read_text(errors="ignore")
            for needle in ("planted_cause", "true_effect_in_segment_pp", "must_identify_event", "data_quality_traps"):
                assert needle not in text, (path, needle)
    assert not (APP / "runtime" / "causelens" / "sim").exists()  # the simulator, whose source states the planted effect, is not shipped


def test_the_runtime_is_only_what_the_server_imports():
    shipped = {p.parent.name for p in (APP / "runtime" / "causelens").rglob("*.py") if p.parent.name != "causelens"}
    assert shipped == {"analytics", "mcp"}
    for path in (APP / "runtime" / "causelens").rglob("*.py"):
        for line in path.read_text().splitlines():
            m = re.match(r"\s*(?:from|import)\s+causelens\.(\w+)", line)
            assert not m or m.group(1) in {"analytics", "mcp"}, (path, line)


def test_the_requirements_cover_every_third_party_import_of_the_runtime():
    declared = {re.split(r"[<>=!~ ]", line)[0].lower() for line in (APP / "requirements.txt").read_text().split() if line}
    stdlib = set(sys.stdlib_module_names)
    imported: set[str] = set()
    for path in [*(APP / "runtime" / "causelens").rglob("*.py"), APP / "mcp_server.py"]:
        for line in path.read_text().splitlines():
            m = re.match(r"\s*(?:from|import)\s+([A-Za-z_]\w*)", line)
            if m and m.group(1) not in stdlib | {"causelens", "__future__"}:
                imported.add(m.group(1).lower())
    assert imported <= declared, imported - declared
    assert not {"pytest", "hypothesis", "ruff"} & declared  # runtime only


# --------------------------------------------------------------------------- manifest, agents, prompts

def test_the_manifest_has_its_required_fields_and_only_relative_contained_paths():
    for key in ("name", "version", "displayName", "description"):
        assert isinstance(MANIFEST[key], str) and MANIFEST[key].strip()
    assert MANIFEST["name"] == "causelens" and re.match(r"\d+\.\d+\.\d+", MANIFEST["version"])
    paths = [*MANIFEST["agents"], *MANIFEST["skills"], MANIFEST["iconPath"], "ui/" + MANIFEST["ui"]["entry"]]
    for rel in paths:
        assert not Path(rel).is_absolute() and ".." not in Path(rel).parts, rel
        assert (APP / rel).exists(), rel
    server = MANIFEST["mcpServers"]["causelens"]
    assert server["command"] == "python3" and server["args"] == ["mcp_server.py"] and "env" not in server  # the one relative form Crew resolves
    assert (APP / "ui" / MANIFEST["ui"]["entry"]).read_text().startswith("import ")


def test_no_committed_file_needs_a_sandbox_or_personal_path():
    pattern = re.compile(r"/projects/sandbox|/workspace/|/Users/[A-Za-z]|/home/[a-z]|C:\\\\Users")
    for path in app_files():
        if path.suffix in TEXT_SUFFIXES and path.name not in {"console.html", "package-lock.json"}:
            hit = pattern.search(path.read_text(errors="ignore"))
            assert hit is None, (path, hit and hit.group(0))
    console = (APP / "ui" / "console.html").read_text()
    assert not pattern.search(console)


def test_each_agent_has_its_role_tools_and_an_app_contained_prompt():
    expected = {"detective": DETECTIVE.tools, "scientist": SCIENTIST.tools, "experiment-designer": EXPERIMENT_DESIGNER.tools | {"test_candidates"}}
    assert set(expected) == {Path(a).stem for a in MANIFEST["agents"]}
    for slug, tools in expected.items():
        spec = agent(slug)
        names = {t.split("/", 1)[1] for t in spec["tools"]}
        assert all(t.startswith("@causelens/") for t in spec["tools"]) and names <= set(TOOL_NAMES) and names == tools - ({"list_datasets"} if False else set())
        assert spec["allowedTools"] == spec["tools"]  # every tool is read-only
        assert spec["prompt"] == f"file://{{APP_ROOT}}/prompts/{slug}.md" and (APP / "prompts" / f"{slug}.md").is_file()
        assert spec["name"] == f"causelens-{slug}"


def test_the_role_tool_sets_follow_the_python_roles_except_for_the_documented_designer_addition():
    assert {t.split("/")[1] for t in agent("detective")["tools"]} == set(DETECTIVE.tools)
    assert {t.split("/")[1] for t in agent("scientist")["tools"]} == set(SCIENTIST.tools)
    assert {t.split("/")[1] for t in agent("experiment-designer")["tools"]} == set(EXPERIMENT_DESIGNER.tools) | {"test_candidates"}
    assert set(TOOL_NAMES) == set(DETECTIVE.tools) | set(SCIENTIST.tools) | set(EXPERIMENT_DESIGNER.tools)  # all 15 are used by some agent


def test_every_prompt_embeds_the_scientist_rules_verbatim_and_records_its_source():
    rules = (ROOT / ".kiro" / "steering" / "scientist-rules.md").read_text(encoding="utf-8").strip()
    source = json.loads((APP / "prompts" / "SOURCE.json").read_text())
    assert source["sha256"] == hashlib.sha256((ROOT / ".kiro" / "steering" / "scientist-rules.md").read_bytes()).hexdigest()
    assert re.fullmatch(r"[0-9a-f]{40}", source["last_commit"]) and source["path"] == ".kiro/steering/scientist-rules.md"
    for slug in packaging.ROLES:
        text = (APP / "prompts" / f"{slug}.md").read_text()
        assert rules in text and source["sha256"][:12] in text
        for must in ("never compute", "copied from a tool result", "Scientist rules"):
            assert must.lower() in text.lower(), (slug, must)
        for tool in packaging.ROLES[slug]["tools"]:  # type: ignore[union-attr]
            assert f"`{tool}`" in text


def test_the_prompts_do_not_state_the_planted_answer_or_the_reference_windows():
    for path in (APP / "prompts").glob("*.md"):
        text = path.read_text().split("## Scientist rules")[0]  # the rules are the steering file verbatim, and its own example mentions Safari
        for planted in ("Checkout v4.2", "Homepage hero", "mobile", "safari", "2026-10", "2026-09-20", "-0.66"):
            assert planted.lower() not in text.lower(), (path.name, planted)
    skill = (APP / "skills" / "causelens-investigation" / "SKILL.md").read_text()
    assert "Checkout v4.2" not in skill and "safari" not in skill.lower()


def test_the_skill_names_the_three_agents_and_the_qualifications():
    skill = (APP / "skills" / "causelens-investigation" / "SKILL.md").read_text()
    for agent_name in ("causelens-detective", "causelens-scientist", "causelens-experiment-designer"):
        assert agent_name in skill
    for must in ("evidence strength", "lag", "alternative", "data-quality", "insufficient"):
        assert must in skill.lower() or must in skill


# --------------------------------------------------------------------------- the launcher from a copied location

@pytest.fixture(scope="module")
def copied_app(tmp_path_factory) -> Path:
    """What Crew does on install: copy the app subtree elsewhere, without .venv, .git or node_modules."""
    dest = tmp_path_factory.mktemp("installed") / "causelens"
    shutil.copytree(APP, dest, ignore=shutil.ignore_patterns(".venv", ".git", "node_modules", "__pycache__"))
    return dest


def run_over_stdio(app: Path, cwd: Path, call):
    """Start the copied launcher as a child process (no PYTHONPATH, another working directory) and speak MCP to it."""
    params = StdioServerParameters(command=sys.executable, args=["mcp_server.py"], cwd=str(app), env={"PATH": "/usr/bin:/bin"})
    del cwd

    async def go():
        async with Client(params) as client:
            return await call(client)

    return asyncio.run(go())


def test_the_copied_launcher_serves_all_fifteen_tools_over_stdio(copied_app, tmp_path):
    async def call(client):
        tools = [t.name for t in (await client.list_tools()).tools]
        catalog = await client.call_tool("list_datasets", {})
        quality = await client.call_tool("check_data_quality", {"dataset_id": "reference"})
        return tools, catalog.structured_content, quality.structured_content

    tools, catalog, quality = run_over_stdio(copied_app, tmp_path, call)
    assert sorted(tools) == sorted(TOOL_NAMES) and len(tools) == 15 and "daily_series" in tools
    (dataset,) = catalog["datasets"]
    assert dataset["id"] == "reference" and dataset["n_rows"] == 5888 and dataset["metrics_sha256"].startswith("7c8fe7196f23")
    assert quality["excluded_dates"] == ["2026-09-20"]


def test_the_copied_launcher_runs_the_packaged_code_not_the_source_checkout(copied_app):
    probe = subprocess.run(
        [sys.executable, "-c", "import runpy, sys; sys.argv=['mcp_server.py']; sys.path.insert(0, '.'); import mcp_server; import causelens; print(causelens.__file__)"],
        cwd=copied_app, check=False, capture_output=True, text=True, env={"PATH": "/usr/bin:/bin"}, timeout=120,
    )
    assert probe.returncode == 0, probe.stderr
    assert Path(probe.stdout.strip()).resolve().is_relative_to(copied_app.resolve())


def test_the_launcher_never_prints_to_stdout_except_protocol(copied_app):
    proc = subprocess.run([sys.executable, "mcp_server.py"], cwd=copied_app, input="", check=False, capture_output=True, text=True, env={"PATH": "/usr/bin:/bin"}, timeout=120)
    assert proc.stdout == ""


def test_the_copy_has_no_answer_key_for_the_server_to_serve(copied_app):
    async def call(client):
        return (await client.call_tool("list_datasets", {})).structured_content

    catalog = run_over_stdio(copied_app, copied_app, call)
    assert [d["id"] for d in catalog["datasets"]] == ["reference"]
    assert not list(copied_app.rglob("*ground_truth*"))


# --------------------------------------------------------------------------- the installed Crew release's own validators

KIROCREW = shutil.which("kirocrew")
needs_crew = pytest.mark.skipif(KIROCREW is None, reason="kirocrew is not installed on this machine")


@needs_crew
def test_crew_validates_the_agent_specs():
    out = subprocess.run([KIROCREW, "agent", "validate", str(APP / "agents")], check=False, capture_output=True, text=True, timeout=120)
    assert out.returncode == 0 and "0 problem(s)" in out.stdout + out.stderr


@needs_crew
def test_crew_loads_and_validates_the_manifest():
    python = Path(KIROCREW).resolve().parent / "python"
    if not python.exists():
        pytest.skip("cannot find Crew's interpreter next to its launcher")
    code = ("from pathlib import Path; from kiro_crew.apps.manifest import AppManifest; "
            f"m = AppManifest.from_json_file(Path({str(APP / 'app.json')!r})); print(m.name, m.validate(Path({str(APP)!r})))")
    out = subprocess.run([str(python), "-c", code], check=False, capture_output=True, text=True, timeout=120)
    assert out.returncode == 0 and out.stdout.strip().endswith("causelens []"), out.stdout + out.stderr


# --------------------------------------------------------------------------- the Crew page

def test_the_page_labels_the_static_console_and_does_not_present_it_as_agent_output():
    source = (APP / "ui" / "src" / "App.tsx").read_text()
    assert "Static reference result" in source and "not the output of a native Crew agent" in source
    assert "/apps/causelens/ui/console.html" in source and 'sandbox=""' in source
    assert "callTool" not in source and "/api/mcp/call" not in source  # no invented SDK method or route


def test_the_built_page_matches_the_source_imports():
    built = (APP / "ui" / "dist" / "index.mjs").read_text()
    assert 'from "@kirocrew/app-sdk"' in built and "ChatEmbed" in built and "Static reference result" in built
    assert "export {" in built and "default" in built
    assert MANIFEST["permissions"]["api"] == ["/api/chat"]  # the chat embed's documented grant, nothing else
