# Requirements: Metric Investigation

## Introduction

CauseLens investigates why a business metric changed. Given daily segment-level metrics and
an event log (releases, campaigns, incidents), it confirms the change, localizes it,
tests candidate explanations with deterministic statistical tools, estimates impact with
uncertainty, and recommends an experiment when the evidence can't support a causal claim.

The reference scenario is the synthetic dataset in `data/` with a planted cause
(Checkout v4.2, mobile Safari), a decoy (Homepage hero refresh) and a tracking outage
(2026-09-20, desktop). `data/ground_truth.json` defines what a correct investigation finds.

## Requirements

### Requirement 1: Load and validate data

**User Story:** As an analyst, I want CauseLens to check my data before analysing it, so
that a broken tracking day is not mistaken for a behaviour change.

#### Acceptance Criteria
1. WHEN a metrics file is loaded THE SYSTEM SHALL validate required columns (`date`,
   segment dimensions, `sessions`, `orders`) and reject files with negative counts or
   `orders > sessions`.
2. WHEN a date is missing from the series THE SYSTEM SHALL report it as a data-quality issue.
3. WHEN a day's conversion in any top-level segment deviates from its robust baseline
   (median ± k·MAD over a rolling window) while sessions are normal THE SYSTEM SHALL flag
   it as a possible tracking issue, naming the segment and date.
4. WHEN a flagged day overlaps an `incident` in the event log THE SYSTEM SHALL link the two.
5. THE SYSTEM SHALL exclude flagged days from downstream estimates by default and state
   that it did so.

### Requirement 2: Confirm the change is real

**User Story:** As a product manager, I want to know whether a drop is real or noise
before anyone spends a day on it.

#### Acceptance Criteria
1. WHEN the user asks about a metric over a comparison window THE SYSTEM SHALL report the
   before and after values, the absolute change (pp) and relative change, each with a 95% CI.
2. THE SYSTEM SHALL test whether the change exceeds normal day-to-day variation using the
   pre-period's own variability, and report the result with its effect size.
3. IF the change is not distinguishable from noise THEN THE SYSTEM SHALL say so and stop
   the investigation, suggesting how much more data would be needed.

### Requirement 3: Locate when it started

**User Story:** As an analyst, I want the most likely start date of the change, so I can
line it up against what shipped.

#### Acceptance Criteria
1. THE SYSTEM SHALL estimate a change point for the metric with an uncertainty range (e.g.
   a set of plausible dates), not just a single date.
2. WHEN the series has weekly seasonality THE SYSTEM SHALL account for it before
   detecting the change point.
3. THE SYSTEM SHALL list every event-log entry within the change point's plausible range
   as a candidate explanation.

### Requirement 4: Localize where it happened

**User Story:** As an analyst, I want to know which segments drive the change, so I can
tell a broad shift from a local break.

#### Acceptance Criteria
1. THE SYSTEM SHALL decompose the overall change into contributions by segment for each
   dimension and for pairs of dimensions (e.g. device × browser).
2. THE SYSTEM SHALL separate each segment's contribution into a **rate effect** (its
   conversion changed) and a **mix effect** (its share of traffic changed).
3. Segment contributions plus mix effects SHALL sum to the overall change (within rounding),
   and any residual SHALL be shown as unexplained.
4. WHEN many segments are tested THE SYSTEM SHALL control the false discovery rate
   (Benjamini–Hochberg) and report adjusted significance.
5. THE SYSTEM SHALL rank segments by contribution and report the smallest set of segments
   that explains most of the change.

### Requirement 5: Test candidate explanations

**User Story:** As a product manager, I want each plausible cause tested, so the team
doesn't fix the wrong thing.

#### Acceptance Criteria
1. FOR EACH candidate event THE SYSTEM SHALL check whether the change's location matches
   the event's scope (an event that touches all visitors should move all segments).
2. WHEN an event's scope predicts an effect in segments that show none THE SYSTEM SHALL
   mark the event as rejected and state the evidence.
3. THE SYSTEM SHALL give every candidate a verdict (`supported`, `rejected`, `inconclusive`)
   with the reason.
4. THE SYSTEM SHALL always consider at least one alternative to the leading explanation.

### Requirement 6: Estimate causal impact

**User Story:** As a growth lead, I want a number for how much the cause cost us, with an
honest range and the assumptions behind it.

#### Acceptance Criteria
1. WHEN a supported candidate has an affected segment and a plausible unaffected comparison
   segment THE SYSTEM SHALL estimate its effect with difference-in-differences, reporting
   the estimate in pp, a 95% CI and the sample size.
2. THE SYSTEM SHALL check the parallel-trends assumption on the pre-period and run at least
   one placebo test (a fake event date in the pre-period).
3. IF the pre-trend check or placebo test fails THEN THE SYSTEM SHALL downgrade evidence
   strength and say why.
4. THE SYSTEM SHALL translate the segment effect into impact on the overall metric and on
   lost orders over the post-period, each with an interval.
5. THE SYSTEM SHALL state the identifying assumptions in plain language next to the estimate.
6. THE SYSTEM SHALL report evidence strength as `strong`, `moderate`, `weak` or `insufficient`.

### Requirement 7: Recommend the next experiment

**User Story:** As an experimentation lead, I want CauseLens to tell me what to test when it
can't be sure, or how to confirm a fix when it is.

#### Acceptance Criteria
1. WHEN evidence strength is below `strong` THE SYSTEM SHALL recommend a randomized
   experiment that would settle the question.
2. WHEN evidence strength is `strong` THE SYSTEM SHALL recommend a confirmation experiment
   (e.g. randomized rollback for the affected segment).
3. Each recommendation SHALL include hypothesis, randomization unit, primary metric,
   guardrail metrics, minimum detectable effect, required sample size per arm and expected
   duration based on observed traffic.
4. Sample size SHALL come from a deterministic power calculation, not the LLM.

### Requirement 8: Investigation report and console

**User Story:** As a stakeholder, I want one readable investigation report, so I can act
without reading the analysis.

#### Acceptance Criteria
1. THE SYSTEM SHALL produce a structured investigation result containing: confirmed change,
   change point, localization, candidate verdicts, causal estimate, unexplained remainder,
   data-quality notes, evidence strength and recommended experiment.
2. Every number in the report SHALL trace to a tool call recorded in the investigation log.
3. THE console SHALL show the investigation as steps (detect → locate → localize → test →
   estimate → recommend) with an evidence view for each.
4. THE report's wording SHALL follow `.kiro/steering/scientist-rules.md`.

### Requirement 9: Analytics exposed over MCP

**User Story:** As a developer, I want every analytics step available as an MCP tool, so
any agent can run an investigation with reproducible numbers.

#### Acceptance Criteria
1. THE SYSTEM SHALL expose each analytics function as an MCP tool with a typed input and
   output schema.
2. Analytics tools SHALL be deterministic given the same input and seed, and SHALL make no
   network or LLM calls.
3. Each tool SHALL return within 2 seconds on the reference dataset.

### Requirement 10: Graded against ground truth

**User Story:** As the builder, I want an automated check that the investigation gets the
planted answer, so regressions are caught immediately.

#### Acceptance Criteria
1. THE SYSTEM SHALL include an evaluation that runs a full investigation on the reference
   dataset and grades it against `data/ground_truth.json`.
2. The evaluation SHALL pass only if the investigation identifies Checkout v4.2, localizes
   to mobile × Safari, rejects the Homepage hero refresh, flags 2026-09-20 as a data-quality
   issue, and its CI covers `true_effect_in_segment_pp`.
3. THE evaluation SHALL run across at least 20 seeds and report how often each criterion passes.
