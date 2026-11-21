# Orchestrator and agents (Task 8)

`causelens.agents` runs an investigation through the MCP server and returns a structured result. It computes no statistic. The
analytics run behind the server (Task 7); this package decides which tool to call next, logs every call, and assembles a report
in which every number is read back from the log (Requirement 8.1, 8.2, 8.4).

## Run it

```python
from causelens.agents import InvestigationRequest, Window, investigate_sync, stdio_target
from pathlib import Path

request = InvestigationRequest(
    question="Why did checkout conversion fall?", dataset_id="reference",
    pre_window=Window(start="2026-08-01", end="2026-10-13"), post_window=Window(start="2026-10-14", end="2026-10-31"),
)
# A server run as a child process over stdio. A URL string ("http://127.0.0.1:8000/mcp") or an in-process MCPServer also works.
run = investigate_sync(request, stdio_target(Path("data/metrics_daily.csv"), Path("data/events.csv")))
run.result.evidence      # strength and reason
run.state.log            # every MCP call, with its arguments and the server's result
```

`question` is recorded and echoed. It is not interpreted: the metric is the dataset's conversion rate (`orders / sessions`) and
the windows come from the request. Declared `expected_patterns` and `lag_days` are the user's and are passed through
unchanged; nothing is inferred from an event's name.

## Shared state and the log

`InvestigationState` holds the request, the stage records and `log`, an append-only list of `LoggedCall`: sequence number, role,
stage, tool, the arguments as sent and the structured result exactly as the server returned it (or the error text).
A prior result passed to a later call (the quality report, the decomposition) is stored in the arguments as
`{"$prior": seq}`, so the log is readable and the data are stored once. The `Toolbox` checks that a prior comes from the tool
that produces that argument. Results are validated into the analytics models only when read (`state.model(call)`).

Stages (`validate`, `confirm`, `change_point`, `localize`, `test`, `estimate`, `recommend`) are recorded with the calls made in
each, and with `done`, `skipped` or `stopped` and a reason.

## Roles

A role is a prompt plus an allowed tool subset, not a service. The toolbox refuses a tool outside the subset before anything is
sent.

| Role | Stages | Tools |
|---|---|---|
| Detective | validate, confirm, change point, localize | `list_datasets`, `check_data_quality`, `confirm_change`, `detect_change_point`, `match_event_candidates`, `decompose_change` |
| Scientist | test, estimate | `test_candidates`, `test_event_scope`, `estimate_did`, `run_placebo`, `check_pre_trends`, `project_impact` |
| Experiment designer | recommend | `power_analysis`, `design_experiment` |

The design table lists four Detective tools and five Scientist tools. `list_datasets` (the dataset fingerprints in the report),
`match_event_candidates` (Task 3.3) and `test_candidates` (the Task 5 entry point that runs the joint timing model over all
candidates) were added in Task 7 and are assigned here. The two roles' tools are disjoint and together cover all 14 server
tools. The designer's `power_analysis` is available but not called: `design_experiment` already sizes from it.

`Role.system_prompt()` is what a model-backed reasoner receives: the role's instructions, its tools, the writing contract, and
`.kiro/steering/scientist-rules.md` verbatim (read from the file at call time, an error if it is missing).

## What each stage does

1. **validate** `list_datasets`, `check_data_quality`. An unknown dataset stops the run. The quality report is passed to every
   later call, so the same days are excluded throughout.
2. **confirm** `confirm_change`. If the result is `insufficient_data`, or the change is not distinguishable from noise, the run
   stops (Requirement 2.3) with evidence `insufficient` and the tool's own "data needed" figures.
3. **change point** `detect_change_point`, then `match_event_candidates` when a change is detected and an event log exists.
   A match is a date coincidence.
4. **localize** `decompose_change`. Segment scans are FDR-controlled by the tool; the result reports the family and the count.
5. **test** `test_candidates` with the localization and quality report. Skipped when the dataset has no event log.
6. **estimate** for each candidate the tests mark `supported`: `estimate_did`, `check_pre_trends`, `run_placebo`,
   `project_impact`, all at the candidate's onset date (event date plus evaluated lag) and the localized focal segment. The
   standalone DiD is compared with the one embedded in the candidate test; a difference is recorded in `state.notes` and
   `matches_candidate_test`. No candidate supported means no estimate.
7. **recommend** `design_experiment` whenever candidate testing produced a focal segment. With no single leading candidate the
   tool needs a target or one arm per open candidate; the orchestrator uses one arm per open candidate rather than name a winner
   the tests did not.

A failing tool call is logged, the stage is marked `stopped`, and the result has status `stopped_tool_error` with the failed call's
position. Nothing is retried or filled in.

## The result

`InvestigationResult` has: confirmed change, change point, localization, candidate verdicts (with the lag actually evaluated,
the reasons, the lag sensitivity and ambiguity), causal estimates, unexplained remainder, data-quality notes, evidence strength
with reason, the recommended experiment, assumptions, and the statements written for the reader. It is deterministic given the
data and the request, and identical over an in-process server and a real stdio process (a test compares them byte for byte). HTTP was not run in
this task.

**Every number is a `Traced` or `TracedInterval`.** Each holds the value together with the log position and JSON pointer it was
read from, built only by a `Tracer` that reads the log. `verify_traces` reads every one again and requires equality, and
`investigate` runs it before returning. A test walks the dumped result and requires that no int or float exists outside a traced
object, apart from log positions. An interval carries its level and the sample sizes behind it (days, sessions, observations).
A p-value appears only next to the effect size it belongs to. Tool-written text (headlines, reasons, assumptions) is traced the
same way, as a `text` value.

**Evidence strength.** For a supported candidate it is the tool's own assessment (`strong`, `moderate`, `weak`, `insufficient`)
with its reasons. With more than one supported candidate the weakest is reported. With none, or when the run stopped, it is
`insufficient`, with a fixed reason. The reference data gives `moderate`: the base assessment is `strong` and the tool caps it
at `moderate` because release attribution is ambiguous (Hero at lag 1 and Checkout at lag 0 share an onset date).

**Unexplained remainder.** The remaining share of the change and the part of the rate effect not distinguishable from noise are the
decomposition's own figures for the localized set. They are shown as returned, including a negative remaining share (the localized
segment moved more than the overall change). Nothing is forced to a whole. With no supported event the note says the whole
change is unexplained by the event log.

**Causal claims.** `assess_claim` allows the claim for an event only when it is `supported`, there is a valid comparison, the
difference-in-differences interval excludes zero, and the pre-trend and placebo checks both `pass` (a borderline or unrun check
does not count). The claim sentence quotes the estimate with its interval and sample size, and the design's assumptions next to
it, and labels the attribution to the release as separate. When the claim is not allowed, the report says "no causal claim is
made" and the estimate is reported as consistent with.

## The LLM layer

The design splits the work: agents decide what to analyse and write the report; tools compute. `Reasoner.interpret(role, context)`
is that LLM boundary. It returns `Statement` objects: a sentence template whose placeholders are bound to `Ref(call, path, format)`
entries into the log. The orchestrator validates each statement before it enters the report:

* the template text contains no digit, so a number can reach a sentence only through a reference;
* every placeholder has a reference and every reference resolves to a value in the log of the right kind for its format;
* words that assert a cause (`cause`, `because`, `due to`, `led to`, `responsible`, and similar) are rejected, except in a
  `causal_claim` that cites a difference-in-differences call whose checks passed and quotes that call's assumptions.

A rejected statement is dropped and listed in `rejected_statements`; it is never repaired. Display formats (`pp`, `pct`, `level`,
`count`) fix the number of decimals in code; the stored values are never rounded.

`TemplateReasoner` is the deterministic, offline implementation, so the demo needs no model (tech.md). A model-backed reasoner
implements the same protocol: it builds its system message with `Role.system_prompt()`, asks the model for statements in the
`Statement` schema, and returns them. No such reasoner ships in this task. The validation does not depend on who wrote the
statement, and a test drives the orchestrator with a scripted reasoner that tries a typed-in number, a cause-asserting sentence,
an unresolved reference and a forged causal claim: all four are rejected and recorded.

## Tests

* `tests/test_agents_trace.py`: pointer resolution, traced values and sentence rules, including property tests (every path in a
  random JSON document resolves to its value; any digit in a template is rejected; values round-trip exactly).
* `tests/test_agents_toolbox.py`: role limits, the call log, prior pointers, error logging, and the official client against the real
  server.
* `tests/test_agents_investigation.py`: the reference investigation graded against `ground_truth.json` (Checkout supported, Hero
  rejected, 2026-09-20 flagged, localized to mobile Safari, interval covers the true effect), traceability, the scientist rules,
  stopping at `confirm_change`, a dataset without events, an unknown dataset, a failing call, a hostile reasoner, withheld claims,
  determinism, and the same run over a real stdio process.

## Not done here

The multi-seed ground-truth evaluation (Task 9) and the investigation console (Task 10). The incorrect-attribution rates of the
candidate tests are in `docs/candidate-testing.md`; this task adds no statistics, so they are unchanged.
