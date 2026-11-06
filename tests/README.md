# Testing Strategy

Planned tests include:

- unit tests for analytical tools
- statistical correctness checks
- property-based tests for analytical invariants
- agent/tool schema validation
- end-to-end investigation workflow tests

## Candidate properties

- a confidence interval lower bound cannot exceed its upper bound
- segment contributions should reconcile with the overall metric change within
  an acceptable tolerance
- null/permuted treatment assignments should not systematically produce large
  treatment effects
- analytical tools should return deterministic results for deterministic inputs
  unless randomness is explicitly controlled by a seed
