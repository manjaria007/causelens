---
inclusion: always
---

# Tech stack and conventions

- Python 3.11+. Package `causelens/` with subpackages `sim/` (synthetic data), `analytics/`
  (deterministic statistics), `mcp/` (MCP server), `agents/` (investigation orchestration),
  `ui/` (investigation console).
- Data: pandas DataFrames in, typed results out. Results are pydantic models (or frozen
  dataclasses) so MCP tool schemas are generated, not hand-written.
- Statistics: numpy, scipy, statsmodels. Add heavier libraries (e.g. DoWhy) only if a
  requirement needs them and the simpler design can't do it.
- Tests: pytest + hypothesis (property-based). `pytest -q` must pass before a task is marked done.
- MCP: official `mcp` Python SDK, Streamable HTTP transport. Every analytics function is
  exposed as one MCP tool with a typed schema.
- Formatting and linting: ruff. Types: mypy on `causelens/analytics`.
- Determinism: every function that samples takes an explicit `seed`.
- Keep the demo runnable offline: `python -m causelens.sim.generate` then the console.
