# CauseLens

An AI investigator for business metrics: it works out *why* a KPI moved, refuses to
overclaim, and tells you what to test next.

Built for Kiroween with spec-driven development in Kiro:

- `.kiro/steering/` – product, tech and **scientist rules** every agent follows
- `.kiro/specs/metric-investigation/` – requirements (EARS), design, task plan

## Project status

The synthetic dataset generator is implemented. Analytics, MCP tools, the
investigation pipeline, evaluation, and the console are planned in
`.kiro/specs/metric-investigation/tasks.md`.

The requirements, design, task plan, and steering files under `.kiro/` are
authoritative. Preserve that directory and its development history. Use Kiro to
run Tasks 2 through 6 one parent task at a time, reviewing and testing each before
moving on.

## Setup

Use Python 3.11 or later and run these commands from the repository root:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m causelens.sim.generate --seed 42
```

Dependency installation needs package access. Dataset generation runs locally
without network or LLM calls. Analytics must also remain offline and deterministic.

The generator writes `data/metrics_daily.csv`, `data/events.csv`, and
`data/ground_truth.json`. Seed 42 is the reference scenario. Generate other seeds
in a separate directory to preserve the reference metadata:

```bash
python -m causelens.sim.generate --seed 7 --out data/generated/seed-7
```

## Repository layout

- `causelens/sim/`: synthetic data generator
- `causelens/analytics/`: planned deterministic analytical tools
- `causelens/mcp/`: planned MCP server
- `causelens/agents/`: planned investigation orchestration
- `causelens/ui/`: planned investigation console
- `data/`: reference metadata and locally generated metrics
- `tests/`: test documentation and forthcoming automated tests

Application code belongs in the `causelens/` package. See
`data/README.md` for the actual data schemas.

## Task verification

After each task, run:

```bash
python -m pytest -q
python -m ruff check causelens tests
```

Tests use pytest and Hypothesis. The baseline has no executable tests yet:
`pytest` exits with code 5 when it collects no tests. That is not a passing task.
Do not mark a task complete or begin the next task while required checks fail.
Commit and push each completed task separately, and record actual Kiro work in
`KIRO_NOTES.md`.

The current Kiro hook configuration at `.kiro/hooks/pytest-on-save.json` runs
`.venv/bin/python -m pytest -q` when Kiro saves a Python file under `causelens/`.
It uses the IDE 1.x / CLI V3 hook format. Start a Kiro session with this repository
open and confirm it appears in Agent Hooks. Older Kiro versions need a hook
created through their Agent Hooks panel.

## The reference case

A synthetic store (Aug–Oct 2026, ~40k sessions/day) with things planted for the
investigation to find:

| Date | Event | Truth |
|---|---|---|
| 2026-10-14 | Checkout v4.2 | **Real cause**: −25% conversion for mobile Safari only |
| 2026-10-13 | Homepage hero refresh | **Decoy**: touches everyone, no effect |
| 2026-09-20 | Analytics tag deploy | **Tracking outage**: 60% of desktop orders not recorded |

`data/ground_truth.json` holds the planted answer for evaluation. Investigation
analytics must infer their results from metrics and events rather than read that
answer. Follow `.kiro/steering/scientist-rules.md` for uncertainty, assumptions,
and limits on causal claims.
