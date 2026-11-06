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
  `scope` is `all` or `dim=value[,dim=value]`.

## Analytics tools

| Tool | Input | Output | Method |
|---|---|---|---|
| `check_data_quality` | metrics, events | flagged (date, segment, reason, linked_event) | rolling median ± k·MAD on daily rate per top-level segment; sessions-normal check |
| `confirm_change` | metric, pre/post windows | before, after, abs/rel change with CI; is_real | daily rates; Welch test on day-level values; bootstrap CI by resampling days |
| `detect_change_point` | daily series | best date, plausible set | deseasonalize by weekday means; binary segmentation / CUSUM; plausible set from cost within threshold |
| `decompose_change` | metrics, windows, dims | per-segment rate effect, mix effect, residual, BH-adjusted q | rate/mix (shift-share) decomposition; two-proportion tests per segment |
| `test_event_scope` | event, decomposition | verdict + evidence | event scope vs segments with significant rate effects |
| `estimate_did` | treated segment, control segment, event date | effect pp, CI, n | WLS `cr ~ treated:post + treated + C(date)`, weights = sessions, HC1 (or cluster by date) SEs |
| `check_pre_trends` | same | slope difference + p | pre-period `cr ~ treated * t` |
| `run_placebo` | same, fake dates | placebo effects | DiD at fake dates within pre-period |
| `project_impact` | DiD result, post traffic | overall pp, lost orders, intervals | scale segment effect by segment share; propagate CI |
| `power_analysis` | baseline rate, MDE, alpha, power | n per arm | two-proportion normal approximation |
| `design_experiment` | investigation state | experiment spec | template + `power_analysis` + observed traffic |

Note on `estimate_did`: with date fixed effects, the `post` main effect is collinear and must
be dropped; only `treated` and `treated:post` enter alongside `C(date)`.

## Evidence strength

`strong`: DiD CI excludes 0, pre-trends p > 0.1, all placebos non-significant, change
localized to the event's predicted scope.
`moderate`: CI excludes 0 but one check is borderline.
`weak`: CI excludes 0 but a check fails.
`insufficient`: CI includes 0 or no valid control segment.

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

## Error handling

- Invalid input → typed error with the offending column/row; never a partial result.
- No valid control segment → `estimate_did` returns `insufficient` with reason, and the
  pipeline routes straight to `design_experiment`.

## Testing

- Unit tests per tool on hand-built small frames with known answers.
- Property tests above.
- End-to-end evaluation (Requirement 10): `python -m causelens.eval --seeds 20`.
