---
inclusion: always
---

# Scientist rules

These rules govern every agent prompt, every tool result shape, and every sentence CauseLens
shows a user. They are product requirements, not style advice.

## Claims
- Never state or imply causation from a correlation, coincidence in time, or segment
  concentration alone. Use "coincides with", "is concentrated in", "is consistent with".
- A causal claim ("X caused about N of the drop") is allowed only when it comes from a named
  identification design (difference-in-differences, interrupted time series, synthetic
  control, or a randomized experiment) and that design's checks passed.
- Every causal claim lists its assumptions in plain language next to the number
  (e.g. "assumes Chrome mobile would have trended like Safari mobile without the release").
- Report the strength of evidence as one of: `strong`, `moderate`, `weak`, `insufficient`,
  with the reason. Never omit it.

## Numbers
- All arithmetic, tests, intervals and estimates come from deterministic analytics tools.
  The LLM never computes, rounds or invents a number; it only quotes tool outputs.
- Every estimate carries an interval (default 95%) and the sample size it rests on.
- Report effects in the metric's own units (percentage points for rates), plus relative
  change where helpful. Never report a p-value without the effect size.
- If a test was run, its result is reported even when it is inconvenient.

## Investigation discipline
- Always consider at least one alternative explanation besides the leading one, and say
  why it was kept or rejected.
- Account for multiple comparisons when scanning many segments (control false discovery
  rate) and say so.
- Show what part of the change remains unexplained. Never force attributions to sum to 100%.
- Check data quality (missing days, tracking drops, volume anomalies) before blaming
  behaviour. A tracking break is a valid explanation.

## When evidence is insufficient
- Say so plainly and recommend the experiment that would settle it: hypothesis, unit of
  randomization, primary metric, guardrail metrics, minimum detectable effect, sample size
  and expected duration.
- Never fill a gap with a plausible-sounding story.

## Code
- Analytics functions are pure and deterministic given a seed. No network or LLM calls
  inside an analytics tool.
- Every analytics function has property-based tests for its invariants
  (see `.kiro/specs/metric-investigation/design.md`, "Correctness properties").
