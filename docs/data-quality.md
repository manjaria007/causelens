# Data loading, validation and data quality (Task 2)

Code: `causelens/analytics/loaders.py`, `causelens/analytics/data_quality.py`.
Requirements: 1.1–1.5. All functions are pure and deterministic and make no network or
LLM calls. Ground truth is never read by analytics; tests use it only for assertions.

## Loaders and validation

`load_metrics(path)` / `validate_metrics(df)` and `load_events(path)` / `validate_events(df)`
return typed, sorted copies (`date` is `datetime64`, counts are `int64`) or raise
`DataValidationError`, which names the file, row and column. Nothing partial is returned.

Metrics are rejected for: missing required columns; unparseable (non `YYYY-MM-DD`) dates;
blank dimension values; missing, non-integer or negative `sessions`/`orders`;
`orders > sessions`; duplicate (date, segment) rows; no rows. Events are rejected for a
missing `date/type/name/scope` column, an unknown `type` (not `release|campaign|incident`),
or an unparseable scope. `description` is optional.

Conventions: for files, row numbers are CSV line numbers (header = line 1); for frames they
are index labels. At most 10 problems are listed (the rest are counted). Extra metric columns
are dropped. Scope `all` parses to `{}`; `a=x,b=y` is a conjunction; unknown or repeated
dimensions are errors.

## `check_data_quality(metrics, events=None, params=None)`

For each top-level segment (every value of each single dimension, e.g. `device=desktop`,
`browser=chrome`) the daily conversion is `orders / sessions` over summed rows.

**Baseline.** For day *t* the baseline is the median and MAD of the *other* days in a
centred window of `window_days` (7 before, 7 after). The day itself is excluded so an
outlier cannot hide in its own baseline. A centred window also means a persistent step
(such as a release) has the step on both sides of the window, so it is not mistaken for a
one-day tracking blip.

**Flag rule.** Flag when all hold:
`|x − median| > k · scale`, `|x − median| / median ≥ min_relative_deviation`, where
`scale = max(1.4826·MAD, rel_scale_floor·|median|, sqrt(p(1−p)/baseline_sessions))`.
The last term is the binomial sampling noise of a day's rate; it stops small segments being
flagged for chance variation.

**Sessions-normal check.** Sessions use the same baseline. They are *abnormal* only if
`|z| > sessions_k` **and** the relative distance is ≥ `sessions_min_relative_deviation`
(weekday traffic swings of ±15% are normal). Normal sessions → kind
`possible_tracking_issue`. Abnormal sessions → kind
`conversion_shift_with_abnormal_sessions`: traffic changed, so it is *not* called tracking.

### Default parameters (`DetectorParams`)

| Parameter | Default | Meaning |
|---|---|---|
| `window_days` | 15 | centred window; 7 days each side |
| `min_baseline_days` | 7 | fewest usable baseline days to evaluate a day |
| `k` | 5.0 | robust-z threshold for conversion |
| `rel_scale_floor` | 0.01 | scale ≥ 1% of median (guards MAD = 0) |
| `sampling_noise_floor` | true | add the binomial-noise term to the scale |
| `min_relative_deviation` | 0.10 | and at least 10% from the median |
| `sessions_k` | 5.0 | robust-z threshold for sessions |
| `sessions_min_relative_deviation` | 0.25 | and at least 25% from the median |
| `incident_tolerance_days` | 0 | incident links when dates differ by ≤ this |

These were chosen on the reference scenario and checked on seeds 1–40 of the simulator:
the 2026-09-20 desktop day is flagged every time and no other day is flagged. They are
not universal; tune them for real data.

### Edge-case conventions

- **Missing dates.** Dates between the first and last date with no rows are reported in
  `missing_dates` and as `missing_date` flags (segment `all`). Dates outside that range
  are unknowable and not reported. A missing day is not a zero-session day.
- **Zero-session day.** No conversion rate (NaN); not flagged for conversion and not used
  in baselines. (Sessions still enter the sessions baseline as 0.)
- **Zero baseline rate** (median 0): not evaluated; there is no relative scale.
- **Short history.** Fewer than `min_baseline_days` usable neighbours: not evaluated.
  Days near the start/end of the series use a one-sided window, so a clean baseline needs
  at least 7 usable days around the target.
- **Direction.** Both drops and spikes are flagged; the reason text says which.
- **Row order and labels.** Results do not depend on input row order.
- **One cause, many flags.** A tracking loss in one segment also depresses every segment
  that contains it (device=desktop also trips browser=chrome, channel=*, …). All are
  reported. The most specific, most deviant flag is the best localizer; later stages
  decide.

## Incident linkage

An `incident` event links to a flag when `|flag date − incident date| ≤ incident_tolerance_days`
(default: same day; the schema has no duration) and the scopes are compatible: scope `all`
overlaps everything; otherwise the scope must not name a different value for the flag's
dimension (`device=desktop` does not link to `device=mobile`, but may link to
`browser=chrome`). `scope_overlap` records `all_visitors`, `same_segment` or
`overlapping_scope`. Only the first matching incident (by date, then name) is linked.

A link is a **coincidence in time and scope, not evidence of causation**. The report text
says "possible tracking issue" and "not proof", per
`.kiro/steering/scientist-rules.md`. Confirming the cause needs the incident owners' logs or
an independent source.

## Default exclusions (Requirement 1.5)

Days with `possible_tracking_issue` or `conversion_shift_with_abnormal_sessions` flags are
excluded from downstream estimates by default; `missing_date` flags have nothing to remove.
`report.excluded_dates` and `report.exclusion_statement` say so, and
`apply_default_exclusions(metrics, report, granularity="date")` returns the filtered frame
plus an `ExclusionSummary` (dates, rows removed, statement).

- `granularity="date"` (default) removes all rows on a flagged date, so segment mix on the
  remaining days is not distorted by a partly removed day.
- `granularity="segment_day"` removes only rows in the flagged `dimension=value` segments.
  Because one outage often trips several overlapping segments, this can remove most or all
  of the day anyway.

## Tests

- `tests/test_loaders.py`, `tests/test_data_quality.py`: unit tests (loader errors,
  reference-data flags, linkage, exclusions, edge cases).
- `tests/test_data_quality_property.py`: Hypothesis property — zeroing 50–90% of one
  segment's orders on one day (with ≥ 10 clean days either side, 8k–20k daily sessions,
  3–6% conversion) always flags that segment-day; clean data produces no flags.
