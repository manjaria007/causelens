# Design: Metric Investigation

## Overview

Reasoning and computation are split. Agents decide *what* to analyse next; deterministic
analytics tools (exposed over MCP) compute every number. The investigation is a fixed
pipeline of stages with an LLM layer that chooses parameters, interprets tool results and
writes the report under the scientist rules.

```
             ┌─────────────── Investigation orchestrator ───────────────┐
 question ─▶ │ validate → confirm → change point → localize → test →   │ ─▶ report
             │ estimate → recommend                                      │
             └──────────────┬────────────────────────────────────────────┘
                            │ MCP tool calls (logged)
             ┌──────────────▼────────────────────────────────────────────┐
             │ causelens.analytics  (pure, deterministic, property-tested)│
             └──────────────┬────────────────────────────────────────────┘
                            │
                 metrics_daily.csv  ·  events.csv
```

## Agents (roles)

Keep to three roles; each is a prompt + an allowed tool subset, not a separate service.

| Role | Stages | Tools |
|---|---|---|
| Detective | validate, confirm, change point, localize | `check_data_quality`, `confirm_change`, `detect_change_point`, `decompose_change` |
| Scientist | test candidates, estimate | `test_event_scope`, `estimate_did`, `run_placebo`, `check_pre_trends`, `project_impact` |
| Experiment designer | recommend | `power_analysis`, `design_experiment` |

The orchestrator runs the stages in order and passes a shared `InvestigationState`. Every
tool call and result is appended to `state.log` (Requirement 8.2).

## Data model

- `metrics_daily.csv`: `date, device, browser, channel, customer_type, sessions, orders`.
  Conversion rate is always computed as `orders / sessions`, never stored.
- `events.csv`: `date, type (release|campaign|incident), name, scope, description`.
  `scope` is `all` or `dim=value[,dim=value]` and is the event's *exposure scope* (who it could
  have touched), not a prediction of where conversion moves. `expected_pattern` is an optional
  per-candidate input (default `unspecified`); it is declared by the user and never inferred
  from names or descriptions.

## Analytics tools

| Tool | Input | Output | Method |
|---|---|---|---|
| `check_data_quality` | metrics, events | flagged (date, segment, reason, linked_event) | rolling median ± k·MAD on daily rate per top-level segment; sessions-normal check |
| `confirm_change` | metric, pre/post windows | before, after, abs/rel change with CI; is_real | daily rates; Welch test on day-level values; bootstrap CI by resampling days |
| `detect_change_point` | daily series | best date, plausible set | deseasonalize by weekday means; binary segmentation / CUSUM; plausible set from cost within threshold |
| `decompose_change` | metrics, windows, dims | per-segment rate effect, mix effect, residual, BH-adjusted q | rate/mix (shift-share) decomposition; two-proportion tests per segment |
| `test_event_scope` | event, onset set, decomposition, focal differential series, `expected_pattern` (default `unspecified`), `lag_days` (default 0) | verdict + hypothesis + evidence | exposure check, declared-pattern test, location and relative-timing layers (see Candidate testing) |
| `estimate_did` | treated segment, control segment, event date | effect pp, CI, n | WLS `cr ~ treated:post + treated + C(date)`, weights = sessions, HC1 (or cluster by date) SEs |
| `check_pre_trends` | same | slope difference + p | pre-period `cr ~ treated * t` |
| `run_placebo` | same, fake dates | placebo effects | DiD at fake dates within pre-period |
| `project_impact` | DiD result, post traffic | overall pp, lost orders, intervals | scale segment effect by segment share; propagate CI |
| `power_analysis` | baseline rate, MDE, alpha, power | n per arm | two-proportion normal approximation |
| `design_experiment` | investigation state | experiment spec | template + `power_analysis` + observed traffic |

Note on `estimate_did`: with date fixed effects, the `post` main effect is collinear and must
be dropped; only `treated` and `treated:post` enter alongside `C(date)`. HC1 and date-clustered
variances remain the options for ordinary multi-day DiD coefficients. They do not apply to a
timing contrast identified from a single date (that date's residual is zero); those contrasts
use the pooled variance described under Candidate testing.

## Candidate testing

**Exposure vs expected pattern.** Exposure scope `all` can never reject by location. A
declared `expected_pattern` is `uniform` (scale `relative`, the default, or `absolute`) or
`restricted(scope)`. Non-significance in a segment is never evidence of no effect.

**Estimand.** The focal segment is the localized segment (Task 4) and the comparison is all
other traffic. The estimand is the *differential* effect on the focal-minus-comparison
conversion difference. It is a release's effect only if the comparison was unaffected; that
assumption is stated beside every estimate and every `project_impact` figure.

**Declared patterns are tested directly.**
- `uniform`: an omnibus Wald test of equal changes across segments, per single-dimension
  partition, using the full covariance of the segment estimates from shared days (days are the
  units), Holm across the four partitions. Segments with a zero rate or fewer than 5 pooled
  orders in either window are excluded and listed; a partition with fewer than 2 remaining
  is skipped; if all are skipped the pattern is untested and contributes nothing.
- `restricted(scope)`: contradicted when the effect outside the scope has a confidence interval
  excluding 0. Absence of an outside effect is claimed only within a user-declared
  equivalence margin; with no margin, absence is never claimed.

**Timing.** Candidate k has the hypothesis H_k(l): the whole localized change begins at
date_k + l (default l = 0). Two layers:
1. *Location:* is date_k + l in the plausible onset set (Task 3) of the focal series? That set
   is approximate (it undercovers for small effects), so exclusion blocks `supported` and
   gives `inconclusive`; it never rejects.
2. *Relative timing:* a nested step model on the focal differential,
   `D_t = mu + sum_j beta_j * 1[t >= d_j + l] + e_t`, over the candidates in the onset window.
   H_k(l) implies beta_j = 0 for every j != k. With X the step model's design matrix over its
   n usable days, the pooled variance is `SSE / (n - rank(X))`; a single contrast is tested with
   t on `n - rank(X)` degrees of freedom and a joint restriction on q coefficients with F on
   `(q, n - rank(X))`, always using the full covariance of the estimates, with Holm across
   candidates. Rejection means "not the sole immediate source".
   The share of the localized change beginning at each candidate's onset date is reported as a
   Fieller confidence set, which can be bounded, disconnected or unbounded. It describes
   calendar steps under the lag model, not a release's causal share. It is `unavailable`
   (unidentifiable), with a reason, when the contrast cannot be estimated: candidate onset
   columns coincide, no usable day separates two onsets, or there are no residual degrees of
   freedom. No coefficient, bound or share is invented in those cases and the affected
   candidates are `inconclusive`.

**Lag sensitivity.** Each candidate's lag is evaluated independently over {0, 1} while the
others keep their declared lags, and the verdict at each is reported. Release attribution is
`ambiguous` if some allowed lag of one candidate and some allowed lag of another give the same
onset date inside the onset set, for example the earlier event at lag 1 and the next-day event
at lag 0. Their step columns then coincide, the data cannot separate them, and strength is
capped at `moderate`.

**Working assumptions.** The contrast assumes independent, homoskedastic day-level noise and
needs at least 14 usable days before and 7 after the first candidate date. Diagnostics
(Ljung-Box on the residuals over lags 1-7; a pre/post variance-equality test) are run.
Passing them does not validate the assumptions; a failure, or too few days to run them, makes
the timing verdict `inconclusive`.

**DiD check criteria (prespecified).** Pre-trends: pass if p > 0.10, borderline if
0.05 <= p <= 0.10, fail if p < 0.05. Placebos (fake dates inside the pre-period, Holm across
dates): fail if any adjusted p < 0.05. A check that was not run, or cannot be evaluated, does
not count as passed.

**Verdicts.**
- `rejected` only if the localized change is disjoint from the exposure scope, or a declared
  pattern is contradicted, or the timing contrast rejects H_k(l) at its stated level. Rejection
  of the named sole-source hypothesis takes precedence over any evidence that the event might
  explain some contribution.
- `supported` only if all hold: (a) event_date + evaluated lag is in the onset set; (b) the
  candidate's step is significant, in the direction of the observed change, after Holm
  adjustment across the candidate step tests (the adjustment and level are stated with the
  verdict); (c) the timing diagnostics were performed, are evaluable and did not fail; (d) the
  DiD pre-trend and placebo checks were performed, are evaluable and did not fail; (e) no
  declared pattern is contradicted; and (f) the sole-source hypothesis was not rejected. One
  surviving candidate is not sufficient.
- otherwise `inconclusive`.
Headlines state the evaluated lag l. At l = 0: "supported under the immediate-effect assumption
(lag 0)" and "rejected as the sole immediate explanation (lag 0)". At l > 0: "supported assuming
the effect begins l day(s) after the event (lag l)" and "rejected as the sole explanation if the
effect begins l day(s) after the event (lag l)".

## Evidence strength

Strength is assigned in two steps. Step 2 can only lower it.
1. From the estimate and the required checks alone (pre-trends, placebos, the timing
   diagnostics, exposure and declared-pattern consistency; a check that was not run or cannot
   be evaluated counts as not passed):
   `strong`: valid comparison segment, DiD CI excludes 0, pre-trends p > 0.1, all placebos
   non-significant, all required checks run and passed.
   `moderate`: valid comparison, CI excludes 0, but one check is borderline.
   `weak`: CI excludes 0 but a check fails or was not run.
   `insufficient`: CI includes 0 or no valid control segment.
2. If release attribution is `ambiguous`, strength is capped at `moderate`, that is
   min(step 1, `moderate`). Ambiguity never raises `weak` or `insufficient`.

## Correctness properties (property-based tests with hypothesis)

1. **Decomposition closes:** sum of rate effects + mix effects + residual equals the
   overall change to within 1e-9.
2. **Interval ordering:** every CI has `lower <= estimate <= upper`.
3. **Null calibration:** on data generated with no planted effect, DiD 95% CIs cover 0 in
   roughly 95% of seeds (accept 88–100% over 50 seeds).
4. **Monotonicity:** scaling down treated post-period orders never increases the DiD estimate.
5. **Invariance:** relabelling segments or reordering rows does not change any result.
6. **Power monotonicity:** required n per arm decreases as MDE increases, increases as
   power increases.
7. **Data quality:** zeroing a share of one segment's orders on one day gets that day
   flagged.
8. **Attribution safety:** a candidate is never `supported` when event_date + evaluated_lag
   is outside the onset set, and onset-set exclusion alone cannot cause `rejected`
   (disjointness from the exposure scope remains a separate rejection ground).

## Error handling

- Invalid input → typed error with the offending column/row; never a partial result.
- No valid control segment → `estimate_did` returns `insufficient` with reason, and the
  pipeline routes straight to `design_experiment`.
- A timing verdict whose working assumptions cannot be supported (too few days, failed
  diagnostics) → `inconclusive`, naming the assumption.

## Testing

- Unit tests per tool on hand-built small frames with known answers.
- Property tests above.
- Candidate-testing validation through the full selection pipeline (confirm, localize,
  candidates, verdicts), reporting incorrect-attribution rates as well as recovery, for: the
  planted cause, a swapped true cause, delayed effects, indistinguishable events, weak
  effects, a change not tied to any logged event, and no change. Wording and ambiguity flags
  are asserted, not only labels.
- End-to-end evaluation (Requirement 10): `python -m causelens.eval --seeds 20`.
