# Data loading, validation and data quality (Task 2)

Code: `causelens/analytics/loaders.py`, `causelens/analytics/data_quality.py`.
Requirements: 1.1–1.5. All functions are pure and deterministic and make no network or
LLM calls. Ground truth is never read by analytics; tests use it only for assertions.

## Loaders and validation

`load_metrics(path)` / `validate_metrics(df)` and `load_events(path)` / `validate_events(df)`
return typed, sorted copies (`date` is `datetime64`, counts are `int64`) or raise
`DataValidationError`, which names the file, row and column. Nothing partial is returned.

**Metrics are rejected for:** missing required columns; unparseable (non `YYYY-MM-DD`) dates;
blank dimension values; missing, non-integer, infinite or negative `sessions`/`orders`; counts
above `MAX_COUNT` (2**53 − 1, the largest integer a float64 holds exactly; checked *before*
conversion so large values can never wrap to negative); `orders > sessions`; duplicate
(date, segment) rows; no rows. **Events are rejected for:** a missing `date/type/name/scope`
column, an unknown `type` (not `release|campaign|incident`), or an unparseable scope.
`description` is optional.

**CSV structure.** Files are read with the `csv` module, not `pandas.read_csv`, so every
record must have exactly as many fields as the header. A row with an extra leading field (which
pandas would silently treat as an index), a short row, or an extra trailing field is a typed
error naming the line number. Blank lines are skipped, a UTF-8 BOM is accepted, and an empty
file, duplicate header names, undecodable bytes and unreadable paths are typed errors.

**Row labels.** Validation is positional, so frames with repeated or unsorted index labels
(for example from `pd.concat`) are fine. For files, error rows are CSV line numbers (header =
line 1); for frames they are the frame's own index labels (`row index 3`). At most 10 problems
are listed (the rest are counted). Extra metric columns are dropped. Scope `all` parses to `{}`;
`a=x,b=y` is a conjunction; unknown or repeated dimensions are errors.

## `check_data_quality(metrics, events=None, params=None)`

For each top-level segment (every value of each single dimension, e.g. `device=desktop`,
`browser=chrome`) the daily conversion is `orders / sessions` over summed rows, and daily
sessions are summed too.

**Baseline.** For day *t* the baseline is the median and MAD of the *other* days in a centred
window of `window_days` (7 before, 7 after). The day itself is excluded so an outlier cannot
hide in its own baseline.

**Conversion flag.** Flagged when `|x − median| > k · scale` **and**
`|x − median| / median ≥ min_relative_deviation`, where
`scale = max(1.4826·MAD, rel_scale_floor·|median|, sqrt(p(1−p)/baseline_sessions))`. The last
term is the binomial sampling noise of a day's rate; it stops small segments being flagged for
chance variation.

**Sessions-normal check.** Sessions use the same baseline (without the binomial term). They are
*abnormal* only if `|x − median| > sessions_k · scale` **and** the relative distance is ≥
`sessions_min_relative_deviation`.

| Conversion | Sessions | Flag kind |
|---|---|---|
| outside baseline | normal | `possible_tracking_issue` |
| outside baseline | abnormal | `conversion_shift_with_abnormal_sessions` (traffic changed; not called tracking) |
| normal (or no rate) | abnormal | `volume_anomaly` |
| — | — | `missing_date` (no rows for a date inside the range) |

A `volume_anomaly` covers a day whose sessions are far from their baseline while conversion
looks normal, including a zero-session day. Its reason says the cause is not established: it may
be a collection gap or a genuine traffic change.

### Default parameters (`DetectorParams`)

| Parameter | Default | Meaning |
|---|---|---|
| `window_days` | 15 | centred window; must be odd; 7 days each side |
| `min_baseline_days` | 7 | fewest usable baseline days to evaluate a day; must be ≤ `window_days − 1` |
| `k` | 5.0 | robust-z threshold for conversion |
| `rel_scale_floor` | 0.01 | scale ≥ 1% of median (guards MAD = 0) |
| `sampling_noise_floor` | true | add the binomial-noise term to the conversion scale |
| `min_relative_deviation` | 0.10 | conversion must also be ≥ 10% from its median |
| `sessions_k` | 5.0 | robust-z threshold for sessions |
| `sessions_min_relative_deviation` | 0.40 | sessions must also be ≥ 40% from their median |
| `incident_tolerance_days` | 0 | incident links when dates differ by ≤ this |

`DetectorParams` validates relationships on construction: an even `window_days`, or a
`min_baseline_days` larger than the `window_days − 1` neighbours a window can hold (which would
silently disable detection), raises `ValueError`.

**Calibration, honestly stated.** `k`, `min_relative_deviation` and the binomial floor were
chosen on the simulator. `sessions_min_relative_deviation` is 0.40 because legitimate traffic
swings (weekday pattern plus day-level noise) reached 0.30 relative to baseline across 100
simulator seeds, so the earlier 0.25 produced false volume flags on Saturdays at the ends of
the series. The consequence is that sessions changes smaller than 40% are *not* reported.
On simulator seeds 1–100 the 2026-09-20 desktop day was flagged in every seed, and one seed
(85) produced one extra chance conversion flag on another day, a false-alarm rate of about 1%
per dataset. Tune all of these for real data; they are not universal.

### Edge-case conventions

- **Missing dates.** Dates between the first and last date with no rows are reported in
  `missing_dates` and as `missing_date` flags (segment `all`). Dates outside that range are
  unknowable and not reported. A missing day is not a zero-session day.
- **Zero-session day.** No conversion rate (NaN; `observed_rate` is `None`); it is not used in
  conversion baselines, but its sessions (0) are compared with the sessions baseline and, when
  the baseline is large, reported as a `volume_anomaly`.
- **Zero baseline rate** (median 0): conversion is not evaluated; there is no relative scale.
- **Zero scale.** If the baseline has no spread and both floors are disabled (or the rate is
  exactly 1), any non-zero deviation counts as beyond `k` and a zero deviation never does;
  `robust_z` is `None` ("undefined: the baseline has zero spread") rather than NaN or infinity.
  Constant data is therefore never flagged.
- **Short history.** Fewer than `min_baseline_days` usable neighbours: not evaluated.
- **Direction.** Both drops and spikes are flagged; the reason text says which.
- **Row order and labels.** Results do not depend on input row order.
- **One cause, many flags.** A tracking loss in one segment also depresses every segment that
  contains it (device=desktop also trips browser=chrome, channel=*, …). All are reported. The
  most specific, most deviant flag is the best localizer; later stages decide.

### Limitation: persistent changes near the ends of the data

The centred window makes a *persistent* change (such as a release) hard to mistake for a
one-day tracking blip, but only where the baseline is complete. For a single persistent step,
a day with 7 usable days on each side cannot be flagged: at most 7 of its 14 neighbours lie on
the other side of the step, so the median stays on the day's own side, or sits mid-gap with
the MAD equal to the half gap (|z| < 1). A property test checks this.

That guarantee **does not hold near the start or end of the series**. When a change begins in
the last few days there are too few post-change days for the baseline to see it persist, so
those post-change days **are flagged as possible tracking issues and excluded by default**.
With a 40-day series and a persistent drop, 3 post-change days gave 3 flags, and a step with 8
post-change days gave none. To make this visible, every flag records `baseline_days_before`,
`baseline_days_after` and `baseline_truncated` (fewer than `window_days // 2` usable days on
either side), its reason says so, and `report.truncated_baseline_dates`,
`ExclusionSummary.truncated_baseline_dates` and the exclusion statement name excluded dates
whose flags all rest on truncated baselines. Treat those dates as *unconfirmed*: a real
tracking loss on the final days of the data looks identical to a behaviour change that has
just begun, and the detector cannot tell them apart.

## Incident linkage

An `incident` event links to a flag when `|flag date − incident date| ≤ incident_tolerance_days`
(default: same day; the schema has no duration) **and the incident's scope and the flagged
segment share real traffic**: at least one row with `sessions > 0` in the dataset satisfies both
the segment (`dimension=value`) and every scope term. Scope `all` shares traffic with every
segment. A `device=mobile` incident therefore does not link to `browser=edge` when the data has
no mobile + Edge rows, even though the two terms never directly contradict each other. The link
records `scope_overlap` (`all_visitors`, `same_segment` or `overlapping_scope`), the
`overlap_sessions` on the flagged day inside both segment and scope, and
`segment_share_in_scope` (that overlap divided by the segment's sessions that day). Only the
first matching incident (by date, then name) is linked.

A link is a **coincidence in time and scope, not evidence of causation**. The report text says
"possible tracking issue" and "not proof", per `.kiro/steering/scientist-rules.md`. Confirming
the cause needs the incident owners' logs or an independent source.

## Default exclusions (Requirement 1.5)

Days with `possible_tracking_issue`, `conversion_shift_with_abnormal_sessions` or
`volume_anomaly` flags are excluded from downstream estimates by default; `missing_date` flags
have nothing to remove. Excluding volume anomalies is conservative: a genuine traffic surge
(for example a campaign) with more than 40% change would also be excluded, and the flag says
the cause is not established. `report.excluded_dates` and `report.exclusion_statement` say so,
and `apply_default_exclusions(metrics, report, granularity="date")` returns the filtered frame
plus an `ExclusionSummary` (dates, truncated-baseline dates, rows removed, statement).

- `granularity="date"` (default) removes all rows on a flagged date, so segment mix on the
  remaining days is not distorted by a partly removed day.
- `granularity="segment_day"` removes only rows in the flagged `dimension=value` segments.
  Because one outage often trips several overlapping segments, this can remove most or all
  of the day anyway.

## Tests

- `tests/test_loaders.py`, `tests/test_data_quality.py`: unit and regression tests (loader
  errors, count range, CSV structure, repeated index labels, reference-data flags, incident
  population overlap, parameter validation, zero scale, volume anomalies, end-of-series limits).
- `tests/test_data_quality_property.py`: two kinds of test, kept apart on purpose.
  - *Guaranteed* Hypothesis properties use **bounded** fixtures whose value ranges make the
    outcome arithmetic (the bounds and the argument are in the fixture's docstring): an order
    loss of 50–100% on one segment-day is always flagged and only that day; bounded clean data is
    never flagged; a persistent step only ever flags days with a truncated baseline.
  - *Statistical* tests use binomial fixtures and assert stated error rates over fixed seeds:
    at most 2% of clean series raise any flag (measured 0/300), and detection of an injected
    loss on the noisiest setting (3% conversion, 8,000 sessions) is at least 97% at 50% loss
    (measured 299/300) and at least 99% at 70% (measured 300/300). A single binomial draw can
    miss at 50% loss, since the robust z-score there is only about 5, so no per-draw guarantee
    is claimed.
