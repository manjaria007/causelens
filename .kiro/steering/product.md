---
inclusion: always
---

# Product: CauseLens

CauseLens is an AI investigator for business metrics. A user asks "why did checkout
conversion fall?" and CauseLens runs an investigation instead of chatting about the data:

1. Confirm the change is real (not noise).
2. Locate when it started (change point).
3. Localize where it happened (which segments drive it).
4. Gather candidate explanations (releases, campaigns, outages, seasonality) from an event log.
5. Test each candidate with a deterministic statistical method.
6. Estimate impact with uncertainty, and say how strong the evidence is.
7. When the evidence can't support a causal claim, recommend the experiment that would.

## Who it is for
Product managers, growth teams, analysts and experimentation teams who see a KPI move on a
dashboard and spend hours or days working out why.

## What makes it different
Detecting that a metric moved is solved. CauseLens is about *not overclaiming*: it separates
correlation from causation, exposes assumptions, quantifies uncertainty, rejects decoy
explanations, and ends with "what to test next" when it can't be sure.

## Demo dataset
The demo runs on a synthetic e-commerce dataset with a **planted cause** and a **decoy**
(see `data/ground_truth.json`). The investigation is judged correct only if it recovers the
planted cause, rejects the decoy, and its confidence interval covers the true effect.
