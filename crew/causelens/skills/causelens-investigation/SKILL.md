---
description: How to run a CauseLens metric investigation with the three CauseLens agents, and how to report it without overclaiming
always: false
---

# CauseLens investigation

Use this when someone asks why a business metric (for example checkout conversion) moved. CauseLens does not answer from knowledge: it runs an
investigation with read-only deterministic tools, and every number in the answer comes from a recorded tool result.

## The three agents

Run them in this order, passing each the previous one's findings as quoted text (never recomputed):

1. **causelens-detective**: data quality, whether the change is real, when it started, where it is concentrated.
2. **causelens-scientist**: tests each logged event against a named hypothesis at its stated lag; estimates the effect of a supported one.
3. **causelens-experiment-designer**: the randomized experiment that would settle what the data cannot.

Give each the question, the dataset id (`reference`), and what the earlier agents found: the two windows, the excluded dates, the localized
segment. They may call only their own tools.

## What the final answer must contain

* The change with its interval and sample size, when it started (as a range), and where it is concentrated, described as location only.
* Every candidate event with its verdict **and the lag that verdict is about**, and at least one alternative explanation with why it was kept or set aside.
* A causal statement only if a candidate is `supported` and its difference-in-differences checks passed, worded as the tools' differential effect under
  the listed assumptions. Say that it is not proof, and what the evidence strength is (`strong`, `moderate`, `weak` or `insufficient`) with the tool's reasons.
* The data-quality warning (excluded days and why), what remains unexplained, and the recommended experiment with its sample size and duration.
* If the tools cannot support a causal claim, say so and give the experiment; do not name a likely cause.

Never state a number that no tool returned. Never read or guess at an answer key; there is none in this app.
