# How Kiro was used to build CauseLens

Owner-review draft, distinguishing implementation, supporting work and observed native evidence. No submission is claimed.

## Preparation and responsibility

Repository setup and the synthetic generator preceded the Kiro build and used other tools. Kiro implemented Tasks 2–10 and the Crew integration. Codex supplied independent review, supporting setup and documentation, including this draft. The chronological record is [KIRO_NOTES.md](../../KIRO_NOTES.md).

## Specifications and steering

Kiro followed the [metric-investigation specification](../../.kiro/specs/metric-investigation/tasks.md), then the [Crew specification](../../.kiro/specs/crew-app/tasks.md), one parent task at a time. Requirements, design and checklists remain in `.kiro/`.

The [scientist rules](../../.kiro/steering/scientist-rules.md) require deterministic statistics, uncertainty, sample sizes and named identification assumptions. Review exposed issues such as exposure scope versus expected effect pattern. Corrections and regression tests were recorded; the planted answer was preserved.

## Tools, hooks and native work

Kiro CLI 3.5.0 attached CauseLens after a Web MCP bug blocked that route. Fifteen tools include `daily_series`. Crew 0.13.1 installed the app through an opt-in Discover registry. Native roles made recorded calls; the template-written console preview is labelled.

Chat creation and message requests record Temporary mode with matching slot/agent metadata. Immediate memory counts do not establish delayed-consolidation behavior. The roles ran in separate sessions, without automatic parent/child coordination. A hosted model behind Python's `ModelReasoner` remains unverified.

Build notes record automatic timestamped Ruff Stop-hook executions. Affected regressions precede integration checks and one final full suite; pytest does not run on every save. The latest recorded check reports 1,404 pytest passes, 17 UI Node passes and Ruff clean.

## What the evidence supports

The reference verdict is moderate, conditional on lag 0, with release ambiguity stated. Delayed and staged failures remain visible. [Evaluation](../evaluation.md) grades fresh simulations against a key kept outside MCP-served inputs; it establishes no real-world accuracy.

The [evidence index](../crew-evidence/README.md) separates Temporary runs from a historical persistent-session menu export. Its fifteen-tool screenshot is Crew's MCP page, not a Kiro CLI panel.
