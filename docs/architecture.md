# Architecture — Draft

## Planned high-level flow

```text
User / Investigation UI
        |
        v
Investigation Orchestrator
        |
        +--> Metric Detective
        +--> Statistical Scientist
        +--> Causal Scientist
        +--> Experiment Designer
        |
        v
MCP / Deterministic Analytical Tools
        |
        +--> Python / statistical libraries
        +--> causal inference libraries
        +--> data sources
        |
        v
Evidence + uncertainty + recommended action
```

## Planned agent roles

### Investigation Orchestrator
Coordinates the investigation and decides which specialist or analytical tool
should be used next.

### Metric Detective
Finds change points, affected segments, and contribution patterns.

### Statistical Scientist
Runs hypothesis tests, effect-size calculations, confidence intervals, and
related statistical checks.

### Causal Scientist
Evaluates whether causal estimation is justified, states assumptions, estimates
treatment effects, and performs counterfactual analysis where appropriate.

### Experiment Designer
Recommends follow-up experiments, success metrics, guardrails, and sample-size
considerations when evidence is insufficient for a causal conclusion.

## Planned Kiro integration

The final architecture should be refined through Kiro Specs rather than treated
as fixed before implementation. Kiro steering should encode scientific
reasoning rules, hooks should automate validation, and MCP should expose
deterministic analytical capabilities.
