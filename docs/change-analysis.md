# Confirming a change and locating it (Task 3)

Code: `causelens/analytics/series.py`, `confirm.py`, `changepoint.py`.
Requirements: 2.1–2.3 (confirm the change is real) and 3.1–3.3 (locate when it started).
All functions are pure and deterministic (the bootstrap takes an explicit `seed`) and make no
network or LLM calls. Nothing here reads `ground_truth.json`; tests use it only to grade results.

These tools say **whether** a difference exists and **when** the level shifted. They never say
why. Their text follows `.kiro/steering/scientist-rules.md`: effect sizes accompany p-values,
every estimate has an interval and a sample size, and a date coincidence is not a cause.

## Shared preparation and exclusions (`series.py`)

`prepare_daily_series(metrics, events=None, segment=None, ...)` sums to one row per calendar day
(`sessions`, `orders`, `rate = orders / sessions`, `status`). Statuses: `ok`, `missing` (no rows),
`no_sessions` (zero sessions, so no rate), `excluded`.

**Task 2's default exclusions are applied.** Data quality is judged on the *whole* dataset (even
for a segment series), then flagged dates are removed. Every analysis discloses what was removed
(`result.exclusions`):

- the dates, flag kinds, number of flags and affected segments (`most_deviant_segment` is the
  flagged segment with the largest relative deviation; overlapping segments can tie, so it is not a
  localization);
- which analysis windows each excluded day fell in (`pre`, `post`, or `search`);
- incident context per day, labelled by how the overlap is known (below);
- days whose every flag rests on a **truncated baseline** (near either end of the data): a persistent
  behaviour change that began there could have been excluded by mistake (see
  `docs/data-quality.md`). A caution is added to the assumptions when such a day lies in a window;
- `apply_exclusions=False` turns exclusions off, and the disclosure then says the flagged days were
  **not** excluded.

**Incident context is context, not explanation.** A link that was `observed_on_flagged_day` is
worded as coinciding "in time" with flagged traffic and "not proof". A `historical_only` link
(shared traffic existed only earlier in the data) is labelled
`uncertain_historical_overlap_only`, its note says "Do not treat it as an explanation", and an
`uncertain_context_note` is added to the assumptions. Neither kind is ever used to decide anything.

## `confirm_change` (2.1–2.3)

```python
confirm_change(metrics, pre_window, post_window, events=None, *, segment=None, quality=None,
               apply_exclusions=True, n_bootstrap=2000, seed=0, level=0.95, alpha=0.05,
               min_days_per_window=7)
```

Windows are inclusive `(start, end)` dates; the pre window must end before the post window starts
(`ValueError` otherwise). `n_bootstrap >= 200`, `1e-12 <= alpha <= 0.5`.

- **Before / after** are pooled conversion rates (total orders ÷ total sessions) over each window's
  usable days, as fractions. Their **bootstrap intervals resample days** with replacement
  (percentile method, 95% by default; every label in the summary is generated from the `level`
  actually requested, so `level=0.8` prints "80% CI"). The **absolute change** (percentage points)
  gets its interval from the same resamples of both windows.
- **Relative change is withheld rather than approximated.** `after / before − 1` is unbounded in any
  resample whose 'before' window has zero orders, and dropping those draws would bias the interval
  toward values that look tidy. So if the observed 'before' rate is zero, or *any* resample has zero
  'before' orders, `rel_change` is `None` and `rel_change_unavailable_reason` says why (including how
  many of the resamples were affected); the summary says the relative change is not reported. The
  absolute change is unaffected. Sparse data (a few dozen orders per window) is where this happens. If a percentile interval would
  not contain its own point estimate (a strongly skewed bootstrap), it is widened to include it, and
  that is noted in the assumptions.
- **Noise test, on the same quantity.** The estimates are pooled rates (total orders ÷ total
  sessions), so the test targets the pooled rate too, with days as the sampling units. Each window's
  pooled rate has the ratio-estimator variance `n/(n−1) · Σ(orders_i − R·sessions_i)² / (Σ sessions)²`,
  and the difference is tested with a t statistic and Welch degrees of freedom
  (`welch_t_on_pooled_rate_ratio_estimator`). The **effect size** is the pooled change divided by the
  pre-period's own day-to-day sd (Requirement 2.2), taken session-weighted about the pooled rate, and a
  p-value is never reported without it. The estimate, bootstrap interval, t-test, effect size and the
  data-needed calculation therefore all describe one number. (An earlier version tested equal-weight
  means of daily rates while reporting pooled rates; with uneven daily traffic the two can disagree
  in size and even in direction, so the test could call an unchanged pooled rate "real".)
- **Verdict.** `is_real` only if the bootstrap interval for the absolute change excludes 0 **and**
  the t-test p-value is below `alpha`. If they disagree the result is *not distinguishable from
  noise* and the note says they disagree (the conservative reading). `stop_investigation` is true
  unless the change is real (Requirement 2.3).
- **How much more data (2.3).** When not real, `data_needed` gives the smallest pooled change
  detectable with 80% power at the current number of days, and the days per window needed to detect
  the *observed* pooled change (equal-size windows, the same ratio-estimator variances, which scale as
  1/n): both assume the observed variability persists. If the observed change is essentially zero it says more of the same data will not help.
- **Weekday mix.** The windows' weekday mixes are compared (total-variation distance); a distance
  over 0.1 is added to the assumptions because part of a difference could be weekday seasonality,
  which `confirm_change` does not remove.

**Insufficient data is explicit.** If either window has fewer than `min_days_per_window` usable
days after exclusions and missing dates, `status = "insufficient_data"`, no estimate or interval
is produced, and the summary states, per window, how many days were requested, usable, excluded as
flagged and missing, and which flagged dates were removed. Windows outside the data are
insufficient, not an error.

**Edge cases.** Windows with identical daily rates (zero variability, tested with a relative float
tolerance so rounding noise does not fake a spread) get `p = 0` if their pooled rates differ and
`p = 1` if not, with no effect size; infinite or NaN values never appear in results (JSON-safe).

## `detect_change_point` (3.1–3.2)

```python
detect_change_point(metrics, events=None, *, segment=None, search_window=None, quality=None,
                    apply_exclusions=True, adjust_weekdays=True, min_segment_days=7,
                    plausible_level=0.95, alpha=0.05, min_days_for_weekday_adjustment=28)
```

1. **Weekday adjustment (3.2).** Daily conversion is divided by multiplicative weekday factors.
   They are estimated *jointly* with the change point (fit the shift; take each weekday's mean ratio
   to the fitted level; divide out and refit until stable) so a shift is not mistaken for
   seasonality or the reverse. The factors and iteration count are returned. It is skipped, with the
   reason in the result and assumptions, when switched off, when fewer than
   `min_days_for_weekday_adjustment` days are usable, or when a weekday has fewer than 3 days.
2. **Best split.** One mean-shift change point minimising the two-segment squared error over all
   splits with at least `min_segment_days` usable days on each side (an exhaustive binary split,
   equivalent to a CUSUM scan for a single change). `best_date` is the first usable day of the new
   level.
3. **Plausible set (3.1).** Every start whose cost exceeds the best by at most
   `chi2_ppf(plausible_level, 1) × residual variance`, an approximate likelihood-ratio confidence
   set, so the set is as wide as the data's noise requires. Residual variance uses `n − 2` degrees of
   freedom (`− 6` more when weekday factors were fitted). `plausible_dates` is a sorted list of
   calendar dates, and `plausible_range` its first and last.
4. **Is a change supported?** The best split's t-statistic gets a Bonferroni correction over all
   admissible splits (`p_value_adjusted`; conservative because neighbouring splits are correlated).
   `change_detected` is `p_value_adjusted < alpha`. If not, the summary says no change point is
   supported and **no events are matched**.
5. **Unusable days.** Excluded or missing days are not used. If days just before a candidate start
   were unusable, the change could have begun on any of them, so they are added to the plausible
   dates and `gap_note` says so (for example when the very day the level changed was flagged).
6. **Insufficient data is explicit**: fewer than `2 × min_segment_days` usable days gives
   `status = "insufficient_data"` with the usable count and the removed flagged dates.

**Numerical safeguards.**

- *Stable arithmetic.* Split costs are computed on the centred series, so rounding error scales with
  the variation in the data rather than its level. (Computing `Σy² − (Σy)²/k` directly on identical
  values near 0.03 left residue far above the true cost of zero, which produced a "certain" change.)
- *Scale-aware equality.* Values closer than `REL_TOL = 1e-9` times the series magnitude are equal.
  A series with no variation (to that tolerance, including after the weekday pattern is removed)
  returns `status = "ok"`, `change_detected = False`, `best_date = None`, an empty plausible set,
  `p_value_adjusted = 1.0` and the summary "No change point exists"; no events are matched.
- *Perfect step.* A fit with no residual noise (cost within the same tolerance) makes the residual
  sd undefined: the shift is reported as `None` with `p = 0`, and only splits that also fit exactly are
  plausible. Never NaN or infinity.
- *Zero conversion.* Multiplicative weekday factors are undefined when a fitted level is zero or a
  weekday's average conversion is zero (all-zero series, a step from or to zero orders, a weekday with
  no orders). The fit then falls back to no weekday adjustment and the result says
  "fell back to no adjustment: ..." with the reason, instead of failing.

## `match_event_candidates` (3.3)

```python
match_event_candidates(change_point, events, *, lead_days=3)
```

Lists every event-log entry that coincides with the **computed** plausible dates:

| `match` | Meaning |
|---|---|
| `in_plausible_set` | its date is one of the plausible dates |
| `in_plausible_range` | between the first and last plausible date, but not itself plausible |
| `precedes_range_within_lead` | up to `lead_days` before the earliest plausible date |

Events after the plausible range are never listed (a later event cannot explain an earlier
shift). **`lead_days` is a policy parameter, not a statistical output.** The plausible set only
reflects sampling noise; a release can precede its visible effect through rollout, caching or a
staged exposure, so events shortly before the range are kept as candidates instead of being
silently dropped. The default of 3 days is a judgement; it is reported in the result, and
`lead_days=0` gives the strict reading of Requirement 3.3. Results are sorted by match kind, then
by distance from the best date.

Each candidate carries `days_from_best_date`, a note that it is a date coincidence "not evidence
that it caused the change" and "still has to be tested", and, for incidents: a reminder that a
data-collection problem must be ruled out first, whether the date was excluded as flagged, and the
incident context above (observed vs uncertain historical-only). With no supported change point, or
too little data, nothing is matched and the statement says why; an empty candidate list is also
stated explicitly.

## Measured behaviour and limits

Measured when the defaults were set (fixed seeds; synthetic series with weekday effects, 80 days
for the change-point tests and 56 days, 28 per window, for `confirm_change`; thresholds in the tests
include margin):

- Bounded fixtures (no sampling noise): a ≥ 15% step was located exactly in 700 of 700 draws
  (300 + 400 fresh draws); steps of 5–10% were also located exactly in 300 of 300.
- Noisy (binomial) series with no step: a change point was reported in 4 of 150 (about 3%); a
  `confirm_change` comparison was called real in 4 of 150.
- Noisy series with a step: detected in 150 of 150 at −15% and −8%; a 10% drop was confirmed in 100
  of 100 and a 5% drop in 97 of 100.
- **Plausible-set coverage.** Nominal 95%. Measured: the true change date was inside the set in
  144 of 150 (96%) at a 15% step but only 131 of 150 (87%) at an 8% step. It undercovers for small
  effects: treat it as an approximate likelihood-ratio set, not an exact interval.

On the reference scenario (seed 42, recorded as an example, not an expectation in code): the
overall series gives best date 2026-10-15 with plausible dates 2026-10-14 to 2026-10-15, because
the planted effect is confined to one segment and the overall level shift is small relative to daily
noise; the mobile Safari series alone gives a single plausible date. 2026-09-20 is excluded and
disclosed. The Checkout and Homepage-hero events are both candidates, the latter only through the
look-back (`lead_days`).

Limitations:

- **One change point.** Several shifts, trends, or gradual rollouts are not modelled; a trend can
  be reported as a shift.
- **Independence.** Days are treated as independent; autocorrelation would make intervals and
  p-values too optimistic.
- **Overall series only dilutes localized effects.** Use a `segment` series (Task 4 localizes) for a
  sharper date.
- **Windows are supplied.** `confirm_change` does not choose its windows; if the windows' weekday
  mixes differ, part of a difference may be seasonality.
- **Exclusions can remove real days.** Flagged days with truncated baselines near the ends of the
  data may be genuine post-change days; they are disclosed, not recovered. Volume anomalies of any
  cause above 40% are excluded as well.
- **Sparse data.** With very few orders per window the relative change is withheld (see above) and
  the t-test's normal approximation is weak; treat results on a few dozen orders as indicative.
- **Supported magnitude.** Counts and the total of either count column across all rows must not exceed
  2**53 − 1 (checked in the Task 2 loader, with a typed error), so daily, segment and window totals
  are exact and cannot wrap.
- **Defaults come from simulated data** and need re-checking on real data.

## Tests

- `tests/test_series.py`, `test_confirm_change.py`, `test_change_point.py`, `test_candidates.py`:
  unit tests, including independent recomputation of the pooled rates, disclosure of excluded days,
  insufficient-data paths, the uncertain historical-only context, gaps at the change, and wording.
  Regression tests for each PR #3 review finding (constant and zero-conversion series, a pooled
  estimand shared by estimate/test/effect size/power with independent recomputation, withheld relative
  intervals, interval labels, aggregate overflow) were each checked to fail on the previous commit.
- `tests/test_change_properties.py`: Hypothesis properties on **bounded** fixtures that hold for
  every draw (exact location of a large step, plausible set contains the best date and nests as the
  level rises, every interval ordered, determinism and row-order invariance, lowering post orders
  never raises the estimated change), and **statistical** tests with stated rates over fixed seeds.
