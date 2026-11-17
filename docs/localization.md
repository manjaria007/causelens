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

## Tests

- `tests/test_decompose.py`: hand-computed rate/mix/interaction cases, one-sided segments, ranking and tie
  handling, explaining-set arithmetic, the broad-shift and traffic-limit logic, reference-data
  reconciliation, independent recomputation of the change, the pooled rate (against `confirm_change`),
  q-values (against `statsmodels`) and one segment's day-level test (rebuilt with plain numpy from the raw
  rows), exclusion and window disclosure, insufficient data, argument validation, wording, and a guard that
  the source contains no scenario names or dates. Regression tests for the review findings: a fixture where
  the top-ranked set exceeds the traffic limit but a narrower combination qualifies (contributions checked
  against independent arithmetic), a limited search never claiming a broad shift, and sparse arms.
- `tests/test_localization_stats.py`: BH, the two-proportion test and Fisher's exact test against
  `statsmodels`/`scipy`; the day-level test against hand computation, its flat-data and few-day behaviour, and
  calibration under the null across 3–14 days and three levels of day-level noise (with the plain test as a
  negative control).
- `tests/test_decompose_properties.py`: Hypothesis properties on random sparse frames, including segments
  that appear or vanish. **Closure** to 1e-9 pp for every partition; **row-order invariance** (bit for bit);
  **consistent relabeling** (a bijection that reverses sort order gives identical numbers, including the local
  set); scaling invariance; **zero-session rows equal absent rows** (the padding rows are now genuinely absent
  keys, asserted to have been added); the **local set equals a brute-force search** over all combinations
  (completeness, soundness and the stated optimality order); the **reliability rule** (no p-value, q-value or
  significance below 5 expected counts); calibration through the whole pipeline; and fixed-seed statistical
  tests of false discoveries and power. A further 600 fresh random frames, checked against closure, row-order
  invariance, relabeling, padding equivalence, the brute-force local set and the reliability rule, found no
  violation (worst residual 1.1e-14 pp; 6,588 padding rows added).
