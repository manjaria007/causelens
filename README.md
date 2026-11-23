# CauseLens

An AI investigator for business metrics: it works out *why* a KPI moved, refuses to
overclaim, and tells you what to test next.

Built for Kiroween with spec-driven development in Kiro:

- `.kiro/steering/` – product, tech and **scientist rules** every agent follows
- `.kiro/specs/metric-investigation/` – requirements (EARS), design, task plan

## Project status

Implemented and merged: the synthetic dataset generator (Task 1), data loading and quality checks, change
confirmation and change point, localization, candidate testing with a difference-in-differences estimate, and
experiment design (Tasks 2 to 6), the read-only MCP server (Task 7), and the investigation orchestrator with its three
roles and a traced, structured result (Task 8), the ground-truth evaluation (`python -m causelens.eval --seeds 20`,
`docs/evaluation.md`, Task 9), and the investigation console (Task 10): `python -m causelens.ui --out console.html`
writes a six-step page with an evidence panel for each step (`docs/console.md`). Method and measured behaviour are in `docs/`.

All ten metric-investigation implementation tasks are merged. The reference console is exported to
[`docs/demo/index.html`](docs/demo/index.html). See [`docs/crew-handoff.md`](docs/crew-handoff.md) for Pages activation,
the separate Crew app specification, and the native-session evidence that still needs to be captured.

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
- `docs/`: method, parameters, calibration results and limits for each task
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
python -m ruff check causelens tests
```

The full suite takes several minutes, which is why it is not automated. A run that collects no tests (pytest exit
code 5) is not a passing result. Do not mark a task complete or begin the next task while required checks fail.
Commit and push each completed task separately, and record actual Kiro work in `KIRO_NOTES.md`.

**Automation is Ruff only.** `.kiro/hooks/ruff-on-stop.json` runs `ruff check causelens tests` when the agent stops
(a `Stop` trigger, which works in Kiro Web) and appends a record to `.pytest_cache/stop-hook-runs.log`. It does not run
tests. File-save hooks do not run in Kiro Web, and the earlier hook that ran pytest on every save was removed. **No
automatic execution of any hook has been observed in Kiro**, so the hook is configured but its activation is
unverified. Running its command by hand is not evidence that Kiro fires it; the results recorded in `KIRO_NOTES.md`
come from running the commands above directly.

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
