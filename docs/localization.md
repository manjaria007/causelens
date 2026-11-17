# Localizing the change (Task 4)

Code: `causelens/analytics/decompose.py`. Requirements: 4.1–4.5. Pure and deterministic, no network or
LLM calls. Nothing in the analytics names a segment, date or event; tests read `ground_truth.json` only
to grade results.

`decompose_change` says **where** a conversion change is concentrated. It never says why. Its wording
follows `.kiro/steering/scientist-rules.md`: "is concentrated in", effect sizes beside every p-value,
intervals and sample sizes, multiple comparisons controlled and stated, and what remains unexplained shown
rather than forced to 100%.

```python
decompose_change(metrics, pre_window, post_window, events=None, *, dimensions=DIMENSIONS,
                 include_pairs=True, quality=None, apply_exclusions=True, alpha=0.05, level=0.95,
                 explain_threshold=0.8, max_localized_traffic_share=0.5, fdr_family="scan",
                 inference="day_level", min_days_per_window=7)
```

## The accounting (4.1–4.3)

Pooled conversion is `R = orders / sessions` over a window's usable days, the same quantity
`confirm_change` reports (a test checks that `change_pp` equals its pooled change). For a partition of
the traffic into segments `s` (every value of one dimension, or every combination of two), let `w` be a
segment's share of the window's sessions, `r` its conversion, `0`/`1` the before/after windows, and
`x̄` the midpoint of the two windows' values:

| Effect | Formula | Meaning |
|---|---|---|
| rate effect | `w̄_s · (r1_s − r0_s)` | the segment's conversion changed |
| mix effect | `(w1_s − w0_s) · (r̄_s − R̄)` | the segment's share of traffic changed |
| contribution | rate + mix | |

Midpoint weights split the rate × mix interaction evenly instead of assigning it to one window, so the
result does not depend on which window is called the base. Centring the mix effect on `R̄` means a
segment that gains traffic only moves the overall rate if its conversion differs from the average. Since
`w̄(r1−r0) + (w1−w0)·r̄ = w1·r1 − w0·r0` and `Σ(w1−w0) = 0`,

```
Σ rate effects + Σ mix effects = R1 − R0      for every partition
```

**Residual.** Each partition reports `residual_pp = (R1 − R0) − (Σ rate + Σ mix)`, where `R1 − R0` comes
from independently derived window totals. Because the identity is exact, the residual is only floating
point error (about 1e-14 pp at worst on every random frame tried, against a 1e-9 pp tolerance), and `reconciles`
says so per partition. It is shown so the reconciliation is checked, not assumed. A segment present in
only one window has no observable rate change: its rate effect is 0, its whole contribution is a mix
effect, and it has no test.

**What is genuinely unexplained is statistical, not arithmetic.** Each partition also splits the rate
effects by evidence: `rate_effect_significant_pp` (segments with q < alpha) and
`rate_effect_not_distinguishable_pp` (the rest, including untested segments). The second is the part of
the movement that cannot be told from noise.

**Partitions are kept separate.** `device` and `device × browser` describe the same sessions, so each
partition has its own segments, reconciliation, ranking and explaining set. Contributions are never added
across partitions (a property test shows the grand total is ten times the change). Pairs are formed from
the requested `dimensions` in the order given (`browser × device` is never also produced).

Window handling: pre must end before post starts; the windows, usable and excluded days, excluded
*dates*, sessions, orders and pooled conversion are returned per window (`result.pre`, `result.post`),
and `result.exclusions` is the same disclosure `confirm_change` gives (including the uncertain
`historical_only` incident context). Task 2's default exclusions remove whole days, so segment mix on the
remaining days is not distorted. Fewer than `min_days_per_window` usable days (or no sessions) in either
window gives `status="insufficient_data"` with no decomposition and the counts per window.

## Tests and FDR (4.4)

For each segment seen in both windows the question is `H0: conversion unchanged`.

**Days with sessions are the sampling units (default, `inference="day_level"`).** Sessions within a day are
not independent draws (weekday effects, day-level noise), so a plain binomial test is badly optimistic: on a
null simulation with day-level noise and seven days per window it rejected 34% of true nulls at a nominal 5%.
Each window's pooled rate (`R = Σ orders / Σ sessions`) gets the ratio-estimator variance
`d/(d−1) · Σ(orders_i − R·sessions_i)² / (Σ sessions)²` over its `d` days **with sessions**, and the
difference is tested with a Welch t statistic on Welch–Satterthwaite degrees of freedom. This is the method
`confirm_change` uses for the overall change. The t distribution accounts for estimating the variance from
few days, and the interval is `Δ ± t · se`.

- **A day with no sessions is not an observation.** Counting a segment's absent days as zero-session days
  inflated its degrees of freedom, and for a segment seen on one day per window (100 sessions and 10 orders
  before, 100 and 11 after) it forced a variance of exactly zero, so it was reported with 14 days per window,
  p = q = 0 and the interval [1.0, 1.0]. Absent days are now ignored, and padding a segment with zero-session
  rows changes nothing (tested).
- **A segment needs at least 3 days with sessions in each window** (`MIN_ACTIVE_DAYS`); with fewer there is
  no usable variance (about one degree of freedom). It is then withheld, not given an optimistic fallback:
  no p-value, q-value or significance claim, and the note says why. `inference="binomial"` is the explicit,
  labelled way to ask for the plain test.
- **The variance is floored at the binomial variance** `R̃(1−R̃)/Σ sessions`, with `R̃ = (orders + 0.5) /
  (sessions + 1)`. Conversions cannot be more predictable than independent sessions, a few coincidentally
  similar days must not produce a vanishing variance, and a window with no orders (or only orders) still
  carries sampling uncertainty (the interval for 0/3,000 versus 0/3,000 is ±0.07 pp, not zero width). A
  floored window's variance is a bound, not an estimate, so it adds no estimation uncertainty to the degrees
  of freedom. `variance_floored_pre/post` records when this applied, and the design effect (day-level variance
  ÷ binomial variance; the reference data reaches about 1.4) is reported raw.
- `binomial` always carries the plain two-proportion result (checked against `statsmodels`) for comparison.

An earlier version inflated the binomial variance by an *estimated* dispersion factor and then used normal
tails as if the factor were known; that rejected 7.2% of true nulls at a nominal 5% with seven days per window
and its "95%" intervals covered zero 92.7% of the time.

**Calibration** (isolated two-window comparisons, 6,000–12,000 runs per setting, Monte Carlo error about 0.3
points): the final test rejected 2.3%–5.6% of true nulls at a nominal 5% and its 95% intervals covered zero
94.4%–97.7% of the time, for 3 to 28 days per window and from no day-level noise to heavy noise, and with a long
before-window and a one-week after-window. Short windows and clean binomial data are conservative, which is
the safe direction. Through the whole pipeline on disjoint segments (300 frames, 2,400 tests, correlated within
frames) it rejected 4.9% at 7 days and 4.3% at 14 days; the plain binomial test rejected 34% and 31%.

**Segments that cannot be tested reliably are not tested.** There are two reasons, both recorded as
`unreliable_reason`:

- `sparse_counts`: the normal approximation needs every cell of the pooled before/after table (orders and
  non-orders in each window, expected under the null) to be at least 5. A segment with 0 orders in 1 session
  before and 900 in 1,000 after has plenty of pooled orders but a one-session arm: the plain z-test gave
  p = 0.0028 (called significant) while Fisher's exact p is 0.10.
- `too_few_active_days`: fewer than 3 days with sessions in a window (above).

Such a segment has `reliable = false`, no p-value, no q-value, `significant = false`, and no place in the
false-discovery family. Fisher's exact p-value and the plain binomial result are shown for information only
(both assume independent sessions). Its contribution is still counted in the accounting and its rate effect
appears in `rate_effect_not_distinguishable_pp`, and a set containing it is never "all significant".

**Benjamini–Hochberg.** `benjamini_hochberg` is checked against `statsmodels` and its defining properties.
Each reliably tested segment gets two q-values: `q_value_scan` (BH over **every** reliably tested segment
across all partitions, 71 on the reference data) and `q_value_partition` (BH within its partition).
`fdr_family` chooses which one is `q_value`, which sets `significant` (`q_value < alpha`). The default is the
scan, because the analyst really did look at all of them. BH only repairs multiplicity, not miscalibrated
input p-values, which is why the calibration above matters. The guarantee is approximate: overlapping segments
are positively dependent, which BH tolerates, but it is not proven for this structure.

A tiny effect on millions of sessions can be "significant", so **rank by contribution, not by p-value**;
every segment carries its effect size and interval.

**Input checking.** The public helpers (`day_level_test`, `two_proportion_test`, `fisher_exact_p`) reject
NaN, infinity, fractional counts, negative values and orders above sessions with `ValueError` before any
calculation. (`day_level_test` used to return NaN estimates and p-values for NaN input.)

## Ranking, explaining set and localization (4.5)

Segments are ranked by contribution **in the direction of the overall change** (rank 1 contributes most;
exactly tied contributions share a rank and are selected together by the ranked prefix, so its choice never
depends on label order). With no net change (`|ΔR| ≤ 1e-9 pp`) there is nothing to explain and no explaining
set.

The **explaining set** (`kind = "ranked_prefix"`) is the smallest prefix of that ranking whose contributions
reach `explain_threshold` (80%) of the change, with no constraint on traffic. It reports the selected share
(which can exceed 100% when other segments offset it, and is then said to be so), the remaining share (can be
negative, never forced), the rate and mix parts, its traffic share, and `all_selected_significant`, which is
false when the selected segments move mostly through traffic mix or cannot be tested.

**Localization across partitions (a search, not just the ranked prefix).** The ranked prefix is the fewest
segments needed, but it can fail the traffic limit while another combination qualifies: a large segment may
rank first yet carry 60% of traffic, while a smaller one alone accounts for 86% of the change on 30%. (That
exact case used to be reported as "spread across traffic".) So each partition also searches combinations of
its positively contributing segments for the best set that reaches `explain_threshold` while carrying at most
`max_localized_traffic_share` (50%) of post-window sessions (`local_set`, `kind = "traffic_limited_search"`).
Among qualifying combinations it prefers one where every selected segment has a distinguishable rate change,
then the fewest segments, then the least traffic, then the largest contribution. The partition whose local set
is best by the same order is `result.localization`.

*What counts as interchangeable.* Two segments are interchangeable only if they have identical counts **and**
the same test outcome (significant or not). Counts alone are not enough: two segments with the same pooled
counts can have very different daily variability, so one can be significant and qualify as a localization
while the other cannot. (Grouping on counts alone once merged such a pair into a 77%-of-traffic block, rejected
both, and reported a broad shift although the significant one qualified on 39%.) Interchangeable segments are
searched by number: any number from none to all of a group can be chosen, so two identical segments that each
qualify alone are not forced together. The segment actually named is the first by label, and the others are
listed in `interchangeable_with`, since any of them would give the same numbers.

*Search size.* The search is exhaustive over up to 12 such groups and 65,536 combinations per partition; if
there are more, the largest contributors are searched and `local_search_exhaustive` is false.

The traffic limit exists because without it a one-value partition (all traffic) or a set covering everything
"explains" the change trivially, which says nothing about where it is. If nothing qualifies and every search
was exhaustive, `localization` is `None` and the note says the change is **consistent with a broad shift
rather than a local break**. If any search was limited, the note says only that no qualifying set was found
among the largest contributors and that this does not by itself establish a broad shift. Both
`explain_threshold` and `max_localized_traffic_share` are policy parameters, not statistical outputs.

## Measured behaviour

Simulator seeds 1–30 (measured with the final inference when the defaults were set; the tests assert these
with margin on 10 seeds):

- **No planted effect** (whole-week windows before the release): 0 of 30 datasets had any segment with
  q < 0.05 (nominal expectation about 1.5 under a global null).
- **Planted comparison:** the affected `device × browser` segment was rank 1, significant and the reported
  localization in 30 of 30 datasets; its interval covers the planted rate change on the reference data.
- **False discovery proportion:** mean 0.019 (nominal bound 0.05). 8 of 30 datasets still contained at least
  one false discovery, which FDR control allows: it bounds the expected proportion among discoveries, not the
  chance of any false hit. A test that demanded zero false hits in every dataset failed on one seed and was
  replaced by this statement.
- Reference data (seed 42): all ten partitions reconcile; no segment is too sparse or too intermittent to
  test; the localization is the segment with 30% of post-window sessions that accounts for 108% of the
  −0.184 pp change, the excess being small opposite movements elsewhere (day-level BH q = 2.7e-15). A call
  takes about 0.1 s once the Task 2 quality report is available (the report itself costs about 0.2 s and can
  be passed in with `quality=`).

## Limitations

- **Descriptive, not causal.** Contributions are accounting; segment concentration alone is not evidence
  of a cause (scientist rules). Task 5 tests candidate explanations.
- **Coarser partitions can show a mix shift as a rate change.** If traffic moves between values of a
  dimension that is not in the partition, that partition's pooled rate moves. A partition with one value is
  the extreme case. This is why the localization has a traffic limit and why the mix-only test case is
  reported as broad.
- **Pairs only.** A break confined to a three-way combination is diluted in every pair.
- **Selection.** Reporting the narrowest of ten overlapping partitions is a data-driven choice; q-values
  account for the number of segments tested, not for that choice.
- **Few days mean wide intervals, and very few mean no test.** The day-level test is honest about this (a week
  per window costs power through the t distribution) and withholds a test below 3 days with sessions per
  window. An intermittent segment is judged on the days it was active.
- **The localization search is exhaustive only up to 12 groups per partition**, and only within single
  partitions. Combinations that mix segments from different partitions are not searched.
- **Weekday mix.** If the windows' weekday mixes differ (distance over 0.1) part of a segment's change may be
  seasonality, and it is flagged in the assumptions. The tool does not remove it.
- **Flagged days are removed everywhere**, including real post-change days near the end of the data; see
  `docs/data-quality.md` on truncated baselines.
- **Calibration was measured on simulated data** with weekday-free day-level noise; real data with autocorrelated
  days would need re-checking, and defaults need the same.

## Tests

- `tests/test_decompose.py`: hand-computed rate/mix/interaction cases, one-sided segments, ranking and tie
  handling, explaining-set arithmetic, the broad-shift and traffic-limit logic, reference-data
  reconciliation, independent recomputation of the change, the pooled rate (against `confirm_change`),
  q-values (against `statsmodels`) and one segment's day-level test (rebuilt with plain numpy from the raw
  rows), exclusion and window disclosure, insufficient data, argument validation, wording, and a guard that
  the source contains no scenario names or dates. Regression tests for the review findings: a fixture where
  the top-ranked set exceeds the traffic limit but a narrower combination qualifies (contributions checked
  against independent arithmetic); a limited search never claiming a broad shift; sparse arms; a segment seen
  on one day per window (withheld, no p = 0); three days as the boundary; zero-padding invariance; constant
  daily rates; equal counts with different significance not being grouped; identical qualifying twins not
  being forced together.
- `tests/test_localization_stats.py`: BH, the two-proportion test and Fisher's exact test against
  `statsmodels`/`scipy`; the day-level test against an independent recomputation in both variance regimes
  (floor binding and not), never degenerate, zero-session days ignored, input rejection (including every
  non-finite value in every argument position), a Hypothesis property that results are always finite and
  non-degenerate, and calibration under the null across 3–14 days and three levels of day-level noise (with
  the plain test as a negative control).
- `tests/test_decompose_properties.py`: Hypothesis properties on random sparse frames, including segments
  that appear or vanish. **Closure** to 1e-9 pp for every partition; **row-order invariance** (bit for bit);
  **consistent relabeling** (a bijection that reverses sort order gives identical numbers; the local set is
  compared by counts and outcomes because which of two interchangeable segments is named may follow label
  order); scaling invariance; **zero-session rows equal absent rows** (the padding rows are genuinely absent
  keys, asserted to have been added); the **local set equals a brute-force search** over every subset of
  individual segments, on random frames and on frames with deliberately duplicated twin segments; the
  **reliability rule** (no p-value, q-value or significance below 5 expected counts or 3 active days; the
  active-day counts are recomputed from the raw frame); calibration through the whole pipeline; and fixed-seed
  statistical tests of false discoveries and power. A further 400 fresh random frames (half with twins)
  checked against all of these found no violation (worst residual 1.1e-14 pp).
