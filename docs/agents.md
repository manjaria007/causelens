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

The request is parsed as strictly as the MCP boundary: dates are ISO `YYYY-MM-DD` strings (not timestamps, numbers or date-times),
a lag is a JSON integer (`true`, `"1"` and `1.0` are refused), strings are not coerced, and a declared pattern is validated as
strict JSON. Reversed windows are the server's to refuse; the failed call is logged.

## Shared state and the log

`InvestigationState` holds the request, the stage records, the parameter decisions and `log`, an append-only list of `LoggedCall`:
sequence number, role, stage, tool, the arguments as sent and the structured result exactly as the server returned it (or the error
text). Each entry is a snapshot. The toolbox copies what the caller passed and what the server returned, and the stored payloads are
read-only (`FrozenDict`, `FrozenList`), so editing a caller's segment list after a call, or a result in place, cannot rewrite the
history. The `log` list itself is append-only by convention; code that replaces an entry is replacing history.
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

A failing tool call is logged, the stage is marked `stopped`, and the result has status `stopped_tool_error` with the failed
call's position and the stage that stopped. That includes transport failures: a server that goes away mid-run gives a logged
`SessionClosed` entry, not an exception. (`McpToolClient` runs the SDK session in its own task, because the SDK cancels the task
that opened it when its transport fails; a real cancellation of the caller still propagates.) Nothing is retried or filled in.

The report's account of what did not happen is derived from how the stages ended. A failure after testing keeps the tests'
evidence and any estimate and says only that the design call failed; a failure during estimation says the estimate did not complete;
a stop before testing says testing did not run; a dataset with no event log says that.

## The result

`InvestigationResult` has: confirmed change, change point, localization, candidate verdicts (with the lag actually evaluated,
the reasons, the lag sensitivity and ambiguity), causal estimates, unexplained remainder, data-quality notes, evidence strength
with reason, the recommended experiment, assumptions, and the statements written for the reader. It is deterministic given the
data and the request, and identical over an in-process server and a real stdio process (a test compares them byte for byte). The same
run over Streamable HTTP is also compared in a test.

**Every number is a `Traced` or `TracedInterval`.** Each holds the value together with the log position and JSON pointer it was
read from, built only by a `Tracer` that reads the log. `verify_traces` reads every one again and requires equality, and
`investigate` runs it before returning. A test walks the dumped result and requires that no int or float exists outside a traced
object, apart from log positions. An interval carries its level and the sample sizes behind it (days, sessions, observations).
A p-value appears only next to the effect size it belongs to. Tool-written text (headlines, reasons, assumptions) is traced the
same way, as a `text` value.

**Why a candidate was rejected.** A rejected candidate lists the ground or grounds the tool rejected on, read from its structured
fields: the exposure check (`disjoint`), a declared pattern that a direct test contradicted, or the sole-source timing test. The
plain statement beside it says the same and no more. A pattern rejection is not described as a sole-source rejection (Checkout under
a declared uniform pattern is rejected on the pattern, while its sole-source test is not rejected), and no rejection is described as
the event contributing nothing. `sole_source_rejected` is reported separately.

**Share confidence sets.** Each tested candidate carries the confidence set for the share of the localized change that begins at its
onset date (Requirement 5.3): level, kind, each interval (an open end is `None`), or the tool's reason when it is unavailable. The
set is labelled as describing calendar steps under the lag model, not a release's causal share.

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
does not count). A causal sentence is then bound to that one claim (`causelens.agents.claims.required_refs`): it must quote the
claim's event, evaluated lag and onset, the estimate with its interval and level, the sample size, and every assumption of the
design, each in its required format (an estimate cannot be shown as a whole number); it must name the design; it may quote nothing
about another candidate, so a rejected event cannot borrow the supported event's estimate; and it may not type an event name. When the
claim is not allowed, the report says "no causal claim is made" and the estimate is reported as consistent with. The attribution to
the release is labelled as a separate claim.

## The LLM layer

The design splits the work: agents decide what to analyse and write the report; tools compute. `Reasoner` is that boundary, with
two methods the orchestrator calls at fixed points.

* `choose_parameters(role, tool, context)` runs before each call to a tool that has tunable parameters
  (`causelens.agents.parameters.TUNABLE`: `confirm_change` bootstrap size and minimum days, the event lead window of
  `match_event_candidates` and `test_candidates`, and the explaining-set threshold and pair option of `decompose_change`). The list is
  short on purpose. Windows, the dataset, prior results, exclusions, declared patterns and lags, seeds, significance levels, power and
  the minimum detectable effect are the user's or the pipeline's, so a choice that names any of them is refused. A choice is validated
  strictly (a JSON integer is not `true`, `"7"` or `7.0`; ranges are inclusive), its rationale follows the sentence rules (no digits, no
  cause-asserting words), and a refused choice is refused whole: the tool's default is used and the refusal is in
  `parameter_choices`. An accepted choice appears in the call's logged arguments; the result lists the parameter names and the
  rationale, not the values.
* `interpret(role, context)` runs right after each role's stages (and, after a failure, for the role that was working), so a model
  sees the Detective's results before the Scientist runs. It returns `Statement` objects: a sentence template whose placeholders are
  bound to `Ref(call, path, format)` entries into the log.

Each statement is validated before it enters the report:

* the template text contains no digit, so a number can reach a sentence only through a reference;
* every placeholder has a reference and every reference resolves to a value of the right kind for its format; a malformed template
  (an unmatched brace, `{}`, `{0}`, `{a.b}`) is a rejection, not a crash;
* words that assert a cause (`cause`, `because`, `due to`, `led to`, `responsible`, and similar) are rejected, except in a
  `causal_claim` bound to an eligible claim as described above;
* a statement written for another role is refused.

A rejected statement or parameter choice is dropped and listed (`rejected_statements`, `parameter_choices`); it is never repaired. A
reasoner that raises is recorded the same way and the investigation continues, because the structured result does not depend on the
text. Display formats (`pp`, `pct`, `level`, `count`) fix the number of decimals in code; the stored values are never rounded.

`TemplateReasoner` is the deterministic, offline implementation, so the demo needs no model (tech.md): fixed sentence templates and the
tools' own parameter defaults. `ModelReasoner` implements the same protocol over any `TextModel` (`complete(system, user) -> str`). It
sends `Role.system_prompt()` (the role's instructions, its tools, the writing contract and `scientist-rules.md` verbatim) and a JSON
user message with a digest of the logged results (pointers to scalar values, capped in size), the allowed claims with the references
each must quote, the formats, and the tunable parameters; it expects a JSON object back and parses it strictly. Its output is
untrusted and goes through the validation above.

**What was and was not run.** `ModelReasoner` is tested with scripted models that stand in for a provider (what it sends, how it reads
replies, malformed replies, a hostile reply that tries a typed number, a cause, a protected parameter and a rejected event's
estimate). No provider adapter ships and no hosted model was called, so how a real model behaves behind it is not known.

## Tests

* `tests/test_agents_trace.py`: pointer resolution, traced values and sentence rules, including property tests (every path in a
  random JSON document resolves to its value; any digit in a template is rejected; values round-trip exactly).
* `tests/test_agents_toolbox.py`: role limits, the call log, prior pointers, error logging, and the official client against the real
  server.
* `tests/test_agents_llm.py`: parameter choice (accepted, refused, protected), when the reasoner is consulted, the model-backed reasoner
  with scripted models, and the digest.
* `tests/test_agents_review.py`: one regression per finding of the PR #9 review of `f62a8b4`.
* `tests/test_agents_investigation.py`: the reference investigation graded against `ground_truth.json` (Checkout supported, Hero
  rejected, 2026-09-20 flagged, localized to mobile Safari, interval covers the true effect), traceability, the scientist rules,
  stopping at `confirm_change`, a dataset without events, an unknown dataset, a failing call, a hostile reasoner, withheld claims,
  determinism, and the same run over a real stdio process.

## Not done here

The multi-seed ground-truth evaluation (Task 9) and the investigation console (Task 10). The incorrect-attribution rates of the
candidate tests are in `docs/candidate-testing.md`; this task adds no statistics, so they are unchanged.
