# Implementation Plan

- [x] 1. Synthetic dataset with planted cause, decoy and outage
  - `causelens/sim/generate.py` writes `metrics_daily.csv`, `events.csv`, `ground_truth.json`
  - _Requirements: 10.1_

- [x] 2. Data loading and validation
  - [x] 2.1 Typed loaders for metrics and events; reject invalid rows with clear errors
  - [x] 2.2 `check_data_quality` (rolling median ± k·MAD), link flags to incident events
  - [x] 2.3 Property test: injected order loss on one segment-day gets flagged
  - _Requirements: 1.1–1.5_

- [ ] 3. Confirm change and change point
  - [ ] 3.1 `confirm_change` with bootstrap CI over days
  - [ ] 3.2 `detect_change_point` with weekday deseasonalization and plausible-date set
  - [ ] 3.3 Match plausible dates to event log candidates
  - _Requirements: 2.1–2.3, 3.1–3.3_

- [ ] 4. Localization
  - [ ] 4.1 `decompose_change` (rate/mix per segment, single dims and pairs)
  - [ ] 4.2 Two-proportion tests with Benjamini–Hochberg adjustment
  - [ ] 4.3 Property test: decomposition closes; invariance to row order and labels
  - _Requirements: 4.1–4.5_

- [ ] 5. Candidate testing and causal estimate
  - [ ] 5.1 `test_event_scope` verdicts with evidence
  - [ ] 5.2 `estimate_did`, `check_pre_trends`, `run_placebo`
  - [ ] 5.3 `project_impact` to overall pp and lost orders
  - [ ] 5.4 Evidence-strength rules
  - [ ] 5.5 Property tests: CI ordering, null calibration over 50 seeds, monotonicity
  - _Requirements: 5.1–5.4, 6.1–6.6_

- [ ] 6. Experiment design
  - [ ] 6.1 `power_analysis` (two-proportion)
  - [ ] 6.2 `design_experiment` using observed traffic for duration
  - [ ] 6.3 Property test: power monotonicity
  - _Requirements: 7.1–7.4_

- [ ] 7. MCP server
  - [ ] 7.1 Expose every analytics tool with generated schemas (Streamable HTTP)
  - [ ] 7.2 Smoke test: each tool returns under 2 s on the reference data
  - _Requirements: 9.1–9.3_

- [ ] 8. Orchestrator and agents
  - [ ] 8.1 `InvestigationState` and logged tool calls
  - [ ] 8.2 Detective / Scientist / Experiment designer prompts using steering rules
  - [ ] 8.3 Structured investigation result
  - _Requirements: 8.1, 8.2, 8.4_

- [ ] 9. Ground-truth evaluation
  - [ ] 9.1 `python -m causelens.eval --seeds 20` grades against ground_truth.json
  - _Requirements: 10.1–10.3_

- [ ] 10. Investigation console
  - [ ] 10.1 Step view: detect → locate → localize → test → estimate → recommend
  - [ ] 10.2 Evidence panels (series with change point, segment waterfall, DiD chart, placebo)
  - _Requirements: 8.3_
