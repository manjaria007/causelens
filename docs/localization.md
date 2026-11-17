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
                 dispersion="quasi_binomial", min_days_per_window=7)
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
point error (at most 1.2e-14 pp over 1,200 random frames, against a 1e-9 pp tolerance), and `reconciles`
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

## Two-proportion tests and FDR (4.4)

For each segment seen in both windows, with sessions as trials: a pooled two-proportion z-test of
`H0: conversion unchanged`, and a **Newcombe hybrid-score interval** for the change in pp (which behaves
with zero counts, unlike Wald). Both are checked against `statsmodels` in the tests.

**Overdispersion.** Sessions within a day are not independent draws (weekday effects, day-level noise), so
a plain binomial test is optimistic. By default each window's variance is inflated by a quasi-binomial
factor `φ = max(1, Pearson χ² / (days − 1))` estimated from that segment's daily counts (only with at least
5 days of sessions and a rate strictly between 0 and 1; otherwise 1). The interval uses effective sizes
`n / φ`. The plain p-value is always reported next to the adjusted one (`p_value_binomial`), and
`dispersion="none"` gives the textbook test. On the reference data φ reached about 1.6.

**Benjamini–Hochberg.** `benjamini_hochberg` is checked against `statsmodels` and its defining properties.
Each tested segment gets two q-values: `q_value_scan` (BH over **every** segment tested across all
partitions, 71 on the reference data) and `q_value_partition` (BH within its partition). `fdr_family`
chooses which one is `q_value`, which sets `significant` (`q_value < alpha`). The default is the scan,
because the analyst really did look at all of them. The guarantee is approximate: overlapping segments are
positively dependent, which BH tolerates, but it is not proven for this structure.

A tiny effect on millions of sessions can be "significant", so **rank by contribution, not by p-value**;
every segment carries its effect size and interval.

## Ranking, explaining set and localization (4.5)

Segments are ranked by contribution **in the direction of the overall change** (rank 1 contributes most;
exactly tied contributions share a rank and are selected together, so the choice never depends on label
order). With no net change (`|ΔR| ≤ 1e-9 pp`) there is nothing to explain and no explaining set.

The **explaining set** is the smallest prefix of that ranking whose contributions reach `explain_threshold`
(80%) of the change. It reports the selected share (which can exceed 100% when other segments offset it,
and is then said to be so), the remaining share (can be negative, never forced), the rate and mix parts, its
traffic share, and `all_selected_significant`, which is false when the selected segments move mostly
through traffic mix.

**Localization across partitions.** Among partitions whose explaining set carries at most
`max_localized_traffic_share` (50%) of post-window sessions, the one with all selected segments
significant, then the fewest segments, then the least traffic is reported as
`result.localization`. The limit exists because without it a one-value partition (all traffic) or a set
covering everything "explains" the change trivially, which says nothing about where it is. If nothing
qualifies, `localization` is `None` and `localization_note` says the change is spread across traffic and
is consistent with a **broad shift rather than a local break**. Both `explain_threshold` and
`max_localized_traffic_share` are policy parameters, not statistical outputs.

## Measured behaviour

Simulator seeds 1–30 (measured when the defaults were set; tests assert these with margin on 10–12 seeds):

- **No planted effect** (whole-week windows before the release): 0 of 30 datasets had any segment with
  q < 0.05 with the dispersion adjustment; the plain binomial test produced 2 false discoveries in one
  dataset.
- **Planted comparison:** the affected `device × browser` segment was rank 1, significant and the reported
  localization in 30 of 30 datasets; its interval covers the planted rate change on the reference data.
- **False discovery proportion:** mean 0.020 (nominal bound 0.05). 7 of 30 datasets still contained at
  least one false discovery, which FDR control allows: it bounds the expected proportion among discoveries,
  not the chance of any false hit. A test that demanded zero false hits in every dataset failed on seed 5
  and was replaced by this statement.
- Reference data (seed 42): all ten partitions reconcile; the localization is the segment with 30% of
  post-window sessions that accounts for 108% of the −0.184 pp change, the excess being small opposite
  movements elsewhere. A call takes about 0.1 s once the Task 2 quality report is available (the report
  itself costs about 0.2 s and can be passed in with `quality=`).

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
- **Dispersion is estimated, with noise** (about 5–17 daily points per window here) and is floored at 1.
  Test p-values in segments with fewer than 10 pooled orders or non-orders are marked `small_sample` and
  should not be trusted.
- **Weekday mix.** If the windows' weekday mixes differ (distance over 0.1) part of a segment's change may be
  seasonality, and it is flagged in the assumptions. The tool does not remove it.
- **Flagged days are removed everywhere**, including real post-change days near the end of the data; see
  `docs/data-quality.md` on truncated baselines.
- Defaults come from simulated data and need re-checking on real data.

## Tests

- `tests/test_decompose.py`: hand-computed rate/mix/interaction cases, one-sided segments, ranking and tie
  handling, explaining-set arithmetic, the broad-shift and traffic-limit logic, reference-data
  reconciliation, independent recomputation of the change, the pooled rate (against `confirm_change`),
  q-values (against `statsmodels`) and dispersion, exclusion and window disclosure, insufficient data,
  argument validation, wording, and a guard that the source contains no scenario names or dates.
- `tests/test_localization_stats.py`: BH and the two-proportion test against `statsmodels`, null
  uniformity and interval coverage by simulation.
- `tests/test_decompose_properties.py`: Hypothesis properties on random sparse frames, including segments
  that appear or vanish. **Closure** to 1e-9 pp for every partition, shares summing to 1, partition
  independence; **row-order invariance** (bit for bit); **consistent relabeling** (a bijection that reverses
  sort order gives identical numbers under the new names); scaling and zero-row invariance; and fixed-seed
  statistical tests of false discoveries and power. 1,200 further fresh draws found no violation.
