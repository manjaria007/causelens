"""Run full investigations on simulated seeds and grade them (Requirements 10.1 to 10.3).

For each seed the simulator's metrics and events are written to a private temporary directory and served over MCP (in process,
through the official client). The investigation sees nothing else. The answer key stays in this module and is used after the
investigation returns. For the reference seed the key is ``data/ground_truth.json`` itself, and it is checked to be the key the
simulator produces for that seed, so a stale or edited file cannot be graded against silently.
"""
from __future__ import annotations

import asyncio
import json
import tempfile
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from causelens.agents import McpToolClient, investigate
from causelens.agents.state import InvestigationRequest, Window
from causelens.eval.grading import (
    CRITERIA,
    CRITERION_TEXT,
    Criterion,
    Findings,
    Grade,
    extract,
    grade,
)
from causelens.eval.stats import wilson_interval
from causelens.mcp import DatasetConfig, DatasetRegistry, create_server
from causelens.sim.generate import generate

REFERENCE_SEED = 42
MIN_SEEDS = 20
QUESTION = "Why did checkout conversion fall?"
REPO_ROOT = Path(__file__).resolve().parents[2]
REFERENCE_TRUTH = REPO_ROOT / "data" / "ground_truth.json"

# The windows are the ones the planted release defines: everything before it, and from it to the end of the data.
PRE = Window(start="2026-08-01", end="2026-10-13")
POST = Window(start="2026-10-14", end="2026-10-31")


class EvalError(RuntimeError):
    """The evaluation cannot be run or its answer key cannot be trusted."""


class SeedOutcome(BaseModel):
    model_config = ConfigDict(frozen=True)

    seed: int
    passed: bool
    criteria: list[Criterion]
    findings: Findings
    wrong_event_supported: list[str]
    has_estimate: bool
    n_calls: int


class CriterionRate(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    description: str
    passes: int
    n: int
    rate: float
    wilson_lower: float
    wilson_upper: float
    failed_seeds: list[int]


class EvalReport(BaseModel):
    model_config = ConfigDict(frozen=True)

    seeds: list[int]
    reference_seed: int | None
    reference_passed: bool | None = Field(description="Whether the reference seed passed every criterion; None if it was not run.")
    below_minimum: bool = Field(description=f"True when fewer than {MIN_SEEDS} seeds were run (Requirement 10.3 asks for at least {MIN_SEEDS}).")
    rates: list[CriterionRate]
    all_criteria: CriterionRate
    coverage_given_estimate: CriterionRate | None = Field(
        description="Among runs that produced an interval for the planted event: how often it covers the true effect."
    )
    wrong_event_supported_seeds: list[int]
    status_counts: dict[str, int]
    strength_counts: dict[str, int]
    outcomes: list[SeedOutcome]


def dataset_files(directory: Path, seed: int) -> tuple[Path, Path, dict[str, Any]]:
    """Write the metrics and events for ``seed`` and return their paths with the answer key (which is not written)."""
    metrics, events, truth = generate(seed)
    metrics_path, events_path = directory / "metrics_daily.csv", directory / "events.csv"
    metrics.to_csv(metrics_path, index=False)
    events.to_csv(events_path, index=False)
    return metrics_path, events_path, truth


def load_key(seed: int, generated: dict[str, Any], reference_file: Path = REFERENCE_TRUTH) -> dict[str, Any]:
    """The answer key for ``seed``: ``data/ground_truth.json`` for the reference seed, otherwise the simulator's own."""
    if seed != REFERENCE_SEED:
        return generated
    try:
        on_disk = json.loads(reference_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise EvalError(f"cannot read the answer key {reference_file}: {exc}") from None
    if on_disk != generated:
        raise EvalError(f"{reference_file} is not the key the simulator produces for seed {seed}; regenerate it or restore it from git")
    return on_disk


class SeedRun:
    """The investigation result and the number of MCP calls it made."""

    __slots__ = ("n_calls", "result")

    def __init__(self, result: Any, n_calls: int):
        self.result, self.n_calls = result, n_calls


async def investigate_files(metrics: Path, events: Path) -> SeedRun:
    registry = DatasetRegistry([DatasetConfig("reference", metrics, events)])
    request = InvestigationRequest(question=QUESTION, dataset_id="reference", pre_window=PRE, post_window=POST)
    async with McpToolClient(create_server(registry)) as client:
        run = await investigate(request, client)
    return SeedRun(run.result, len(run.state.log))


def run_seed(seed: int, reference_file: Path = REFERENCE_TRUTH) -> SeedOutcome:
    """One graded investigation. The temporary directory holds the metrics and events only."""
    with tempfile.TemporaryDirectory(prefix="causelens-eval-") as tmp:
        directory = Path(tmp)
        metrics, events, generated = dataset_files(directory, seed)
        key = load_key(seed, generated, reference_file)
        run = asyncio.run(investigate_files(metrics, events))
        leaked = sorted(p.name for p in directory.iterdir() if "ground_truth" in p.name)
        if leaked:
            raise EvalError(f"the answer key must not be served; found {leaked} beside the data")
    findings = extract(run.result)
    graded: Grade = grade(findings, key)
    return SeedOutcome(
        seed=seed, passed=graded.passed, criteria=graded.criteria, findings=findings,
        wrong_event_supported=graded.wrong_event_supported, has_estimate=graded.has_estimate, n_calls=run.n_calls,
    )


def summarize(outcomes: list[SeedOutcome]) -> EvalReport:
    """Per-criterion pass counts and rates with Wilson 95% intervals, over the seeds that were run."""
    if not outcomes:
        raise EvalError("no seeds were run")
    n = len(outcomes)

    def rate(name: str, description: str, passed: list[bool], among: list[SeedOutcome] = outcomes) -> CriterionRate:
        k, total = sum(passed), len(among)
        lo, hi = wilson_interval(k, total)
        return CriterionRate(
            name=name, description=description, passes=k, n=total, rate=k / total, wilson_lower=lo, wilson_upper=hi,
            failed_seeds=[o.seed for o, ok in zip(among, passed, strict=True) if not ok],
        )

    with_estimate = [o for o in outcomes if o.has_estimate]
    coverage = (
        rate("coverage_given_estimate", "among runs with an interval for the planted event, how often it covers the true effect",
             [next(c.passed for c in o.criteria if c.name == "ci_covers_truth") for o in with_estimate], with_estimate)
        if with_estimate else None
    )

    rates = [
        rate(name, CRITERION_TEXT[name], [next(c.passed for c in o.criteria if c.name == name) for o in outcomes]) for name in CRITERIA
    ]
    statuses: dict[str, int] = {}
    strengths: dict[str, int] = {}
    for o in outcomes:
        statuses[o.findings.status] = statuses.get(o.findings.status, 0) + 1
        strengths[o.findings.strength] = strengths.get(o.findings.strength, 0) + 1
    reference = next((o for o in outcomes if o.seed == REFERENCE_SEED), None)
    return EvalReport(
        seeds=[o.seed for o in outcomes], reference_seed=REFERENCE_SEED if reference else None,
        reference_passed=reference.passed if reference else None, below_minimum=n < MIN_SEEDS, rates=rates,
        all_criteria=rate("all_criteria", "passes every criterion", [o.passed for o in outcomes]), coverage_given_estimate=coverage,
        wrong_event_supported_seeds=[o.seed for o in outcomes if o.wrong_event_supported],
        status_counts=dict(sorted(statuses.items())), strength_counts=dict(sorted(strengths.items())), outcomes=outcomes,
    )


def evaluate(seeds: list[int], reference_file: Path = REFERENCE_TRUTH) -> EvalReport:
    if len(set(seeds)) != len(seeds):
        raise EvalError("seeds must be distinct")
    return summarize([run_seed(seed, reference_file) for seed in seeds])
