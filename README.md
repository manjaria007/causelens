# CauseLens

**CauseLens** is an autonomous AI investigation agent that helps teams understand **why a business metric changed**, validates possible causes with statistical and causal analysis, and recommends the next experiment or action.

Built for **Kiroween 2026** using Kiro.

## Problem

Dashboards are good at showing *what* changed, but teams still spend hours or days figuring out *why* it changed.

A drop in conversion, revenue, retention, or model performance often triggers a manual investigation across segments, releases, experiments, and operational events. CauseLens is designed to make that investigation faster, more rigorous, and more reproducible.

## What CauseLens Does

A user can ask a question such as:

> Why did checkout conversion fall from 8.2% to 6.9%?

CauseLens is designed to:

1. Detect when and where the metric changed.
2. Identify the most affected customer or product segments.
3. Generate candidate hypotheses.
4. Run statistical tests and quantify uncertainty.
5. Distinguish correlation from causal evidence.
6. Estimate treatment effects when the data and assumptions support causal analysis.
7. Simulate counterfactual outcomes.
8. Recommend the next experiment or business action.

## Why It Is Different

CauseLens is not intended to be another generic “chat with your data” assistant.

Its goal is to combine:
- agentic reasoning,
- reproducible analytical tools,
- statistical testing,
- causal inference,
- uncertainty reporting,
- experiment design,
- and a clear evidence trail.

## Planned Kiro Usage

CauseLens will use Kiro as part of both the development workflow and the product-building process.

Planned Kiro capabilities include:
- **Spec-driven development** for product requirements and technical tasks
- **Steering** to enforce scientific reasoning principles
- **Kiro Crew / custom agents** for specialized analytical roles
- **MCP** for deterministic analytical tools
- **Hooks** for automated quality checks
- **Property-based testing** for analytical invariants and edge cases

The root-level `.kiro/` directory will contain the Kiro-generated specs, steering, hooks, and related project configuration used during development.

## Planned Agent Architecture

- **Investigation Agent** — coordinates the analysis
- **Metric Detective** — change-point and segmentation analysis
- **Statistical Scientist** — hypothesis tests, confidence intervals, effect sizes
- **Causal Scientist** — treatment effects, assumptions, counterfactual analysis
- **Experiment Designer** — recommends follow-up experiments and guardrails

## Planned Analytical Tools

Potential MCP / analytical tools include:

- `inspect_dataset()`
- `detect_metric_change()`
- `find_change_point()`
- `segment_metric_change()`
- `generate_candidate_hypotheses()`
- `run_hypothesis_test()`
- `estimate_treatment_effect()`
- `simulate_counterfactual()`
- `recommend_experiment()`

## Example Demo Scenario

A synthetic commerce dataset will demonstrate a decline in checkout conversion.

CauseLens should be able to investigate the change, identify the most affected segment, connect the timing to a product release, quantify the statistical evidence, avoid overstating causality, and recommend a randomized rollback experiment.

## Project Status

🚧 **In active development for Kiroween 2026.**

The initial repository contains project documentation and scaffolding. Core implementation will be developed using Kiro during the official hackathon submission period.

## Kiroween 2026

**Category:** Frankenstein

CauseLens combines agent orchestration, statistical analytics, causal inference, MCP tools, experimentation, and a product interface into one application.

Potential bonus categories:
- Best Kiro Crew App
- Best Demo
- Social Blitz Prize
- Bonus Blog Prize

## License

This project is licensed under the MIT License.
