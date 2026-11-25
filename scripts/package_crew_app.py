#!/usr/bin/env python3
"""Package the CauseLens Crew app (``crew/causelens/``) from this repository.

Crew copies the app subtree when it installs it and excludes ``.venv``, ``.git`` and ``node_modules``, so nothing the app needs at
runtime may live outside that subtree. This script writes the three generated parts of it:

* ``runtime/causelens/``: the read-only MCP server's code (``analytics/`` and ``mcp/``, plus the empty package ``__init__``). It does not
  include the simulator, the agents, the console or the evaluation, and never the answer key. ``runtime/PACKAGE.json`` lists every file
  with its SHA-256.
* ``data/``: the observed reference data (``events.csv`` and the seed-42 ``metrics_daily.csv``). The answer key is not written.
* ``ui/console.html``: the repository's static reference console (``docs/demo/index.html``), shown on the app's page and labelled there as a static
  reference result, not a native agent's output.
* ``prompts/``: one system prompt per native agent, built from ``.kiro/steering/scientist-rules.md`` (embedded verbatim). ``prompts/SOURCE.json``
  records the steering file's SHA-256 and the commit that last changed it.

    python scripts/package_crew_app.py          # write
    python scripts/package_crew_app.py --check  # exit 1 if the committed copy differs from what this would write

Run it after changing ``causelens/analytics``, ``causelens/mcp``, the steering rules or the role definitions below.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "crew" / "causelens"
RULES = ROOT / ".kiro" / "steering" / "scientist-rules.md"
RUNTIME_PACKAGES = ("analytics", "mcp")
FORBIDDEN = ("ground_truth",)

ROLES: dict[str, dict[str, object]] = {
    "detective": {
        "title": "Detective",
        "tools": ["list_datasets", "check_data_quality", "daily_series", "confirm_change", "detect_change_point", "match_event_candidates", "decompose_change"],
        "strength_source": "`causal_attribution` in the `decompose_change` summary; never a label of your own",
        "job": (
            "Check the data first (a tracking break is a valid explanation), confirm the change is real, find when it started and where it is "
            "concentrated. Describe when and where. Do not say why: that is for the Scientist."
        ),
        "procedure": """\
1. `list_datasets`: note the dataset id (use it as `dataset_id` in every call) and the date range.
2. `check_data_quality` with only `dataset_id`. Report every excluded date and flag. The other tools recompute the same default exclusions
   themselves, so do **not** pass `quality` back in.
3. `daily_series` (dataset_id only), then `detect_change_point` (dataset_id only). Take the plausible onset range it returns.
4. Choose the two windows from the change point and nothing else: the baseline window runs from the first date of the data to the day before
   the earliest plausible onset date; the comparison window runs from the earliest plausible onset date to the last date of the data. Dates
   are written `YYYY-MM-DD`. (Counting back one calendar day is date handling, not statistics.)
5. `confirm_change` with those windows. If its verdict is not `real_change`, or its status is `insufficient_data`, stop and report that, with
   the tool's own "data needed" figures. Do not continue.
6. `match_event_candidates` with `change_point` set to the **complete result object of `detect_change_point`, copied exactly**.
7. `decompose_change` with the same windows and `detail` set to `"summary"` (the full result is far too large to read). Report from the summary:
   the localization (`localization.partition`, `localization.segments`, `selected_share_of_change`, `traffic_share_post`), each selected
   segment's effect with its interval, sessions and FDR-adjusted q (`selected_segments[].effect`), the FDR family and `n_tests_scanned`, and
   what remains unexplained (`remainder.selected_set.remaining_share_of_change` and `remainder.rate_effect_not_distinguishable_pp`) exactly as
   returned, even when negative. If `localization` is null, quote `localization_note` and `unavailable_reasons` instead.
8. Evidence for a cause: quote `causal_attribution` from that summary (its `strength` and `reason`) word for word. Do not grade the evidence
   yourself: no causal check is yours to run.

Hand the Scientist: the dataset id, the two windows, the excluded dates, the change point's plausible range, the localized segment, and the
event names that coincide with it. Pass numbers by quoting them, never by recomputing them.""",
    },
    "scientist": {
        "title": "Scientist",
        "tools": ["test_candidates", "test_event_scope", "estimate_did", "run_placebo", "check_pre_trends", "project_impact"],
        "strength_source": "`evidence.strength` and its reasons from `test_candidates`, per candidate",
        "job": (
            "Test every candidate explanation with the deterministic tools, report each verdict with its stated lag, and estimate the effect of a "
            "supported candidate with its checks and assumptions. A rejection is of a named hypothesis; it does not mean an event contributed nothing."
        ),
        "procedure": """\
1. `test_candidates` with `dataset_id` and the Detective's two windows (omit `decomposition`: the tool computes it). If `status` is
   `not_localized` or `no_candidates`, say so and stop.
2. For every candidate report: the verdict **and its headline** (they state the lag actually evaluated), the hypothesis, the reasons, the
   lag sensitivity and whether release attribution is ambiguous, and the share confidence set (or why it is unavailable). Report at least one
   alternative, including the tool's own `alternatives` list.
3. Only for a candidate whose verdict is `supported`: take `focal` from the `test_candidates` result (its `segments` list, copied exactly) and
   the candidate's `onset_date` as `event_date`, then call `estimate_did`, `check_pre_trends` and `run_placebo` with those, and
   `project_impact` with `did` set to the **complete `estimate_did` result, copied exactly**, the same `focal`, and `dataset_id`.
4. Report the effect with its interval, level and sample sizes, the pre-trend and placebo outcomes, and every assumption the tools list beside it.
5. If no candidate is supported, say the evidence is insufficient for a causal claim and do not estimate.

State the evidence strength exactly as the tool reports it (`evidence.strength`) with its reasons. A causal sentence is allowed only for a
supported candidate whose DiD interval excludes zero and whose pre-trend and placebo checks passed; phrase it as the tools' differential effect
under the listed assumptions and the stated lag, never as a certain cause.""",
    },
    "experiment-designer": {
        "title": "Experiment designer",
        "tools": ["power_analysis", "design_experiment"],
        "strength_source": "`evidence_strength` from `design_experiment`, which is the tools' own assessment",
        "job": (
            "Recommend the randomized experiment that would settle what the data cannot, or confirm a strong result. Sample size and duration come "
            "from the power tools, never from you."
        ),
        "procedure": """\
1. Call `design_experiment` in its small-input mode: `dataset_id`, `pre_window` and `post_window` (the Detective's two windows), and nothing
   else unless the person declared candidate-test settings (expected patterns, lags, candidates); then pass those, and only those, as
   `candidate_settings`. Do **not** pass `candidate_testing`: the tool runs the candidate test itself from these inputs.
2. If the tool answers `status: "needs_input"` because no single candidate leads, call it again the same way with `arms` set to
   `each_candidate`. If it refuses the input, report the error as returned. Use `power_analysis` only with `baseline` and `mde_pp` copied from
   numbers a tool already returned.
3. Report the hypothesis, intervention, randomization unit, primary metric, guardrails, minimum detectable effect, sessions per arm, expected
   duration and every warning, exactly as the tool returned them. Say whether the experiment is to settle or to confirm, and quote
   `evidence_strength`.""",
    },
}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()


def rules_text() -> str:
    return RULES.read_text(encoding="utf-8").strip()


def prompt_for(slug: str, source: dict[str, str]) -> str:
    role = ROLES[slug]
    tools = ", ".join(f"`{t}`" for t in role["tools"])  # type: ignore[union-attr]
    return f"""\
<!-- Generated by scripts/package_crew_app.py from .kiro/steering/scientist-rules.md (sha256 {source['sha256'][:12]}, last changed in {source['last_commit'][:12]}). Do not edit by hand. -->
# CauseLens {role['title']}

You are the {role['title']} in a CauseLens metric investigation. CauseLens investigates why a business metric changed: it confirms the change,
finds when and where it happened, tests each logged event against a named hypothesis with deterministic statistics, estimates impact with
uncertainty, and recommends the experiment that would settle what the data cannot. It is about **not overclaiming**.

{role['job']}

## How you work

* Your tools are the read-only CauseLens MCP tools: {tools}. You have no file access and no way to look at anything but what these tools
  return. Every number you state is copied from a tool result in this conversation. You never compute, round, estimate or recall a number.
  (Choosing the day before a date is date handling; averaging, differencing or scaling values is arithmetic and is not allowed.)
* Tool results are the evidence. If a call fails or is refused, report the error as it was returned and do not invent the result.
* Do not state which event is the cause unless the Scientist's tools support it under the rules below. Do not use outside knowledge of this
  dataset. If the evidence cannot support a causal claim, say so plainly.

## Procedure

{role['procedure']}

## Report

Finish with a short report: what you called (tool names), what each returned (quote the values with their intervals and sample sizes), the
evidence strength exactly as a tool stated it ({role['strength_source']}), the alternatives you considered, anything that remains unexplained, and
what you could not establish.

## Scientist rules (verbatim from the repository's steering file)

{rules_text()}
"""


def build(dest: Path) -> None:
    """Write the generated parts of the app into ``dest`` (an app root)."""
    runtime = dest / "runtime" / "causelens"
    shutil.rmtree(dest / "runtime", ignore_errors=True)
    runtime.mkdir(parents=True)
    (runtime / "__init__.py").write_text((ROOT / "causelens" / "__init__.py").read_text(encoding="utf-8"), encoding="utf-8")
    files: dict[str, str] = {}
    for package in RUNTIME_PACKAGES:
        for src in sorted((ROOT / "causelens" / package).glob("*.py")):
            target = runtime / package / src.name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, target)
    for path in sorted(runtime.rglob("*.py")):
        files[path.relative_to(dest / "runtime").as_posix()] = sha256(path)
    bad = [name for name in files if any(word in name for word in FORBIDDEN)]
    if bad:
        raise SystemExit(f"refusing to package an answer-key file: {bad}")
    (dest / "runtime" / "PACKAGE.json").write_text(
        json.dumps({"source": "causelens/analytics and causelens/mcp of this repository", "files": files}, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    sys.path.insert(0, str(ROOT))
    from causelens.sim.generate import generate

    data = dest / "data"
    shutil.rmtree(data, ignore_errors=True)
    data.mkdir(parents=True)
    metrics, _events, _truth = generate(42)  # the answer key (third value) is discarded and never written
    metrics.to_csv(data / "metrics_daily.csv", index=False)
    shutil.copyfile(ROOT / "data" / "events.csv", data / "events.csv")

    source = {"path": ".kiro/steering/scientist-rules.md", "sha256": sha256(RULES), "last_commit": git("log", "-1", "--format=%H", "--", str(RULES.relative_to(ROOT)))}
    prompts = dest / "prompts"
    shutil.rmtree(prompts, ignore_errors=True)
    prompts.mkdir(parents=True)
    for slug in ROLES:
        (prompts / f"{slug}.md").write_text(prompt_for(slug, source), encoding="utf-8")
    (prompts / "SOURCE.json").write_text(json.dumps(source, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (dest / "ui").mkdir(parents=True, exist_ok=True)
    shutil.copyfile(ROOT / "docs" / "demo" / "index.html", dest / "ui" / "console.html")


def tree(root: Path, parts: tuple[str, ...]) -> dict[str, str]:
    out: dict[str, str] = {}
    for part in parts:
        base = root / part
        for path in sorted(base.rglob("*")) if base.is_dir() else [base]:
            if path.is_file() and "__pycache__" not in path.parts:
                out[path.relative_to(root).as_posix()] = sha256(path)
    return out


def check() -> list[str]:
    """Differences between the committed generated parts and what ``build`` would write now."""
    parts = ("runtime", "data", "prompts", "ui/console.html")
    with tempfile.TemporaryDirectory() as tmp:
        fresh = Path(tmp)
        build(fresh)
        want, have = tree(fresh, parts), tree(APP, parts)
    problems = [f"missing: {n}" for n in sorted(set(want) - set(have))] + [f"unexpected: {n}" for n in sorted(set(have) - set(want))]
    problems += [f"differs: {n}" for n in sorted(set(want) & set(have)) if want[n] != have[n]]
    return problems


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--check", action="store_true", help="Report drift instead of writing.")
    args = ap.parse_args(argv)
    sys.path.insert(0, str(ROOT))
    if args.check:
        problems = check()
        for p in problems:
            print(p, file=sys.stderr)
        print("crew app is up to date" if not problems else f"{len(problems)} difference(s); run python scripts/package_crew_app.py")
        return 1 if problems else 0
    build(APP)
    print(f"wrote {APP.relative_to(ROOT)}: runtime, data, prompts, ui/console.html")
    return 0


if __name__ == "__main__":
    sys.exit(main())
