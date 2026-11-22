# Kiro development notes

Record work that actually happens in Kiro. Do not attribute preparation or code
written by other tools to Kiro.

## Preparation

- Repository setup and the synthetic data generator were prepared with other tools before the Kiro build; Kiro implementation began with Task 2.
- Codex prepared this setup branch: documentation, generated-data/cache ignores,
  removal of the src placeholder, and the pytest-on-save hook configuration (since removed; see
  "Automation and verification (current)").
- Existing steering, requirements, design, task checklist, simulator, events, and
  ground truth are preserved. Tasks 2 through 6 remain unimplemented.
- The hook uses the current Kiro IDE 1.x / CLI V3 schema documented at
  https://kiro.dev/docs/hooks/. Its matcher selects Python files under causelens/.
- Kiro Web does not support File Save triggers; the existing PostFileSave hook is
  for the IDE / CLI V3. In Web, use an Agent Stop hook (JSON trigger: Stop) to run
  pytest after each agent turn. See https://kiro.dev/docs/hooks/types/.
- Hook activation has not been verified in the user's Kiro session. Confirm an
  actual automatic execution record; manually running pytest does not verify a hook.
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
separately, and stop after each parent task for review before starting the next.
The verification order is in "Automation and verification (current)" below.

For each task, record:

- Which Kiro task or prompt ran.
- How steering or specifications affected the implementation.
- Any specification issue and how it was resolved.
- Commands and actual test results.
- The commit containing the completed task.

## Automation and verification (current)

This section is the current state. Earlier entries describe what was true when they were written, including a
full-suite Stop hook and a pytest-on-save hook, and a "Hooks section on the `hook-pytest-on-stop` branch".

- **Automation is Ruff only.** `.kiro/hooks/ruff-on-stop.json` (tracked on `main`) runs
  `.venv/bin/python -m ruff check causelens tests` on the `Stop` trigger, resolves the active git checkout first (falling
  back to `/projects/sandbox/causelens`), appends a record to `.pytest_cache/stop-hook-runs.log`, and always exits 0. It
  runs no tests: the full suite takes 3 to 6 minutes. The workspace copy that a Web session loads,
  `/projects/sandbox/.kiro/hooks/ruff-on-stop.json`, is outside git and identical to it. The old full-suite
  `pytest-on-stop.json` and the pytest-on-save hook were removed. `hook-pytest-on-stop` is a superseded branch holding the
  same file. `ruff-after-task.json` (a prompt hook that runs Ruff after a spec task and checks that the full suite ran
  once) exists only as an untracked local file.
- **Activation is unverified.** No automatic execution of any hook has been observed in Kiro. File-save triggers do not
  run in Kiro Web. Running a hook's command by hand (I did, in the repository, the workspace root and a temporary worktree)
  shows the command works; it does not show that Kiro fires it. Every test result in these notes comes from running the
  commands directly.
- **Verification order.** (1) After each change, the new regression and the affected module's tests. (2) After a related
  block is stable, the dependent integration tests. (3) After the final code change, the full suite and Ruff, once
  (`.venv/bin/python -m pytest -q` and `.venv/bin/python -m ruff check causelens tests`). A wording-only change needs the
  focused tests and Ruff, not another full run, and a note says so when the full suite was not rerun. Slow statistical
  calibration (hundreds of simulated datasets) is run only when a change touches statistical behaviour, in separate
  scripts rather than in the fast suite.

## Reference result, as reported

Wording to keep consistent across the README, the docs and any write-up. Numbers and intervals are in
`docs/candidate-testing.md` and `docs/experiment-design.md`; this does not repeat them.

- Checkout v4.2 is `supported` with evidence strength **`moderate`**, **conditional on lag 0** (the effect begins on the
  event date). It is not `strong` because attribution is ambiguous: Hero at lag 1 and Checkout at lag 0 begin on the same
  date, so the data cannot separate them (a strong base strength is capped at `moderate`).
- Hero is `rejected` only as the sole immediate explanation (lag 0). At lag 1 it is `inconclusive`, not excluded, and it
  stays a lag-limited option in the experiment design.
- The estimate is a differential effect and assumes the comparison traffic was unaffected. It is not a claim about the
  sole historical cause.
- Recovery rates and limits come from 150 simulated seeds per scenario (the simulator's constants overridden in memory): planted
  cause 83%, swapped cause 92%; a one-day-delayed true cause is identical data and is named wrongly (as Checkout, flagged
  ambiguous) in 83% of runs; staged effects are wrong without an ambiguity flag in 29%; a -12% effect is recovered in 27%
  and at -6% 59% of runs stop at the confirm-change step. These limits are part of the result and should be stated with it.
- Not yet true, so not to be claimed: an end-to-end investigation, the Requirement 10.2 grading (Task 9), and any statement
  that Kiro has fired a hook.

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

## Task 3: confirm the change and locate it

- Implemented in this session from `.kiro/specs/metric-investigation/tasks.md` (Task 3,
  subtasks 3.1-3.3) after reading the steering files, requirements, design and task plan.
- Code: `causelens/analytics/series.py` (daily series, Task 2 exclusions and their disclosure),
  `confirm.py` (`confirm_change`), `changepoint.py` (`detect_change_point`,
  `match_event_candidates`). Methods, parameters, measured behaviour and limits are in
  `docs/change-analysis.md`.
- How the specification shaped the work: the scientist rules drove the result shapes (intervals and
  sample sizes on every estimate, effect size beside every p-value, assumptions listed, no causal
  wording) and the explicit insufficient-data and stop-investigation paths (Requirement 2.3).
- Specification interpretations: (1) Requirement 3.3 says events "within the plausible range"; the
  likelihood-ratio set alone excluded an event one day before the earliest plausible date, so a
  documented `lead_days` look-back (default 3) lists events shortly before the range, tagged
  `precedes_range_within_lead`, with `lead_days=0` giving the strict reading. (2) The design's
  "binary segmentation / CUSUM" is implemented as an exhaustive single-split search, equivalent for
  one change point; multiple change points are not modelled. (3) Weekday factors are estimated
  jointly with the shift rather than from raw weekday means, which a shift would bias.
- Bugs found by tests and fixed: an overflow for extremely small `alpha` (now bounded), float
  rounding noise defeating a zero-variance check, and a crash formatting the summary when a
  perfect step leaves no residual noise.
- Measured limits (see the docs): the plausible set covers the true date about 96% of the time for a
  15% step but only about 87% for an 8% step, so it is approximate.
- Results: `.venv/bin/python -m pytest -q` passed (215 tests, about 60 s);
  `.venv/bin/python -m ruff check causelens tests` passed. Task 3 is checked off in `tasks.md`
  on this branch only; PR #3 is open and unmerged, and Task 4 has not been started.
- Hook status (see the Hooks section on the `hook-pytest-on-stop` branch): the `Stop` hook is
  unverified, and no hook execution record exists for this task. These results come from running
  the commands directly, not from a hook.

### Task 3 review round (PR #3, review supplied by the user)

- Six findings, all reproduced before fixing, each with focused regression tests that were checked
  to fail on the previous commit (`f803714`): (1) constant data reported a certain change because
  split costs were computed without centring, so float residue looked like signal; (2) zero-conversion
  series crashed in weekday adjustment; (3) the pooled-rate estimate and the daily-mean t-test,
  effect size and power targeted different quantities; (4) relative intervals silently dropped
  zero-denominator bootstrap draws; (5) custom confidence levels were always labelled "95% CI";
  (6) valid maximum-count rows overflowed int64 when summed.
- Decisions: the test now targets the pooled rate (ratio-estimator variance) so estimate, interval,
  test, effect size and power agree; the relative interval is withheld (with the reason) when it is
  unbounded rather than approximated; constant series return "no change point exists"; and the
  loader rejects aggregates above 2**53 - 1 with a typed error. The last one changes the Task 2
  loader's contract (total, not just per-row), which is documented in `docs/data-quality.md`.
  `welch_significant` was renamed `test_significant` because the test is no longer a plain Welch test
  on daily means.
- Results: `.venv/bin/python -m pytest -q` passed (262 tests); `.venv/bin/python -m ruff check
  causelens tests` passed. Re-measured: null `confirm_change` false positives 5/150, a 5% drop
  confirmed 146/150.
- A `Stop` hook execution record still does not exist (see the Hooks section on the
  `hook-pytest-on-stop` branch); these results come from running the commands directly.

## Task 4: localization

- Implemented in this session from `.kiro/specs/metric-investigation/tasks.md` (Task 4, subtasks
  4.1-4.3) after reading the steering files, requirements, design and task plan, on a branch cut from
  `main` after PR #3 was merged.
- Code: `causelens/analytics/decompose.py` (`decompose_change`, `benjamini_hochberg`,
  `two_proportion_test`). Method, parameters, measured behaviour and limits: `docs/localization.md`.
- How the specification shaped the work: Requirement 4.3 and the design's "Decomposition closes" property
  drove an exact shift-share identity, with the reconciliation shown per partition. The scientist rules
  drove the output: effect sizes and intervals beside every p-value, FDR control stated, no causal
  wording, and the unexplained part shown instead of forced to 100% (an explaining set can report 108%).
- Specification interpretations (each documented): (1) rate and mix use midpoint weights, so the
  rate x mix interaction is split evenly and there is no base-window choice; the "residual" is therefore
  floating-point error only, and the genuinely unexplained part is reported separately as the rate effect
  of segments not distinguishable from noise. (2) BH runs over every segment scanned across partitions by
  default, with the within-partition q-value always reported too. (3) The two-proportion test carries a
  quasi-binomial dispersion factor by default (the plain binomial p-value is reported beside it) because
  day-level variation made the plain test optimistic. (4) "Smallest set that explains most of the change"
  is per partition; the cross-partition `localization` needs a set that carries at most 50% of traffic.
- Problems found by tests and fixed: a one-value partition (all traffic) was reported as where a pure
  traffic-mix shift was concentrated, so a traffic limit and an explicit "broad shift" outcome were added; a
  test that demanded no false discoveries in every dataset failed on one seed and was replaced by the
  false-discovery-proportion statement FDR control actually makes (mean 0.020 over 30 seeds); the first
  implementation was slow because of repeated pandas groupbys (0.375 s a call) and was rewritten with
  integer codes and `bincount` (0.11 s), with every existing test unchanged; and a test's `query()` on a
  datetime column silently did not filter, which a cross-check against `confirm_change` exposed.
- Results: `.venv/bin/python -m pytest -q` passed (352 tests); `.venv/bin/python -m ruff check causelens
  tests` passed. Task 4's own tests add about 27 s here. The whole suite took 70 s to 156 s on different
  runs because the sandbox speed varies, so it can exceed the 120 s timeout of the (unverified) `Stop`
  hook. The three headline properties (closure, row-order invariance, relabeling) also held on 1,200
  further random draws, with a worst residual of 1.2e-14 pp.
- The `Stop` hook still has no execution record (see the Hooks section on the `hook-pytest-on-stop`
  branch); these results come from running the commands directly.

### Task 4 review round (PR #4, review supplied by the user)

- Four findings, each reproduced on commit `64e0cf8` before fixing, with regression tests checked against
  the previous behaviour on the exact fixtures: (1) when the top-ranked set failed the 50% traffic limit the
  output said "spread across traffic" although a narrower combination qualified (86% of the change on 30% of
  traffic); (2) an estimated quasi-binomial dispersion was treated as known, rejecting 7.2% of true nulls at a
  nominal 5% with seven days per window (my own run of the reviewer's setting: 7.2%, 92.7% interval
  coverage); (3) a segment with 0 orders in 1 session before and 900 in 1,000 after was `small_sample=False`
  and significant at q = 0.0056 (Fisher's exact p is 0.10); (4) the zero-session padding property filtered out
  every row it proposed, so it compared a frame with itself (0 rows added in 40 frames).
- Decisions: the default test now treats days as the sampling units (ratio-estimator variance, Welch t),
  which is the same method `confirm_change` uses, replacing the estimated-dispersion adjustment. Measured
  calibration across 3-28 days and three levels of day-level noise: 3.1%-5.7% rejections at a nominal 5%
  (the old method reached 7.2%; the plain test 34%). Sparse segments (any expected cell count below 5) get no
  p-value, q-value or significance and are left out of the FDR family; Fisher's p is shown for information.
  The localization is now a search over combinations (exhaustive up to 12 groups per partition) rather than
  a check of the ranked prefix, and "broad shift" is only said after an exhaustive search. API changes in this
  unmerged PR: `dispersion` became `inference`; `two_proportion` on the test became `binomial`/`day_level`;
  `q_value` can be `None`.
- Mistakes of mine caught while fixing: an expected-value slip in a hand calculation (orders are whole
  numbers, so a nominal 2.6% became 16/600), a test that expected the wider of two equally small sets, and an
  over-tight lower bound on a calibration test where being conservative is the safe direction.
- Results: `.venv/bin/python -m pytest -q` passed (390 tests, 167 s on a slow run of the sandbox);
  `.venv/bin/python -m ruff check causelens tests` passed. A further 600 fresh random frames found no violation of closure,
  row-order invariance, relabeling, padding equivalence, the brute-force local set or the reliability rule.
- The `Stop` hook still has no execution record; these results come from running the commands directly.

### Task 4 review round 2 (PR #4, review supplied by the user)

- Three findings, each reproduced on commit `00c96ba` before fixing, and their fixtures re-run against the
  old code for the regression record: (1) a segment seen on one day per window (100 sessions / 10 orders
  before, 100 / 11 after) was reported with 14 days per window, p = q = 0 and the interval [1.0, 1.0]; (2) two
  segments with identical pooled counts but different daily variability were grouped into one block, so a
  localization that qualified on 39% of traffic was missed and the change was called broad; (3)
  `day_level_test` accepted NaN and returned NaN estimates and p-values.
- A related defect found while fixing (2), not in the review: two identical segments that each qualify alone
  were also forced together (77% of traffic), so the same wrong "broad shift" appeared. The search now selects
  by number from each group of interchangeable segments, so this no longer happens.
- Decisions: (1) days with sessions are the sampling units, a segment needs 3 in each window or the test is
  withheld (no optimistic fallback; `inference="binomial"` stays an explicit choice), and the day-level
  variance is floored at the binomial variance so a few similar days cannot give zero variance (the floor uses
  a half-observation-shrunk rate so a window with no orders still has an interval). Measured: 2.3%-5.6%
  rejections at a nominal 5% across 3-28 days and no to heavy day-level noise; with the floor treated as a
  bound (no estimation uncertainty) the test is no longer needlessly conservative on clean data. (2) Segments
  are interchangeable only with identical counts and the same significance, and a group is searched by number
  of members. (3) Every public helper rejects non-finite, fractional and impossible inputs before calculating.
- A correction to my own previous round: `docs/localization.md` had lost most of its sections (161 lines at the
  first commit, 89 at `00c96ba`). A script that replaced everything from the first `## Tests` heading also
  matched `## Tests and FDR (4.4)` and deleted the sections after it, including the ones just written for the
  previous review. The document is rebuilt in full for the final behaviour.
- Mistakes of mine caught while fixing: a hand-built fixture with the wrong direction of change, a chained
  `approx` comparison, an assertion that the ranked prefix holds one segment when it holds both tied ones (the
  documented behaviour), and a vacuous `or ... >= 1` clause. The Hypothesis frames rarely produce exact twin
  segments, so a dedicated twin-frame strategy checks partial selection against a brute-force search.
- Results: `.venv/bin/python -m pytest -q` passed (430 tests, 175 s on a slow run of the sandbox);
  `.venv/bin/python -m ruff check causelens tests` passed. A further 400 fresh random frames (half with twin
  segments) found no violation of closure, row-order invariance, relabeling, padding equivalence, the
  brute-force local set or the reliability rule.
- The `Stop` hook still has no execution record; these results come from running the commands directly.

## Task 5: candidate testing and causal estimate (branch `task-5-candidate-testing`)

- Spec first, as its own commit (`2484ea6`), with the user's five corrections: (1) evidence strength is judged from
  the interval, control validity and required checks, and ambiguity only caps at `moderate`; (2) property 8 uses
  `event_date + evaluated_lag`, and headlines state the lag; (3) `supported` needs a significant step in the
  observed direction, diagnostics and DiD checks run and passing, and sole-source rejection wins; (4) a single
  contrast is t on the residual df, a joint restriction is F, pooled variance is `SSE/(n - rank(X))`; (5) Fieller
  and share sets can be unavailable, with a reason, and coincident columns never give invented coefficients.
- Decisions: lag 0 is an explicit assumption with a one-day sensitivity per candidate; the equivalence margin is
  user-declared; `did_cov` defaults to HC1. Measured on 200 null seeds, HC1 covered 95.5% (se 0.049 vs true sd
  0.045) and date-clustering 99.5% (se 0.065), so clustering was too conservative. Onset-set exclusion only
  blocks `supported`; it never rejects.
- Found while building: HC1 variance collapsed on a single-date timing contrast, so single-date contrasts use t on
  residual df instead; identical timing columns (Hero lag 1 and Checkout lag 0) are marked unidentifiable instead of
  using a pseudo-inverse; the omnibus uniform test uses the full shared-day covariance rather than diagonal variances
  (I did not keep a test measuring the power difference).
- Mistakes of mine: I overwrote my own `tests/test_evidence.py` while believing it did not exist (rewritten, 22 tests); final suite: 604 tests passed, ruff clean;
  a placeholder test in `test_patterns.py` was left unfinished and was deleted; a too-weak "rare segment" fixture
  did not trigger the exclusion until its base rate was lowered. Background runs of the calibration were killed when the
  tool call ended, and a single 11-scenario run timed out at 29 minutes, so the 150-seed runs were split into
  halves (seeds 1-75 and 76-150) and merged.
- Results (N = 150 per scenario, full pipeline; tables in `docs/candidate-testing.md`): planted cause recovered in
  83%, swapped cause in 92%, wrong-event or rejected-truth unflagged in 2% and 0%. `hero_delayed` is identical data to
  planted, so 83% of those runs name Checkout (flagged ambiguous). Weak effects: -12% recovered in 27% (4% incorrect,
  unflagged), -6% stops at confirm in 59%. Staged: 29% incorrect unflagged. Indistinguishable same-day events:
  0 attributed. No change: 1% reach a `supported` verdict.
- Simulator, `data/`, `ground_truth.json`, steering and the Requirement 10.2 grader were not changed.

### Task 5 review round 1 (PR #5, review supplied by the user)

- Eleven findings; every one was reproduced with a failing test before fixing (19 of 22 new tests failed on `aec76b9`;
  the remaining three were controls). Fixes and measurements are in `docs/candidate-testing.md` ("Review fixes").
  Sparse segments in the uniform test falsely rejected 37 of 40 uniform datasets (the regression test now allows at most 5 of 40); a Holm p of 0
  was read as 1; exactly constant groups gave p = 0.0012 from rounding noise; a placebo date that could not be run
  passed the check; the overall impact divided by focal + comparison traffic instead of all traffic (a focal share of 0.5 instead of 0.1
  in my reproduction); `sensitivity_lags=(0,)` removed the mandatory one-day check; fractional, negative
  and boolean lags were coerced; a comma in a segment value crashed candidate testing; a zero step gave NaN bounds;
  pre-trend slopes were per usable day (0.0285 pp/day for a true 0.01 on a series with two of every three days
  missing); and the scoring ignored the ambiguity flag on a rejected true cause and counted a gradual effect as correct.
- Decision: a requested but unevaluable placebo makes the check `not_evaluable` rather than `pass`; an evaluated
  failure still fails. Lags must be integers, and the API rejects `True`, `0.9`, `-0.9`, NaN and strings instead of
  rounding them. Lags 0 and 1 are always in the sensitivity set; the argument can only add to them.
- Correction to my own earlier documentation: the reference interval quoted for the HC1 default was the clustered
  one. HC1 gives [-0.774, -0.585]; clustered gives [-0.805, -0.555].
- The calibration was rerun on 150 seeds per scenario. Verdicts did not change; the scoring did (planted incorrect 2%,
  now all flagged; weak-effect errors split into flagged and unflagged; gradual_hero no longer scores "correct").
  The runs had to be split into chunks of at most 56 seeds because 29-minute tool calls time out.
- Results: `.venv/bin/python -m pytest -q` passed (626 tests) and `.venv/bin/python -m ruff check causelens tests` passed.

### Task 5 review round 1, follow-up (verification plan)

- Added the excluded-traffic regressions to `tests/test_patterns.py` (sparse segment excluded with its reason, the
  other segments' result unchanged by excluding it, a 60-dataset null calibration, and Holm p = 0). All four fail on
  `aec76b9`'s `patterns.py` and pass now.
- Sparse-pattern null calibration (`tests/calibration/run_pattern_null.py`, 400 datasets per row) is in
  `docs/candidate-testing.md`: 2.5% to 5.0% false contradictions from 0 to 10 active days. The attribution scenarios
  were not rerun because they declare no pattern.
- The Stop hook now runs Ruff only (separate branch `hook-pytest-on-stop`, not part of this PR). Its execution by
  Kiro is still unverified.
- Verification order from now on: the new regression and the affected module after each change, dependent integration
  tests once a block is stable, and the full suite and Ruff once after the final code change.

### Task 5 review round 2 (PR #5): excluded traffic strengthened the uniform test

- The first fix (`7aebc9f`) excluded sparse segments but left the covariance and degrees of freedom on the global day
  count. Adding an excluded segment that was active every day raised the same included observations from 2 to 19
  denominator df. In a 40-day reproduction (both included groups on 3 active days per window, a `dead` segment with
  sessions and no orders) 29 of 200 datasets flipped to `contradicted`; one went from adjusted p 0.068 to 0.00035.
- My follow-up commit `b66220c` did not fix this. Its regression used included groups present every day, so it could
  not see the problem, and its calibration used a different sampling setup. I reported it as done; it was not.
- The fix: sampling units are the days on which an included segment has sessions, and
  `nu = min(d_pre, d_post, active days of the sparsest included segment) - 1`. The new regressions fail on `b66220c`
  (6 of 6) and pass now; the boundary calibration shows 0 of 400 verdicts changed by excluded traffic at 3, 4, 5, 8
  and 15 active days. A side effect: the test is conservative when a sparse segment is included (0/400 false
  contradictions at 3 and 5 active days in that setup), so its power is lower there.
- Hooks: the workspace `/projects/sandbox/.kiro/hooks/pytest-on-stop.json` was the active full-suite Stop hook (it is
  outside git). It is replaced by `ruff-on-stop.json` (Ruff only), the same file is now on this branch, and
  `pytest-on-save.json` (full suite on every save of a `causelens/*.py` file) is removed from this branch. The
  untracked prompt hook that ran the full suite after each task is now `ruff-after-task.json` (Ruff only). The
  separate `hook-pytest-on-stop` branch holds an identical `ruff-on-stop.json` and is redundant with this one. Save
  hooks do not run in Kiro Web, and no automatic execution of any hook has been verified.
- Verification order: the new regression and the affected module after each change, dependent integration tests once
  a block is stable, and the full suite and Ruff once after the final code change.

## Task 6: experiment design (branch `task-6-experiment-design`)

- Contract agreed before coding, with the user's corrections: `baseline` is a fraction and `mde_pp` a signed
  percentage-point change (`target_rate = baseline + mde_pp / 100`); power is reported as "achieved power under the
  normal approximation", not exact power; alpha, power, allocation and the resulting rates are validated.
  A user-declared business target is preferred. The nearest-zero interval bound is only a clearly labelled provisional
  planning heuristic that does not correct winner's curse, with its sign reversed for a rollback; with no usable
  estimate the result is `needs_input`. The baseline is the usable post-period focal conversion with its window and
  counts. The default is a two-arm rollback of the leading candidate (current contribution, other releases fixed, not the
  sole historical cause), with a shared-control multi-arm option and alpha divided by the number of comparisons, power
  labelled per comparison. Guardrails are labelled by what can be measured. Session randomization is an explicit
  planning assumption. Duration is the slowest-filling arm, with shared control counted once, then whole weeks.
- Integer allocation is `n_control = n`, `n_treatment = ceil(r * n)`. The search brackets by doubling and bisects, and
  relies on power not decreasing in `n`; the tests compare each result with `statsmodels` and scan 40 sizes below it.
  In `power_proportions_2indep` sample 1 is the treatment arm (`nobs1 = n_treatment`, `ratio = n_control / n_treatment`);
  a test shows the swapped mapping gives a different power, so the mapping is verified, not assumed.
- Verification worked in the agreed order: the new regression and affected module after each change (67 power tests,
  then 42 design tests), dependent integration tests after the block, and the full suite once at the end. The 42 design
  tests all passed on the first run, so I mutated six behaviours to check that the tests can fail (no sign reversal,
  alpha not divided, fastest arm instead of slowest, percentage points as fractions, baseline from all traffic, one
  tail of power). Each mutation was caught by a named test.
- Mistakes of mine caught on the way: a validation test used `alpha=0.05, power=0.01, n_comparisons=10`, which is
  valid because power only has to exceed alpha per comparison (0.005); a test read `model_fields` from an instance
  (deprecated in pydantic 2.11); a worked-values table in the docs was garbled and was rebuilt from the real outputs;
  and the first summary sentence had nested parentheses and read "2 day(s), 7 days".
- Monte Carlo (`tests/calibration/run_power_mc.py`, 2,000,000 replications per configuration, seeded, separate from the
  fast suite; a two-test smoke check is in the suite): where the approximation is flagged reliable, null rejection is
  4.99%-5.01% at alpha 0.05, the family-wise rate with several arms is 4.63% (two arms) and 4.46% (three arms), and
  simulated power is 80.02%-80.16% against 80% (89.99% against 90%). The sparse case (min expected count 1.1) is
  flagged unreliable and does behave differently: null rejection 5.21% and power 86.2% against the planned 80%.
- Not done, by design: no experiment for `no_candidates` or `not_localized` (returns `needs_input`), no factorial
  design for interacting releases, no visitor-level randomization. No MCP exposure (Task 7).
- Simulator, `data/`, `ground_truth.json`, steering and the Requirement 10.2 grader were not changed.

### Task 6 review round 1 (PR #6, review supplied by the user)

- Seven findings, each reproduced on `08a79bd` before any change (17 of the 20 new regression tests failed on it; the
  other three are controls): a focal-only dataset gave "0 usable post-period days" because the panel's `usable` flag
  needs comparison sessions; Hero, rejected at lag 0 but `inconclusive` at lag 1, was dropped from the options; targets
  of -0.600 and +0.601 pp shared one sign and the positive arm had 66.4% power; the hypothesis said a null result would
  mean no change; `power_analysis(.01, 1.8, allocation_ratio=.5)` called a design reliable with 6.31 expected treatment
  successes under the all-baseline null; `power_analysis(.58, 41.5, power=.17, allocation_ratio=.09)` returned 45/5 where
  34/4 already reaches the target; and `power_analysis(.03, .00008)` was rejected as above 10^12 although it needs about
  7.1 x 10^11 (doubling jumped from 2^39 to 2^40).
- Fixes: the panel has a `focal_usable` column and the design uses it (additive; `usable` is unchanged). A candidate is
  open if it is not rejected at some evaluated lag; a lag-limited arm is sized at that onset and says so, and the default
  stays two-arm. Each arm is sized for its own target, the neediest arm sets `n`, and the sparse regime where no common
  `n` suits every arm returns `needs_input`. The wording says "did not detect ... does not show that the rollback has no
  effect" and points to an interval or an equivalence assessment. The sparse diagnostic includes the all-baseline null
  cells. Minimality is checked exhaustively for every smaller `n` up to 1,000,000 (`minimality = "exhaustive"`) and above
  that is a bisection that assumes monotonicity (`"assumed_monotone"`, stated in the assumptions). The cap is tested
  itself before giving up.
- Three tests of mine encoded the reviewed behaviour (Hero never offered, the smallest magnitude sizing every arm,
  `each_candidate` refused for the reference case). They were updated to the corrected contract, not deleted.
- The Monte Carlo table is unchanged (identical output at 2,000,000 replications), and the reference design is still
  10,216 sessions per arm, 2 traffic days, 7 days rounded.
- Mutation checks: six mutations of the fixes (control required again, lag-limited candidates dropped, one shared target,
  null cell ignored, no exhaustive scan, cap tested after the overshoot) were each caught by a named review test.
- Verification order: the new regressions and affected modules after each change, then the full suite once at the end.

### Task 6 review round 2 (PR #6): wording of the alternative note

- `alternative_note` hard-coded "not at lag 1" and "a one-day delay", which was wrong when Checkout is declared at lag 1
  and open at lag 0 (reproduced with `lag_days={Checkout: 1}`: the note said Checkout was not rejected at lag 1 while
  the arm metadata correctly said lag 0). The note now quotes each arm's own `hypothesis_limit`, and that text names the
  actual lag ("immediately (lag 0)" or "with a 1-day delay (lag 1)"). The unsized-alternative path uses the same text.
- A wording-only change: verified with the focused review and design tests (65 passed) and Ruff; the full suite was not rerun.

## Task 7: MCP server (branch `task-7-mcp-server`)

- Built from `main` at `1977a36` as a thin, read-only adapter in `causelens/mcp/` (`datasets.py`, `models.py`, `tools.py`,
  `server.py`, `__main__.py`). `causelens/analytics/`, the simulator, `data/` and steering are unchanged (`git diff
  --stat main` on those paths is empty). Documentation: `docs/mcp-server.md`, with Kiro examples in `docs/mcp/`.
- The installed SDK is `mcp` 2.3 (`FastMCP` is now `MCPServer`, and the client API differs), while `requirements.txt` said
  `mcp>=1.20`. The code targets 2.x, so the pin is now `mcp>=2.3,<3`.
- Decisions:
  - A tool takes `dataset_id` (a registry of configured local files; no path ever comes from the caller) and returns the
    analytics result model unchanged, so output schemas are generated from the analytics models and `structuredContent`
    equals `model_dump(mode="json")` of the direct call. `power_analysis` takes no dataset.
  - Prior results (`quality`, `change_point`, `decomposition`, `candidate_testing`, `did`) are typed model arguments.
  - `project_impact` and `design_experiment` rebuild their population from `dataset_id`, `focal`, `comparison`,
    `quality`, `apply_exclusions` and refuse a prior result that does not match it (excluded dates, usable days, sessions;
    or different exclusions). `test_candidates` and `test_event_scope` refuse a decomposition for different windows. These
    checks only reject; no number is changed.
  - Insufficient-data results are ordinary results. Invalid arguments are tool errors with a clear message and no
    traceback or server path.
  - The tool set is the design's eleven tools plus `test_candidates`, `match_event_candidates` and `list_datasets`. The
    helpers that are not tools in the design are not exposed.
  - Every input schema is `additionalProperties: false`. The SDK ignores unknown arguments, so tools are registered while
    the SDK's argument-model base forbids extras and then restored. That uses an SDK internal; a test checks it does not
    leak and fails loudly if the SDK changes.
  - No caching: each call reads and validates the files (about 45 ms), so a read-only server never serves stale data.
  - `ground_truth*` can never be configured as a dataset file (rejected at startup), and nothing else reads it.
- Mistakes of mine caught by my own tests: an awkward annotation expression for `date` (replaced by an import); a
  missing-file error that embedded the server's absolute path (now file names only); the first draft of the HTTP test file
  had placeholder lines, a nonexistent attribute and a junk assertion, cleaned before its first run; the contract test
  found `check_data_quality` calls its argument `params` where every other tool says `detector_params` (kept the
  consistent name, made the test's mapping explicit).
- All 108 HTTP tests passed on their first complete run, so I mutated the adapter thirteen ways to see whether they can
  fail. Eleven were caught at once. Two were not: `apply_exclusions` ignored by `check_pre_trends`, and `comparison`
  ignored by `project_impact`, because my cases used the defaults. Both now have dedicated tests (a lenient quality report,
  `apply_exclusions=false` and an explicit comparison, each required to match the direct call and to differ from the
  default), and all thirteen mutations are caught.
- Results: `.venv/bin/python -m pytest -q` passed (915 tests, 301 s) and `.venv/bin/python -m ruff check causelens tests`
  passed. The MCP tests are 145 (HTTP with the official client, real stdio and HTTP processes, configuration). Over HTTP on the
  reference data the slowest call is 573 ms (`test_event_scope`), a cold connection plus `test_candidates` is 645 ms, and
  every tool is asserted under 2 s with loading and serialization included.
- Read-only, answer key, network: an audit hook records every file open and socket connect during all tools. `ground_truth.json`
  (in the same directory as the data) is never opened, nothing under the data directory is opened for writing, file hashes and
  modification times are unchanged, and the only connections are to loopback.
- Not verified: that Kiro itself launches or connects to the server from the example configs (they follow Kiro's documented
  format and are tested for valid JSON and real flags). Automatic execution of the Stop hook is still unverified.
- Known costs: `tools/list` is about 270 KB of schema (typed prior results), and `decompose_change` returns about 164 KB.
  There is no authentication on the HTTP transport (loopback by default). Task 8 is not started.

### Task 7 review round 1 (PR #7, review supplied by the user)

- Four groups of findings, each reproduced on `0b288c7` with a failing test first (53 of the 78 new regression tests failed;
  the rest are controls such as "a matching result is still accepted"). Numbers from my reproductions, on variants of the
  reference data built in memory (`tests/mcp_variants.py`):
  1. Binding. A DiD result from the reference data was accepted for a dataset with the same sessions and a rise, and
     `project_impact` returned -0.206 pp where that data give +0.195 pp. A stale candidate-testing result sized a rollback
     at +0.585 pp where the data give -0.523 pp (the wrong direction). A DiD for mobile/safari was accepted for an
     equal-sized mobile/chrome population (-0.189 pp where the data give +0.067 pp). The old check compared dates and session
     totals, which none of these differ in. A stale quality report is the same defect: it changes which days are excluded.
  2. Coercion. `mde_pp: true` ran as a 1.0 pp effect, `"0.5"` as 0.5, `allocation_ratio: true` as 1.0, and
     `apply_exclusions: "false"` was accepted. The SDK validates arguments leniently, and so did the typed models inside prior
     results and settings.
  3. A symlink named `metrics.csv` pointing at a file named `ground_truth.json` was accepted at startup. I used a scratch
     stand-in file; the real answer key was never opened.
  4. `"events": ["events.csv"]` started the server with no event log.
- Decisions. (1) Bind a prior result by **reproducing** it: recompute with the existing analytics from the named dataset and
  the settings the result records, and require equality (floats to a relative 1e-9). That needs no new state, tokens or
  fields on the analytics models, works for any client, and changes no value. It covers `quality`, `did`, `candidate_testing`
  and `decomposition` (`causelens/mcp/provenance.py`). A change point is not checked: it records too few settings to
  recompute faithfully, and the tool only joins its dates to the event log. The cost is a recomputation (about 0.13 s for a
  quality report, about 0.2 s for a DiD); the slowest call is now 564 ms and a cold connection plus `test_candidates` 735 ms.
  A hand-edited quality report is refused on purpose. (2) Numbers, integers and flags are strict per field, and model-typed
  arguments are re-validated as strict JSON (`strictly()` in `causelens/mcp/models.py`), so the same holds inside them. The
  published schemas are unchanged. (3) A configured file is checked by name, by every symlink hop, by its resolved target and
  by identity against a `ground_truth*` sibling (a hard link), and the resolved path is what is kept, so a link re-pointed after
  startup cannot redirect a read. A hard link from another directory cannot be detected. (4) Config-file values must be a string,
  null or omitted; unknown keys are rejected.
- Mistakes of mine. My first test file for these fixes had junk in it (a nonsense assertion, a placeholder `A.QualityHolder`,
  a convoluted `ids=`), cleaned before its first run. Two tests encoded my own earlier wording ("different exclusions", a
  `cov` edit that I expected to name `cov`); the new check recomputes with the claimed `cov`, so the numbers disagree instead,
  and the tests now say that. The "baseline: true" strictness case passed on the old code by accident (1.0 is an invalid
  baseline), so it was removed as a regression.
- Mutation checks on the fixes: 11 mutations (each binding check disabled, `quality` unchecked in one tool, no decomposition
  check, a lax `mde_pp`, lax nested models, no hard-link check, a non-string `events` dropped, the supplied path kept instead
  of the resolved one). All are caught except one, which is an equivalent mutant: dropping the check of the resolved target's
  name changes nothing, because the identity check catches the same file. The layers overlap deliberately.
- Results: `.venv/bin/python -m pytest -q` passed (992 tests, 358 s, none skipped) and `.venv/bin/python -m ruff check causelens tests`
  passed. The MCP block alone is 222 tests. A test that skipped when the generated CSV was absent now builds its own copy of the
  layout, so none depends on `data/metrics_daily.csv`.
- Order of work: failing regressions on the old code, the fixes, the focused MCP tests, the whole MCP block, then the full suite
  once. No statistical code changed (`causelens/analytics/` is identical to `main`), so no calibration was rerun.
- **Kiro tool call: still not verified.** The tool catalogue of this session does not include `causelens`, and the workspace
  settings directory `/projects/sandbox/.kiro/settings/` is read-only to me, so I could not install the config. What I did
  check: the exact command in `docs/mcp/kiro-stdio.json`, run from the workspace root as a process, initializes, lists 14
  tools, answers `power_analysis` (8,158 per arm), refuses `mde_pp: true`, and exits 0 with nothing on stderr. That is not a
  Kiro call. The steps to verify one are in `docs/mcp-server.md` ("Verify an actual Kiro tool call"). Do not start Task 8
  until that has been done and recorded here.

### Task 7 review round 2 (PR #7, review of `f71024a`)

- Three findings, each reproduced with a failing test before any fix (20 of the 39 new tests failed on `f71024a`; the rest
  are controls). One more thing the review reported was not a code defect: two timing tests failed under load and passed alone.
  1. **A decomposition was bound by its window totals only.** That was my design, and it was not enough. `mcp_variants.swapped`
     moves the planted effect from mobile/safari to desktop/chrome and keeps every window's total sessions and orders. A
     decomposition of the reference data was accepted for it, `test_candidates` investigated mobile/safari and returned
     `no_candidates`, where the right decomposition localizes to desktop/chrome and finds Checkout supported. Now every
     segment of every partition the decomposition lists is recomputed (sessions and orders, both windows, after the window's own
     excluded days), and a segment that the data have but the result lacks is reported. On the reference data all 71 segments of
     the ten partitions reproduce exactly before the check was written.
  2. **A valid 90% result was refused when it stored no interval.** `_timing_level` fell back to 95% when the total step, every
     candidate step and every DiD were absent. I reproduced it with every onset past the data (`lag_days` of 400), which leaves
     nothing estimable. Every share set records the level even then, so the level is read from there, and 80%, 90% and 99% results
     are now accepted unchanged.
  3. **Dates were still lax.** Pydantic reads `0` and `"86400"` as Unix timestamps. Checked on `f71024a`: `event_date` 0 and "0" ran
     as 1970-01-01, and a window starting at 0 returned an `ok` result that began on 1970-01-01. `"2026-10-14T00:00:00"` was
     accepted as a date. Dates in tool arguments (bare dates, `DateWindow`, the placebo list) are now `YYYY-MM-DD` strings only
     (`IsoDate`). Dates inside prior-result models were already strict JSON and are tested.
- Timing tests. The assertion "max of three calls under 2 s" is a test of the machine as much as of the code. With one core shared
  with three busy loops it failed on `test_candidates` on my machine too (the median was inside the limit). It is now the median of
  five calls under 2 s plus a 6 s hang guard, and the same for a cold connection plus `test_candidates` (median of three). The
  same contention now passes; pinned to a single core without competition the old assertion also passed, so this is about noise,
  not a speed-up. Typical latency is unchanged: the slowest call is 661 ms and a cold connection plus `test_candidates` 653 ms.
- Mistakes of mine: the first version of the new decomposition check indexed a pandas table with a mobile key that did not match a
  single-dimension index (a crash, `Error executing tool`), found by the first test run and rewritten with plain dictionaries; and a
  test expected the word "toaster" in an error that lists segment labels from the result, not the edited dimension, so it now
  checks the count the data give. Docs had a "20,727 days" figure taken from the review; I could not reproduce that number (the
  window is about 20,740 days inclusive), so the docs no longer state one.
- Mutation checks (7): per-segment check off, level fallback only, `event_date` lax, `DateWindow` lax, placebo dates lax, missing
  segments not reported, level always 95%. All caught by named tests.
- Results: `.venv/bin/python -m pytest -q` passed (1,032 tests, 377 s, none skipped) and `.venv/bin/python -m ruff check causelens tests`
  passed. The MCP block is 262 tests.
- Order of work: failing regressions on `f71024a`, the three fixes, the review file, the whole MCP block, then the full suite once.
  No analytics code changed.
- **Kiro tool call: still not verified.** Unchanged from the previous round; Task 8 stays pending until it is.

### Task 7: native MCP verification from Kiro CLI

- **Kiro tool call: verified in Kiro CLI.** The four calls were made directly from a Kiro CLI session against the `causelens` MCP
  server, not through pytest or a script. Environment: Kiro CLI 3.5.0, Python 3.14.5 (`.venv`), on my Mac.
- `list_datasets` returned one dataset, `reference`, with 5,888 rows, dates 2026-08-01 to 2026-10-31 and 5 events.
  Fingerprints: metrics `7c8fe7196f23a9ba5b65b48ad3de8cc458c469d72c2f1ebc997e33509649f641`, events
  `6611f41eb7d72f7bbc14643701c865e0492040e398f3ca018851950a0f7f38c8`.
- `power_analysis` with `baseline` 0.05 and `mde_pp` 1.0 returned 8,158 sessions per arm (16,316 in total) for target power 0.8,
  with achieved power 0.8000138678522831, `approximation_reliable` true and no warnings.
- `check_data_quality` on `reference` returned `excluded_dates` exactly `["2026-09-20"]`. The `device=desktop` flag on that date is
  a `possible_tracking_issue`: conversion 1.68% against a rolling baseline of 4.22% (60.21% below, robust z = -16.4) while
  sessions were normal (18,276 against 16,842). It is linked to the incident "Analytics tag deploy" with the same segment
  (`device=desktop`). The tool states this is a coincidence in time and scope, not proof that the incident caused the shift. Six
  more flags on the same date (chrome, edge, organic, paid, new, returning) were returned and are not reproduced here.
- `power_analysis` with the JSON boolean `mde_pp: true` was rejected, as intended, with
  `Input should be a valid number [type=float_type, input_value=True, input_type=bool]`.
- **Status.** The Kiro CLI workaround is verified. Kiro Web issue #12523 remains unresolved; this check says nothing about it.
- The Mac-specific edit to `.kiro/settings/mcp.json` is deliberately not committed. Task 8 has not been started.

## Task 8: orchestrator and agents (branch `task-8-orchestrator-agents`)

- Built from `origin/main` at `bd7cbaa` (PR #8, merged; confirmed with `git merge-base --is-ancestor` after a fetch). New code is
  `causelens/agents/` (`state.py`, `toolbox.py`, `roles.py`, `trace.py`, `claims.py`, `reasoner.py`, `report.py`, `result.py`,
  `wording.py`, `orchestrator.py`), `docs/agents.md`, and three test files plus `tests/agents_support.py`. `causelens/analytics/`,
  `causelens/mcp/`, the simulator, `data/` and steering are unchanged. `tasks.md` has 8.1-8.3 ticked. The Mac-specific
  `.kiro/settings/mcp.json` edit stayed unstaged and uncommitted.
- Read first: the three steering files, the design, requirements and tasks, `docs/mcp-server.md`, the MCP tool signatures and the
  analytics result models. Task 9 (evaluation) and Task 10 (console) are not started.
- What it does: the orchestrator calls the real MCP server through the official SDK client (`mcp.Client`) and logs every call
  (`InvestigationState.log`: role, stage, tool, arguments as sent, the server's structured result). Prior results are logged as
  `{"$prior": seq}`. Three roles are a prompt plus an allowed tool subset; the toolbox refuses any other tool before sending.
  Prompts embed `scientist-rules.md` verbatim.
- The design's LLM layer is kept as a `Reasoner` protocol. It returns sentence templates whose numbers are references into the
  log; the orchestrator validates them (no digit in the text, every reference resolves, no cause-asserting words outside a causal
  claim that cites a passed difference-in-differences result and quotes its assumptions) and drops and records what fails.
  `TemplateReasoner` is the deterministic offline implementation. **No model-backed reasoner ships and none was run.**
- Every number in the result is a `Traced` / `TracedInterval` read from the log by a `Tracer`; `verify_traces` re-reads all of
  them (more than 150 on the reference run), and a test requires that the dumped result has no int or float outside a traced
  object except log positions.
- Decisions I made that the spec did not dictate (each is in `docs/agents.md`):
  - The request carries the windows. `question` is recorded, not interpreted.
  - Evidence strength is the tool's assessment for the weakest supported candidate; with none supported, or a stopped run, it is
    `insufficient` with a fixed reason. A causal claim needs `supported`, a valid comparison, an interval excluding 0 and passing
    pre-trend and placebo checks (borderline does not count).
  - Estimates are made only for supported candidates, using the standalone `estimate_did`, `check_pre_trends`, `run_placebo` and
    `project_impact`, cross-checked against the DiD embedded in the candidate test.
  - With no single leading candidate, `design_experiment` is called with `arms="each_candidate"` instead of naming a target the
    tests did not pick.
  - Role tool subsets differ from the design table by `list_datasets`, `match_event_candidates` (Detective) and `test_candidates`
    (Scientist). `power_analysis` is allowed to the designer and not called.
- Reference result (seed 42, 12 calls): Checkout v4.2 `supported` (lag 0), Homepage hero refresh `rejected` as the sole immediate
  explanation, localized to mobile x Safari, `excluded_dates` exactly `["2026-09-20"]`, DiD interval covering the true segment
  effect, evidence `moderate` (base `strong`, capped by release ambiguity), experiment purpose `settle`. The same run over a real
  stdio process gives byte-identical JSON.
- Mistakes of mine caught by my own checks:
  - The wording check first used `caus\w*`, which flagged "causal" in my own fixed phrases, and three of my phrases used
    "because". The regex is now the verb forms and the phrases were reworded.
  - Running a weak-effect scenario showed `design_experiment` returning `needs_input` ("name one with target_candidate") when no
    candidate leads; that produced the `each_candidate` decision above.
  - Test errors: a wrong count of traced values (2, not 3), a wrong count of calls reusing the quality report (8, not 9), a digit
    check that counted placeholder names such as `{d0}`, a causal-claim test sentence with no assumption placeholder, and a
    perturbation test whose tampered DiD was refused by the server's own provenance check. The last one is now a test of that
    behavior: a candidate result altered in transit is recorded as `matches_candidate_test = False`, and the server then refuses to
    build the experiment on it, so the run stops at `design_experiment`.
- Mutation checks (13): role limit off, digits allowed, cause words allowed, claim without a DiD call, verification skipped,
  pre-trend ignored, placebo ignored, interval-includes-zero ignored, strongest instead of weakest strength, estimates for
  unsupported candidates, no stop on an unconfirmed change, exclusions not reused, cross-check off. Ten were caught at once.
  Three were not (interval including zero, weakest vs strongest, the cross-check); each now has a test, and all 13 are caught.
- Results: targeted agents tests 64 passed; the MCP block (server, CLI, datasets, review) 262 passed; then the full suite once after
  the last code change, `.venv/bin/python -m pytest -q`: 1,096 passed (126 s, none skipped); `.venv/bin/python -m ruff check
  causelens tests` passed. That is 64 new tests on top of the 1,032 in Task 7. Run on Python 3.14.5.
- Not verified: a model-backed reasoner; the orchestrator over the HTTP transport (in-process and stdio were run); running the
  orchestrator from inside Kiro. The weak-effect and no-event-log paths are tested on simulated data only. The candidate tests'
  incorrect-attribution rates are unchanged (`docs/candidate-testing.md`); no new statistics were added.
- Known limits: a causal claim is made for at most the candidates the tests support, and the reference run reports the claim with
  `moderate` strength, not `strong`, because of the ambiguity cap. Display formats fix decimals (for example 3 for pp); the stored
  values are exact. A 3-arm experiment on a weak effect can come back with a duration of years (the tool's own figure); the
  design warnings are passed through as caveats.

### Task 8 review round 1 (PR #9, review of `f62a8b4`)

- Order of work: failing regressions first (`tests/test_agents_review.py`, 59 tests; 39 failed on `f62a8b4`, the rest were guards that
  already held), the fixes, then the agents tests, then the full suite once after the last code change. Only `causelens/agents/`, its
  tests and docs changed; analytics, the MCP adapters, simulator, data and steering are untouched.
- Findings and what changed:
  1. **A rejected event could receive a causal claim.** Confirmed: a claim citing Checkout's DiD but naming Hero's event passed. A
     `causal_claim` is now bound to exactly one eligible `ClaimEligibility`: it must quote that claim's event, lag, onset, estimate,
     interval, level, sample size and every assumption (each in its required format), name the design, quote nothing about another
     candidate, and not type an event name. The spec for what it must quote is `claims.required_refs`.
  2. **The LLM layer was unfinished.** Added `choose_parameters` to the `Reasoner` protocol (a short allow-list in `parameters.py`, strict
     validation, refused whole, recorded), interpretation right after each role instead of after the whole run, and `ModelReasoner` over a
     `TextModel` protocol (prompt = `Role.system_prompt()` with the rules verbatim plus a digest of the log and the allowed claims; strict
     JSON parsing; same validation as any statement). **No provider adapter ships and no hosted model was run**; the tests use scripted
     models, so real-model behavior is unknown. I chose not to ship an adapter I could not run.
  3. **A combined statistic was credited to one segment.** The localization sentence now lists the whole selected set and says the figures
     are for the set.
  4. **Rejection wording overstated the conclusion.** Rejected candidates now carry the grounds the tool rejected on (exposure, declared
     pattern, sole-source timing test) and a matching statement; `sole_source_rejected` is reported separately.
  5. **Share confidence sets were missing.** Each tested candidate now carries its set (level, kind, pieces with open ends as `None`) or the
     tool's reason when unavailable.
  6. **Connection failures escaped.** `Toolbox` logs any ordinary exception from the client as a failed call. Reproducing it showed a second
     problem: the SDK cancels the task that opened the session when its transport fails, which arrived as a `CancelledError` that looked like
     the caller's own cancellation. `McpToolClient` now runs the session in its own task and fails calls with `SessionClosed`; a real
     cancellation still propagates (tested).
  7. **Malformed reasoner text crashed the run.** Parse and render errors are `TraceError`s and the statement is rejected and recorded.
  8. **Logged payloads were mutable.** Arguments and results are copied on the way in and out and stored read-only. The `log` list itself is
     still append-only by convention.
  9. **Request parsing coerced.** `Window` uses the server's `IsoDate`; `lag_days` is `StrictInt`; strings are strict and declared patterns
     use the server's strict-JSON validation.
  10. **Late failures gave false history.** Notes and evidence are derived from how the stages ended. A failure at the design call keeps the
     tests' evidence and the estimate and says the design failed; a failure during estimation says the estimate did not complete; no-event-log
     datasets say so. The result has `stopped_stage`.
- Decisions: the tunable parameters are only the bootstrap size, minimum days, the event lead window (two tools), the explaining-set threshold
  and the pair option. I excluded alpha, level, power, MDE, seeds and anything the user declares. A refused parameter choice is not trimmed
  to its valid part.
- Mistakes of mine caught on the way: my first `McpToolClient.__aexit__` fix swallowed the wrong error and left a `CancelledError` (found by
  the real closed-server test); a first mutation of the malformed-template check was equivalent to the original and showed "missed", so I
  rewrote it; an assertion in a new test compared against arguments the pipeline legitimately sends.
- Mutation checks: 18 on the new code (foreign refs, missing claim parts, typed event name, design name not required, payloads not frozen,
  transport errors escaping, malformed template crash, render crash, pattern ground dropped, share set dropped, lax window, lax lag, estimate
  note always "not run", unknown parameter allowed, interpretation only at the end, first segment only, late failure dropping evidence). All
  caught.
- Results: `.venv/bin/python -m pytest -q`: 1,209 passed (186 s, none skipped), run once after the last code change; `.venv/bin/python -m
  ruff check causelens tests` passed. That is 113 more tests than the 1,096 before this round. The orchestrator now also runs over
  Streamable HTTP with byte-identical results to in-process and stdio. No statistical calibration was rerun; analytics are unchanged.
- Not verified: any hosted model behind `ModelReasoner`; running the orchestrator inside Kiro.

### Task 8 review round 2 (PR #9, re-review of `ffc060d`)

- Two findings, both reproduced with failing regressions first (10 tests in `tests/test_agents_review.py`, all failing before the fix),
  then fixed. Only `causelens/agents/reasoner.py` and `parameters.py` changed, plus tests and these notes.
  1. **Localization wording.** The segment list was built from every reference whose name began with `s`, which also caught `share`
     ("device=a, device=b, 100.06%"). It happened for one segment as well as several; my round-1 tests used a rendered check that did not
     look at the exact list. Segment references now have their own names (`seg0`, ...) and the list is built from that set only; the
     assumption list is likewise built from the claim's required references, not from a name prefix. Tests cover one, two and three segments.
  2. **Non-finite parameter.** `explain_threshold=NaN` passed because every comparison with NaN is false, was recorded as accepted and made
     the server reject the call (`stopped_tool_error`). `validate_choice` now refuses any non-finite number. NaN, infinity and negative
     infinity from a reasoner fall back to the tool's default, are recorded as refused, and the run completes; a model reply containing
     the JSON token `NaN` is covered too.
- Mutation checks: reverting either fix fails the new regressions (7 and 3 tests).
- Results: `.venv/bin/python -m pytest -q`: 1,219 passed (191 s), once after the last code change; `.venv/bin/python -m ruff check
  causelens tests` passed. No calibration was rerun; analytics are unchanged.
- Not verified: a live hosted model behind `ModelReasoner` (unchanged).
