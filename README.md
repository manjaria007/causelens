# CauseLens

An AI investigator for business metrics: it works out *why* a KPI moved, refuses to
overclaim, and tells you what to test next.

Built for Kiroween with spec-driven development in Kiro:

- `.kiro/steering/` – product, tech and **scientist rules** every agent follows
- `.kiro/specs/metric-investigation/` – requirements (EARS), design, task plan

## Try it

- **Live demo:** https://manjaria007.github.io/causelens/demo/ — the full investigation of the reference store, step by step
- **Demo video:** `<YouTube link>`
- **Kiro Crew app:** `crew/causelens/` — three agents investigating live on a hosted model

## Project status

Complete. All 10 tasks of the `metric-investigation` spec and all 5 tasks of the `crew-app` spec are merged.

- Statistics engine: data quality, change confirmation and change point, localization, candidate testing with a
  difference-in-differences estimate, and experiment design (`causelens/analytics/`, method notes in `docs/`)
- MCP server with 15 read-only tools (`python -m causelens.mcp`, `docs/mcp-server.md`)
- Orchestrator with three roles and a traced result (`causelens/agents/`, `docs/agents.md`)
- Ground-truth evaluation over 20 simulated stores (`python -m causelens.eval --seeds 20`, `docs/evaluation.md`)
- Investigation console (`python -m causelens.ui --out console.html`, `docs/console.md`)
- Kiro Crew app (`crew/causelens/`); evidence from a real Crew install in `docs/crew-evidence/`

## Run it in Kiro Crew

Published copy for installation: https://github.com/manjaria007/causelens-crew-app.

With Kiro Crew installed (`kirocrew setup` and `kirocrew doctor` pass):

```bash
kirocrew app install crew/causelens
kirocrew app enable causelens
```

Or add this repository's `app-registry.json` as an app registry and install CauseLens from Apps → Discover.
Then open the CauseLens page, pick the Detective, and ask: "Why did checkout conversion fall?"
The registry points to the standalone copy; local installation above uses the development copy. See
[switching an existing install](docs/crew-standalone-install.md) before changing its source.

## Use the MCP server in Kiro

`.kiro/settings/mcp.json` starts the server with `scripts/mcp_stdio.sh`, which creates the virtual environment and
generates the reference data on first run. Open the repository in Kiro and the `causelens` server lists 15 tools.
The verified CLI connection and the separate Kiro Web attachment issue are documented in [`docs/mcp-server.md`](docs/mcp-server.md).

The requirements, design, task plan, and steering files under `.kiro/` are authoritative. Preserve that directory and
its development history. Tasks are run one parent task at a time in Kiro, each reviewed and tested before the next.

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
- `causelens/analytics/`: deterministic analytical tools (Tasks 2 to 6)
- `causelens/mcp/`: read-only MCP server over the analytics (see `docs/mcp-server.md`)
- `causelens/agents/`: investigation orchestration and traced reports
- `causelens/ui/`: self-contained investigation console
- `crew/causelens/`: installable Kiro Crew app with three native agents and its packaged MCP runtime
- `docs/`: method, parameters, calibration results and limits for each task
- `docs/crew-evidence/`: native Crew screenshots, transcripts and verification records
- `data/`: reference metadata and locally generated metrics
- `tests/`: pytest and Hypothesis tests, plus seeded calibration scripts in `tests/calibration/`

Application code belongs in the `causelens/` package. See
`data/README.md` for the actual data schemas.

## Task verification

Run checks in this order while working on a task, and run the full suite once, at the end:

1. **Targeted:** after each change, the new regression test and the tests of the module you touched, for example
   `python -m pytest -q tests/test_patterns.py`.
2. **Integration:** once a related block is stable, the tests of everything that depends on it.
3. **Full:** after the final code change, once:

```bash
python -m pytest -q
python -m ruff check causelens tests scripts crew
```

The full suite takes several minutes, which is why it is not automated. A run that collects no tests (pytest exit
code 5) is not a passing result. Do not mark a task complete or begin the next task while required checks fail.
Commit and push each completed task separately, and record actual Kiro work in `KIRO_NOTES.md`.

**Automation is Ruff only.** `.kiro/hooks/ruff-on-stop.json` runs `ruff check causelens tests` when the agent stops
and appends a record to `.pytest_cache/stop-hook-runs.log`. Automatic execution was observed after native Kiro CLI
turns on the Mac: timestamped start/end blocks record this checkout and `ruff_exit=0` in `KIRO_NOTES.md`.
The hook does not run tests; the earlier pytest-on-save hook was removed. Crew's own hook settings are separate
from this repository hook. Tests follow the targeted → integration → full-suite order above.

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

### What the analytics report on this data

Checked against the planted truth above, not read from it (`docs/candidate-testing.md` has the numbers):

- It localizes the change to mobile Safari and flags 2026-09-20 as a data-quality issue.
- **Checkout v4.2 is `supported` with `moderate` evidence, and only under the stated assumption that its effect begins
  on the event date (lag 0).** The estimate is a differential effect of about -0.68 pp (95% interval -0.77 to -0.58)
  against a planted segment effect of -0.66 pp.
- The evidence is `moderate`, not `strong`, because attribution is ambiguous: Hero at a one-day delay would begin on the
  same date as Checkout at lag 0, and the data cannot tell those apart. A randomized rollback would settle it, and
  `design_experiment` plans one (`docs/experiment-design.md`).
- The Homepage hero refresh is `rejected` as the sole immediate explanation (lag 0). That is not a finding that it
  contributed nothing.

How often this works, over 150 simulated seeds per scenario (`docs/candidate-testing.md` has the tables and intervals):

- **Recovered:** the planted cause in 83% of runs (2% wrongly rejected, all flagged as ambiguous), and a swapped true cause
  (Hero) in 92%.
- **Delayed effects are not identifiable.** If Hero acted with a one-day delay the data are identical to the reference
  case, so the tool names Checkout in 83% of those runs. It flags the attribution as ambiguous, but it is wrong.
- **Staged effects break the single-source model.** When Hero and Checkout each contribute half, 29% of runs are wrong
  without an ambiguity flag.
- **Weak effects are mostly missed.** A -12% effect is recovered in 27% of runs, and at -6% most runs stop at the
  confirm-change step (59%).
