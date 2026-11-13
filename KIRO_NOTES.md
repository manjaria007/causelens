# Kiro development notes

Record work that actually happens in Kiro. Do not attribute preparation or code
written by other tools to Kiro.

## Preparation

- Repository setup and the synthetic data generator were prepared with other tools before the Kiro build; Kiro implementation began with Task 2.
- Codex prepared this setup branch: documentation, generated-data/cache ignores,
  removal of the src placeholder, and the pytest-on-save hook configuration.
- Existing steering, requirements, design, task checklist, simulator, events, and
  ground truth are preserved. Tasks 2 through 6 remain unimplemented.
- The hook uses the current Kiro IDE 1.x / CLI V3 schema documented at
  https://kiro.dev/docs/hooks/. Its matcher selects Python files under causelens/.
- Hook activation has not been verified in Kiro. Open the repository in Kiro,
  confirm the hook is listed, and save a causelens Python file to verify execution.
- The baseline contains no executable tests. A no-tests pytest result must not be
  recorded as a successful Task 2 run.
- Setup verification: created a project-local .venv and installed requirements.txt.
  Running python -m causelens.sim.generate produced 5,888 rows and five events;
  seed 42 matched the committed ground truth and event log. Reference file bytes
  were preserved after generation.
- Baseline .venv/bin/python -m pytest -q reported "no tests ran" and exited 5.
  No implementation task has been started or marked complete.
- Hook JSON and representative matching/nonmatching paths were checked directly.
  git diff --check passed. These checks do not establish that Kiro fired the hook.

## Sequential task workflow

Start each parent task in .kiro/specs/metric-investigation/tasks.md, complete its
subtasks, review the diff, run pytest, and fix failures before moving on. Keep
analytical code in causelens/analytics/. Commit and push each completed task
separately. Stop after Task 6 for review before starting the MCP server or agents.

For each task, record:

- Which Kiro task or prompt ran.
- How steering or specifications affected the implementation.
- Any specification issue and how it was resolved.
- Commands and actual test results.
- The commit containing the completed task.

## Review points for Kiro

- Task 2 needs explicit rolling-window, MAD, session-normality, missing-date, and
  exclusion conventions. A tracking screen reports a possible issue; incident
  coincidence alone is not proof of a tracking cause.
- Task 3 reference percentages depend on comparison windows and exclusion policy.
  Compute results from the data; do not hard-code approximate target percentages
  or require a particular date range without evidence from the method.
- Before Task 5, reconcile the candidate-scope rule with Checkout v4.2: the event
  log says scope=all, but the simulator's effect is limited to mobile Safari.
  Preserve the simulator and evaluation truth; report this specification conflict
  rather than silently changing the answer or forcing a supported verdict.
- For Task 5's date-fixed-effects DiD, design.md explicitly omits the collinear
  post main effect. Report uncertainty and checks even when results are inconvenient.
- Task 6 sample size and duration must come from deterministic functions using
  declared assumptions and observed eligible traffic.

## Task 2: data loading and validation

- Implemented in this session from `.kiro/specs/metric-investigation/tasks.md` (Task 2,
  subtasks 2.1-2.3), after reading the steering files, requirements and design.
- Code: `causelens/analytics/loaders.py`, `causelens/analytics/data_quality.py`.
  Documentation of detector parameters and edge cases: `docs/data-quality.md`.
- Specification issue: the design lists only "rolling median +/- k*MAD". On the simulator
  that alone gave 1-3 chance flags per seed in small segments, so a binomial sampling-noise
  floor and a minimum relative deviation were added and documented. A centred window was
  used so persistent changes (Checkout v4.2) are not flagged as tracking blips, which holds
  only where the baseline is complete (see the limitation in the docs).
- Review round (PR #2, review supplied by the user): fixed seven findings - property-test
  fixtures that could not guarantee their assertions (now bounded fixtures plus explicit
  statistical error rates), count overflow, CSV records with extra or missing fields, repeated
  index labels, incident links to segments with no shared traffic, parameter relationships and
  zero scales, and unreported volume-only anomalies. Each has a regression test. The
  end-of-series limitation is now exposed in the flags, report and exclusion statement.
  Raising the sessions threshold to 0.40 was a calibration against observed weekday swings
  (up to 0.30 over 100 seeds), documented as such.
- Second review round: counts are now validated exactly from the original value (no float
  rounding, complex rejected); the CSV reader is strict and rejects records spanning lines so an
  unclosed quote cannot swallow later events; incident links now state whether overlap was
  observed on the flagged day or only historical. Each has a regression test.
- Results: `.venv/bin/python -m pytest -q` passed (129 tests);
  `.venv/bin/python -m ruff check causelens tests` passed.
- Hook activation in Kiro was not verified.
