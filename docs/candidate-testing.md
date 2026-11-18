# Candidate testing and the causal estimate (Task 5)

Entry points: `test_candidates` and `test_event_scope` (`causelens/analytics/candidates.py`), with
`estimate_did`, `check_pre_trends`, `run_placebo`, `project_impact` (`did.py`), the timing model
(`timing.py`), the declared-pattern tests (`patterns.py`) and the strength rules (`evidence.py`).
Everything is computed from the data. There are no network or LLM calls, and the planted answer is never read by
the analytics.

## What is tested

* **Exposure scope** is the event's logged `scope`: who the event could have touched. It is never a prediction
  of where conversion moves. An exposure scope disjoint from the localized segment rejects the event only
  when the localized change is itself significant. An `all` scope never rejects.
* **Expected pattern** is a separate, declared input (`ExpectedPattern`; default `unspecified`, nothing is
  inferred). `uniform` is tested with an omnibus Wald test on the ratio estimators (full covariance from shared
  days, Hotelling-type F, Holm across the single-dimension partitions). `restricted` is tested on the traffic outside
  the scope, and absence is claimed only inside a user-declared equivalence margin (no default).
* **Timing.** The daily focal-minus-comparison difference (pp) is fitted with one step per candidate,
  `mu + sum_j beta_j * 1[t >= event_date_j + lag_j]`, by OLS. A single contrast is a t test on the
  residual df. A joint restriction is an F test. Pooled variance is `SSE / (n - rank(X))`, a stated working
  assumption. Step tests are Holm-adjusted across candidates, and the sole-source tests separately. Candidates whose
  step columns coincide are reported as unidentifiable, with no coefficient, no pseudo-inverse and no finite
  share bounds. Share sets (Fieller) are unavailable, with the reason, when they cannot be identified.
* **Lag** is an explicit assumption. Lag 0 (the effect begins on the event date) is the default and the headline says
  so. Each candidate is also evaluated at the other allowed lags (default {0, 1}), one candidate at a time,
  and reported.
* **DiD** (`estimate_did`): differential effect with session weights, default `cov="hc1"`, a t interval, and
  a statement of its assumptions. `project_impact` gives the overall pp change and lost orders.
* **Checks.** Pre-trend: pass if p > 0.10, borderline for 0.05 to 0.10, fail if p < 0.05 (needs 14+ pre days).
  Placebo: 3 fixed fake dates inside the pre-period, Holm-adjusted, fail if any adjusted p < 0.05. Timing
  diagnostics: Ljung-Box (lags 1 to 7) and a pre/post Levene test at p < 0.01 (needs 14 days before and
  7 from the first onset).

## Verdict rules

* `rejected` needs positive evidence: exposure disjoint from a significant change, a contradicted declared pattern, or a
  timing contrast that rejects the event as the sole source (timing status ok). Sole-source rejection takes precedence over
  contribution evidence.
* `supported` needs all of: onset inside the onset set (Task 3 on the focal series); a significant step in the
  direction of the observed change after Holm adjustment; timing and DiD checks run, evaluable and not failed;
  no contradicted pattern; sole-source not rejected. A check that was not run never counts as passed.
* Otherwise `inconclusive`. Onset-set exclusion alone blocks `supported` and never rejects.

## Evidence strength

Judged first from the DiD interval, control validity and required checks: `insufficient` (no valid control or
interval includes 0), `weak` (a check failed or was not run), `moderate` (a borderline check), `strong`. Ambiguous
release attribution (two candidates reaching the same onset at independently allowed lags) then caps the result at
`moderate`. It never raises `weak` or `insufficient`.

## Calibration

### DiD variance: HC1 vs date-clustered (200 null seeds)

| option | 95% interval coverage | mean se (pp) | true sd (pp) |
|---|---|---|---|
| HC1 (default) | 95.5% | 0.049 | 0.045 |
| date-clustered | 99.5% | 0.065 | 0.045 |

Both pass the design's 88-100% coverage over 50 seeds (`test_property_null_calibration_over_50_seeds`). HC1 is
the default because clustering was too conservative here. Cluster remains available as `did_cov="cluster"`.

### Reference data (seed 42)

Default HC1: DiD -0.680 pp, 95% CI [-0.774, -0.585] (the date-clustered interval, `did_cov="cluster"`, is
[-0.805, -0.555]; an earlier version of this document quoted the clustered interval by mistake). Planted segment
effect -0.6618. Projected overall change -0.206 pp, 95% CI [-0.234, -0.177] (planted -0.2002). Checkout is
`supported` under the immediate-effect assumption (lag 0), strength `moderate` (base `strong`, capped by ambiguity).
Hero is `rejected` as the sole immediate explanation. Hero at lag 1 coincides with Checkout at lag 0, so that pair is
unidentifiable from the data and the Hero lag-1 verdict is `inconclusive`.

### Uniform pattern test with a sparse segment (null, 400 datasets per row)

`python tests/calibration/run_pattern_null.py --n 400`. Truth is uniform. Two dense segments plus one sparse segment
seen on `k` days in each window (4,000 sessions on each of those days). The sparse segment is excluded when it has
fewer than 3 active days in a window.

| active days of the sparse segment per window | falsely contradicted (95% Wilson) | sparse segment excluded |
|---|---|---|
| 0 (absent) | 16/400, 4.0% (2.5-6.4%) | 0/400 |
| 1 | 12/400, 3.0% (1.7-5.2%) | 400/400 |
| 2 | 15/400, 3.8% (2.3-6.1%) | 400/400 |
| 3 (the minimum that is included) | 20/400, 5.0% (3.3-7.6%) | 0/400 |
| 5 | 10/400, 2.5% (1.4-4.5%) | 0/400 |
| 10 | 10/400, 2.5% (1.4-4.5%) | 0/400 |

Before the fix, one active day per window gave 37 false contradictions in 40 datasets. The attribution scenarios
declare no pattern, so this fix does not change them and they were not rerun.

### Full pipeline, 150 seeds per scenario

`python tests/calibration/run_attribution.py --scenarios <name> --n 150`. The pipeline is quality, confirm
change, localize, then test candidates. Intervals are 95% Wilson. "Incorrect" means a wrong event was `supported`
or the true event was `rejected`; it is split by whether release attribution was flagged ambiguous.

| scenario (truth) | correct | event recovered, step misspecified | unmet (inconclusive) | stopped at confirm | incorrect, flagged | incorrect, unflagged |
|---|---|---|---|---|---|---|
| planted (Checkout, lag 0) | 124/150 (83%, 76-88%) | - | 23 (15%) | 0 | 3 (2%, 1-6%) | 0 (0-2%) |
| hero_immediate (Hero, lag 0; swapped cause) | 138/150 (92%, 87-95%) | - | 12 (8%) | 0 | 0 | 0 (0-2%) |
| hero_delayed (Hero, lag 1) | 0/150 | - | 26 (17%) | 0 | 124 (83%, 76-88%) | 0 |
| checkout_delayed (Checkout, lag 1; tested at lag 0) | 0/150 | - | 148 (99%) | 0 | 2 (1%) | 0 |
| same_day (indistinguishable events) | 150/150 (100%, 98-100%) | - | 0 | 0 | 0 | 0 |
| weak_12 (Checkout, -12%) | 41/150 (27%, 21-35%) | - | 95 (63%) | 8 (5%) | 3 (2%, 1-6%) | 3 (2%, 1-6%) |
| weak_6 (Checkout, -6%) | 7/150 (5%, 2-9%) | - | 51 (34%) | 88 (59%) | 2 (1%) | 2 (1%) |
| neither (unlogged change 20 Oct) | 148/150 (99%) | - | 0 | 2 (1%) | 0 | 0 |
| none (no change) | 5/150 (3%) | - | 0 | 144 (96%) | 1 (1%) | 0 |
| staged (Hero half, Checkout half) | 50/150 (33%, 26-41%) | - | 0 | 0 | 56 (37%, 30-45%) | 44 (29%, 23-37%) |
| gradual_hero (Hero ramp over 3 days) | 0/150 (not scored as correct) | 18/150 (12%, 8-18%) | 70 (47%) | 0 | 62 (41%, 34-49%) | 0 |

The review of this PR corrected the scoring. An error is now flagged when any event involved in it (a wrongly
supported event, or the true event when it was rejected) is marked ambiguous; before, a rejected true cause was
always counted as unflagged. In `gradual_hero`, naming Hero is no longer counted as "correct": the verdict states an
immediate step on 13 Oct, and the true effect is a three-day ramp. The verdicts themselves are identical to the first
run (the other fixes in this round change edge cases that these scenarios do not reach); only the grading moved.
Net effect: the planted incorrect rate is 2%, all flagged ambiguous (3 of 150 runs reject the true cause), and the
weak-effect incorrect rates are 2% flagged plus 2% unflagged at -12% and 1% plus 1% at -6%.

How to read it:

* **Planted and swapped causes.** The true cause is recovered in 83% and 92% of runs and the wrong event is never
  supported. Planted recovery is lowered by the prespecified checks (pre-trend failures about 5% by design, variance
  diagnostic failures about 3%, steps too small to reject). 2% of planted runs reject the true cause (a Holm second
  test at the nominal 5% level); all of them carry the ambiguity flag.
* **Strength.** Planted supports are all `moderate` (ambiguity cap), and swapped-cause supports are `strong` in 132 of
  138 runs, because Checkout is rarely a candidate there.
* **Delayed effects.** `hero_delayed` data are identical to `planted`. With lag 0 as the default the tool
  names Checkout; it flags the attribution as ambiguous in 99% of runs, but the supported event is wrong in 83% of
  hero_delayed runs. Nothing in the data can fix that; only an experiment or a declared lag can. `checkout_delayed`
  is `inconclusive` at lag 0 in 99% and `supported` at lag 1 in 91% (136/150), reported in the lag sensitivity.
* **Indistinguishable events.** Never attributed (0/150 supported).
* **Weak effects.** At -12% the tool recovers the cause in 27% of runs and gives a wrong or rejecting verdict
  in 4% (2% flagged ambiguous, 2% not). At -6% most runs stop at `confirm_change` (59%).
* **Staged and gradual effects.** These break the single-source model. 29% of staged runs are incorrect without an
  ambiguity flag (Hero alone supported, Checkout outside the onset set). Treat a lone `supported` result as
  one explanation, not as the whole change, when the onset set is wide.
* **No change.** 96% stop at `confirm_change`. One run of 150 reaches a `supported` verdict (1%).

## Review fixes (PR #5)

Each case was reproduced first and has a regression test in `tests/test_review_regressions.py`.

* Uniform test: a segment needs at least 3 active days (days with sessions) in each window, otherwise it is excluded
  with a reason. A segment seen on one day per window has zero estimated variance and falsely rejected
  uniformity in 37 of 40 datasets; the regression test now allows at most 5 of 40. A Holm p-value of exactly 0 now counts as a rejection.
* DiD, pre-trend and placebo regressions report `not estimable` when residuals are at rounding level (data fitted
  exactly, such as two groups that never change), instead of a spurious significant effect.
* A placebo that was requested but could not be evaluated leaves the check `not_evaluable` (so it cannot support
  `strong` evidence); a failure among the evaluated dates still fails.
* `project_impact` divides by all post-period sessions of the whole population (new `s_all` panel column), also with an
  explicit comparison group.
* Lags 0 and 1 are always evaluated; `sensitivity_lags` can only add lags. Lags must be integers (booleans,
  fractions, NaN and strings raise `ValueError`).
* The localized segment is read from the decomposition's structured labels, so values containing commas work.
* The timing step's standard error comes from the covariance, so a coefficient of exactly 0 has finite bounds.
* Pre-trend slopes use elapsed calendar days, so missing days do not inflate the slope.

## Limitations

* A lag is assumed, not estimated. When two events can produce the same onset, the data cannot separate them.
* The onset set is an approximate likelihood-ratio set and undercovers for small effects.
* The timing model assumes independent homoskedastic daily noise; diagnostics screen for gross violations only.
* Pooled variance is a working assumption subject to the calibration above.
* A single-day gap between two events gives little power; weak effects are mostly inconclusive.
* A differential effect equals the segment's own effect only if the comparison traffic was unaffected.
* Staged and gradual effects are not modelled; their incorrect rates are reported above.

## Tests

`tests/test_did.py`, `test_timing.py`, `test_patterns.py`, `test_evidence.py`, `test_candidate_testing.py`
and `test_attribution_pipeline.py` (small-N scenario margins). The N=150 table is reproduced by
`tests/calibration/run_attribution.py`.
