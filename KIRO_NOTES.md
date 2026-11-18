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
