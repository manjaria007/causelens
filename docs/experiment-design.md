# Experiment design (Task 6)

Entry points: `power_analysis`, `normal_approx_power` and `design_experiment`
(`causelens/analytics/experiment.py`). Every number is computed deterministically; there are no network or LLM
calls (Requirement 7.4).

## `power_analysis`

**Units.** `baseline` is a fraction in (0, 1). `mde_pp` is a signed change in percentage points of the treatment arm
relative to the control arm:

```text
target_rate = baseline + mde_pp / 100
```

The target rate must also lie in (0, 1); an error message says that `mde_pp` is in percentage points when it does not.

**Test and quantity.** The planned test is the two-sided pooled two-proportion z-test. The variance is pooled under the
null and unpooled under the alternative, and both rejection tails count. The result is called **achieved power under
the normal approximation** (`achieved_power`, with `achieved_power_label`), not exact finite-sample power.

**Allocation.** Explicit and integer: `n_control = n` and `n_treatment = ceil(allocation_ratio * n)`, where
`allocation_ratio` is treatment sessions per control session. `n` is the smallest integer whose pair reaches the target
power. The result reports the previous pair's power (`previous_pair_power`, below the target by construction). The
search brackets by doubling and bisects, which relies on power not decreasing as `n` grows. The tests check this
against `statsmodels.stats.proportion.power_proportions_2indep` for a range of cases and a scan of 40 values below
each minimum, and a separate scan of 3,876 random cases found no violation of the monotonicity in effect size.

**Mapping to statsmodels.** In `power_proportions_2indep`, sample 1 is the treatment arm, so `nobs1 = n_treatment`
and `ratio = n_control / n_treatment`. The tests include a case showing that swapping the arms gives a different
answer, so the mapping is checked and not assumed.

**Several primary comparisons.** `n_comparisons = k` divides alpha by `k` (Bonferroni) and the power is **per
comparison**. It is not the probability that every comparison is detected. The Monte Carlo below shows the
family-wise error stays below alpha (4.63% for k = 2 and 4.46% for k = 3).

**Validation.** `baseline` in (0, 1); `mde_pp` finite and non-zero with the target rate in (0, 1); `alpha` in
(0, 0.5]; `power` strictly between alpha-per-comparison and 1; `allocation_ratio` in [0.01, 100];
`n_comparisons` an integer of at least 1. Booleans, strings, NaN and infinity are rejected, and a required size above
10^12 per arm raises an error.

**Sparse counts.** `approximation_reliable` is false, with a warning, when the smallest expected count (successes or
failures in either arm, under the null or the alternative) is below 10.

### Worked values

| baseline | mde_pp | alpha | comparisons | power | allocation | n control | n treatment | achieved power |
|---|---|---|---|---|---|---|---|---|
| 5% | +1.0 | 0.05 | 1 | 0.8 | 1 | 8,158 | 8,158 | 0.80001 |
| 4% | -0.6 | 0.05 | 1 | 0.8 | 2 | 11,529 | 23,058 | 0.80000 |
| 4% | -0.6 | 0.05 | 2 | 0.8 | 1 | 18,814 | 18,814 | 0.80000 |
| 30% | +3.0 | 0.05 | 3 | 0.9 | 1 | 6,476 | 6,476 | 0.90000 |

## `design_experiment`

Input: the Task 5 `CandidateTestingResult`, the metrics, and the post-period window. Output: a randomized **rollback**
for the focal segment, or `status = "needs_input"` with what is missing.

* **Target.** By default the leading candidate (the single supported one), with the other releases held fixed. This
  tests the release's *current contribution*; it cannot establish that it was the sole historical cause. If nothing
  leads and exactly one candidate is not rejected, that one is used. Otherwise the result is `needs_input` for
  `target_candidate`. `target_candidate` names one explicitly.
* **Several open candidates.** The default stays a two-arm rollback of one candidate. When two or more candidates are
  not rejected, `alternative_plan` also gives the shared-control option, one rollback arm per candidate, for when
  independent rollbacks are feasible and comparing their contributions is the objective. `arms="each_candidate"`
  makes it the main plan. Alpha is divided by the number of primary comparisons and power is labelled per comparison.
* **Purpose.** `confirm` when the target's evidence strength is `strong`, otherwise `settle`.
* **Baseline.** The usable post-period focal conversion (flagged days removed). That is the experience the control arm
  keeps. The result reports its window, the days used and the underlying orders and sessions. Fewer than 7 usable days
  gives `needs_input` for `post_window`.
* **Minimum detectable effect.** A user-declared business target (`mde_pp`) is preferred. Without one, a
  **provisional planning heuristic** applies only if the target's differential-effect interval excludes zero: the
  bound nearest zero, with its sign reversed (a rollback undoes the observed effect, so a decline gives a positive
  target). It does not correct winner's curse or selection bias, and the result says so. With no usable estimate, or an
  interval that includes zero, the result is `needs_input` for `mde_pp`. For several arms, the smallest magnitude
  sizes every arm. A candidate without a Task 5 estimate gets a default (HC1, 95%) DiD at its own onset date.
* **Randomization unit.** The session, stated as a planning assumption that needs independent sessions and no
  cross-session carryover. The data has no visitor ids, which does not establish either condition.
* **Duration.** `days = ceil(max over arms of n_arm / (daily_eligible_sessions * allocation_share_arm))`, with the
  shared control counted once, then rounded up to whole weeks. `daily_eligible_sessions` is the mean over the usable
  post-period days, and the result states that traffic is assumed to continue at that rate with all eligible sessions
  enrolled.
* **Guardrails**, each labelled by what can be measured:
  * *Sample-ratio mismatch*: an assignment diagnostic that needs the experiment's assignment logs; not computable from
    the metrics table.
  * *Conversion outside the experiment*: an external monitor, meaningful only while those sessions stay outside it.
    The observed reference is reported for the same days.
  * *Revenue or value per order* and *checkout errors and latency*: need new instrumentation; no value is given.
* **Wording.** The hypothesis is conditional ("If X currently contributes to ..."), and states that it cannot establish
  the sole historical cause.

### Reference data (seed 42), default design

Leading candidate Checkout v4.2 (evidence `moderate`; Hero is rejected, so no alternative plan). Baseline 1.985%
(4,808 orders in 242,199 sessions, 18 usable days, 2026-10-14 to 2026-10-31). Differential effect -0.680 pp, 95%
interval [-0.774, -0.585], so the heuristic target is +0.585 pp. The plan is 10,216 sessions per arm, achieved power
under the normal approximation 0.8000, at 13,455.5 observed sessions per day: about 2 days, so 7 days rounded to
whole weeks. With a declared target of +0.3 pp it is 36,445 per arm, 6 days, rounded to 7.

Two open candidates (`same_day`, seed 2), declared target +0.4 pp: the default two-arm plan for Checkout needs 21,210
per arm in 4 days. The shared-control alternative (alpha 0.025 each) needs 25,685 per arm in 6 days.

## Monte Carlo check

`python tests/calibration/run_power_mc.py --reps 2000000` (seeded, about 12 s). It runs the planned test: each
treatment arm against one shared control, two-sided pooled z-test at alpha / k. It is not part of the fast suite; a
two-test smoke check of the helper is (`tests/test_power_mc_smoke.py`).

| configuration | n control / treatment | alpha per comparison | null rejection, per comparison | null rejection, any comparison (alpha) | normal-approx power | simulated power, per comparison (target) | reliable? |
|---|---|---|---|---|---|---|---|
| 5% baseline, +1.0 pp | 8,158 / 8,158 | 0.05 | 5.01% (4.98-5.05%) | 5.01% (4.98-5.05%) (<= 5%) | 0.8000 | 80.02% (79.96-80.07%) (0.8) | yes |
| reference-like: 1.99%, +0.585 pp | 10,230 / 10,230 | 0.05 | 5.01% (4.98-5.04%) | 5.01% (4.98-5.04%) (<= 5%) | 0.8000 | 80.16% (80.11-80.22%) (0.8) | yes |
| 4%, -0.6 pp, treatment 2x control | 11,529 / 23,058 | 0.05 | 4.99% (4.96-5.02%) | 4.99% (4.96-5.02%) (<= 5%) | 0.8000 | 80.03% (79.98-80.09%) (0.8) | yes |
| 4%, -0.6 pp, treatment 0.5x control | 23,541 / 11,771 | 0.05 | 5.00% (4.97-5.03%) | 5.00% (4.97-5.03%) (<= 5%) | 0.8000 | 80.10% (80.04-80.15%) (0.8) | yes |
| 4%, -0.6 pp, 2 comparisons | 18,814 / 18,814 | 0.025 | 2.49% (2.47-2.51%) | 4.63% (4.60-4.66%) (<= 5%) | 0.8000 | 80.03% (79.97-80.08%) (0.8) | yes |
| 30%, +3 pp, 3 comparisons, power 0.9 | 6,476 / 6,476 | 0.0166667 | 1.66% (1.64-1.68%) | 4.46% (4.43-4.48%) (<= 5%) | 0.9000 | 89.99% (89.95-90.03%) (0.9) | yes |
| 1%, +0.2 pp, alpha 0.01 | 63,527 / 63,527 | 0.01 | 1.00% (0.98-1.01%) | 1.00% (0.98-1.01%) (<= 1%) | 0.8000 | 80.06% (80.01-80.12%) (0.8) | yes |
| sparse: 0.1%, -0.09 pp | 10,652 / 10,652 | 0.05 | 5.21% (5.18-5.24%) | 5.21% (5.18-5.24%) (<= 5%) | 0.8000 | 86.17% (86.13-86.22%) (0.8) | no: min expected count 1.1 |

reps per configuration: 2,000,000; seed 20261113 (+ configuration index); two-sided pooled z-test.


Reading it:

* **Null.** Where the approximation is reliable, the per-comparison rejection rate is 4.99% to 5.01% at alpha 0.05
  (2.49% at 0.025, 1.66% at 0.0167, 1.00% at 0.01), inside or within 0.01 points of the nominal level. The
  family-wise rate with several arms is below alpha (4.63% for two arms, 4.46% for three).
* **Power.** Simulated power is within 0.16 points of the target in every configuration flagged reliable (80.02% to
  80.16% against 80%, and 89.99% against 90%). The reference-like row is 80.16% (80.11-80.22%), slightly above target.
  The sizes are whole numbers and the binomial is discrete, so the normal approximation is not exact.
* **Sparse case.** At 0.1% with -0.09 pp the smallest expected count is 1.1, so the plan flags it unreliable. There
  the null rejection is 5.21% (5.18-5.24%), above the nominal 5%, and the simulated power is 86.2% against the planned
  80.0%. The normal approximation is wrong in both directions there, which is why the warning exists. An exact or
  simulation-based calculation is needed for such designs.

## Limitations

* The heuristic target is not a business target. It starts from an estimate that was selected for being notable.
* Session randomization is a planning assumption. Visitors who return across arms, or carryover, would violate it.
* Duration assumes the observed traffic continues, with all eligible sessions enrolled and no seasonality beyond the
  weekly rounding.
* A rollback measures the current contribution of one release with the rest fixed. Two releases that interact need the
  multi-arm option or a factorial design, which is not built.
* No candidate release (`no_candidates`, `not_localized`) gives `needs_input`; this version does not design an experiment
  for an unlogged change.
* Guardrails beyond sessions and orders need instrumentation that this data does not have.

## Tests

`tests/test_experiment_power.py` (67 tests, including monotonicity and the statsmodels comparison),
`tests/test_experiment_design.py` (42 tests) and `tests/test_power_mc_smoke.py`.
