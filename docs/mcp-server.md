# MCP server (Task 7)

`causelens.mcp` exposes the analytics as MCP tools. It is a thin, read-only adapter: each tool resolves a `dataset_id`
to configured local files, calls the existing `causelens.analytics` function with the same defaults, and returns that
function's result model unchanged. Nothing is recomputed, reshaped or rounded in the adapter. It uses the official
Python SDK (`mcp` 2.x, `MCPServer`) with the Streamable HTTP transport, and also offers stdio.

Requirement 9: each analytics function is a tool with a typed input and output schema (9.1), results are
deterministic and make no network or LLM calls (9.2), and each tool returns within 2 seconds on the reference data (9.3).

## Start it

Python 3.11+ and the packages in `requirements.txt` (`mcp>=2.3,<3`; the 1.x `FastMCP` API is not used).

```bash
# Streamable HTTP, http://127.0.0.1:8000/mcp
python -m causelens.mcp --dataset reference data/metrics_daily.csv data/events.csv

# stdio, for a local client that launches the server itself
python -m causelens.mcp --transport stdio --dataset reference data/metrics_daily.csv data/events.csv
```

| Option | Meaning |
|---|---|
| `--dataset ID METRICS_CSV [EVENTS_CSV]` | A dataset; repeat for several. `ID` is letters, digits, `.`, `_`, `-`. The events file is optional. |
| `--config FILE` | JSON `{"datasets": [{"id", "metrics", "events", "description"}]}`; paths are relative to the file (also `$CAUSELENS_MCP_CONFIG`). See `docs/mcp/datasets.example.json`. |
| `--transport` | `streamable-http` (default) or `stdio`. |
| `--host`, `--port`, `--path` | HTTP bind address (default `127.0.0.1`), port (`8000`) and endpoint path (`/mcp`). |
| `--stateless`, `--json-response` | HTTP without server-side sessions; JSON instead of an event stream for POST replies. |
| `--allowed-host HOST[:PORT]` | Extra `Host` header to accept. Needed when binding a non-loopback address. |
| `--log-level` | `WARNING` by default. |

A bad configuration prints one line to stderr and exits with status 2 before anything is served. That covers a missing
file, a duplicate id, an invalid id, a config-file value of the wrong type (`"events": ["events.csv"]`, a non-string
`description`, an unknown key) and the answer key. A config-file `events` is a file name, `null` or omitted; a malformed
value is rejected, never read as "no event log".

**The answer key is never reachable as a dataset.** A configured file is refused if its name, any symlink hop on the way, its
resolved target, or its identity (a hard link to a `ground_truth*` file in the same directory) looks like the answer key.
The resolved path is what is kept and read, so a link that is re-pointed after startup does not change what is read. This is a
guard against misconfiguration, not a sandbox: a hard link to the key from a different directory cannot be detected.

**stdio hygiene.** Under `--transport stdio`, stdout carries only protocol messages. Logs, warnings and errors go to
stderr. `tests/test_mcp_cli.py` runs the real process, drives it with raw JSON-RPC (including `test_candidates`, which
loads statsmodels) and requires every stdout line to be a JSON-RPC message.

The HTTP server has no authentication. It binds loopback by default; do not expose it on a network without an
authenticating proxy in front.

## Configure Kiro

Kiro reads `.kiro/settings/mcp.json` (workspace) or `~/.kiro/settings/mcp.json` (user), per
[Kiro's MCP configuration](https://kiro.dev/docs/mcp/configuration/). Ready-made files are in `docs/mcp/`; copy one into
`.kiro/settings/mcp.json`. Every tool is read-only, so listing them in `autoApprove` is reasonable; remove names to be
prompted instead, or add rarely used ones to `disabledTools`.

Remote (start the HTTP server first): `docs/mcp/kiro-http.json`

```json
{ "mcpServers": { "causelens": { "url": "http://127.0.0.1:8000/mcp", "disabled": false } } }
```

Local process over stdio (for Kiro Web, where the workspace root is `/projects/sandbox` and the repository is
`/projects/sandbox/causelens`): `docs/mcp/kiro-stdio.json`

```json
{ "mcpServers": { "causelens": {
    "command": "/projects/sandbox/causelens/.venv/bin/python",
    "args": ["-m", "causelens.mcp", "--transport", "stdio", "--dataset", "reference",
             "/projects/sandbox/causelens/data/metrics_daily.csv", "/projects/sandbox/causelens/data/events.csv"],
    "env": { "PYTHONPATH": "/projects/sandbox/causelens" } } } }
```

Use absolute paths: the package is not pip-installed, so `PYTHONPATH` points at the repository, and the process's
working directory is not specified by Kiro's configuration. These files follow Kiro's documented format and are checked
by tests for valid JSON and real command-line flags. I did not run them inside Kiro itself, so confirm the server
appears and connects in Kiro's MCP panel after copying.

### Verify an actual Kiro tool call

Everything above is tested with the official SDK client. **A call made from Kiro itself has not been made**: the tool
catalogue of the session that built this server did not include it, because Kiro loads MCP servers when a session starts.
Before relying on Kiro to call these tools:

1. Copy `docs/mcp/kiro-stdio.json` (or `kiro-http.json` with the server running) to `.kiro/settings/mcp.json` in the
   workspace (the Kiro Web workspace root is `/projects/sandbox`). Leave `autoApprove` out for the first run if you want to
   see each call. (In the Kiro Web sandbox that built this server, `/projects/sandbox/.kiro/settings/` was read-only to the
   agent, so the file could not be written from a session and has to be added from Kiro's MCP settings or by hand.)
2. Start a **new** Kiro session so the server loads, and confirm `causelens` is listed with 14 tools in the MCP panel.
3. Ask Kiro to call `list_datasets`, then `power_analysis` with `baseline: 0.05, mde_pp: 1.0`, then
   `check_data_quality` on `reference`. Expect: dataset `reference` with 5,888 rows; 8,158 sessions per arm; excluded date
   `2026-09-20`.
4. Check one refusal: ask for `power_analysis` with `mde_pp: true`. It must come back as an error, not as 1 pp.

Record what Kiro actually showed in `KIRO_NOTES.md`. A manual run of the command line, or of the SDK client, is not that.

## The tools

All tools carry `readOnlyHint`, `idempotentHint` and `openWorldHint: false` annotations. A tool's input schema rejects
unknown arguments (`additionalProperties: false`), so a misspelt option is an error instead of a silently ignored default.

| Tool | Analytics function | Prior results it accepts | Returns |
|---|---|---|---|
| `list_datasets` | (adapter) | | `DatasetCatalog` |
| `check_data_quality` | `check_data_quality` | | `DataQualityReport` |
| `confirm_change` | `confirm_change` | `quality` | `ConfirmChangeResult` |
| `detect_change_point` | `detect_change_point` | `quality` | `ChangePointResult` |
| `match_event_candidates` | `match_event_candidates` | `change_point` | `CandidateMatchResult` |
| `decompose_change` | `decompose_change` | `quality` | `DecompositionResult` |
| `test_candidates` | `test_candidates` | `decomposition`, `quality` | `CandidateTestingResult` |
| `test_event_scope` | `test_event_scope` | `decomposition`, `quality` | `CandidateVerdict` |
| `estimate_did` | `estimate_did` | `quality` | `DidResult` |
| `check_pre_trends` | `check_pre_trends` | `quality` | `PreTrendResult` |
| `run_placebo` | `run_placebo` | `quality` | `PlaceboResult` |
| `project_impact` | `project_impact` | `did`, `quality` | `ImpactResult` |
| `power_analysis` | `power_analysis` | | `PowerResult` |
| `design_experiment` | `design_experiment` | `candidate_testing`, `quality` | `ExperimentDesign` |

The table has the eleven tools from the design, plus `test_candidates` and `match_event_candidates` (the Task 3 and
Task 5 entry points an investigation needs between them) and `list_datasets`. Library helpers that are not tools in the
design (`normal_approx_power`, `two_proportion_test`, `benjamini_hochberg`, the loaders and validators) are not exposed.

### Conventions

* **Datasets.** Data-dependent tools take `dataset_id`. The caller never supplies a path, and no tool takes a file name,
  URL or directory. `power_analysis` takes no dataset. Each call reads and validates the files (about 45 ms for the
  reference data); there is no cache, so an edited file is picked up. The dataset's event log is passed to the analytics
  whenever one is configured; `test_candidates`, `test_event_scope` and `match_event_candidates` require it.
  `list_datasets` returns file names (never paths), SHA-256 fingerprints, row counts, the date range and the segment
  values, so a result can be tied to the exact data behind it.
* **Windows** are `{"start": "YYYY-MM-DD", "end": "YYYY-MM-DD"}`, inclusive. **Dates** are ISO strings.
* **Units.** Conversion rates are fractions. Changes and effects are in percentage points (`*_pp`). In `power_analysis`,
  `baseline` is a fraction and `mde_pp` a signed percentage-point change (`target_rate = baseline + mde_pp / 100`).
* **Segments** are `{"device": "mobile", "browser": "safari"}`; a `focal` or `comparison` may also be a list of segments
  that are pooled.
* **Values are not coerced.** The SDK's default validation turns `true` into `1.0`, `"0.5"` into `0.5` and `"false"` into
  `false`, which changes what a call means (`mde_pp: true` was a 1 pp effect). Numbers must be JSON numbers (an integer is
  fine for a float), integers must be integers (`1.5`, `true` and `"5"` are rejected, never rounded), and flags must be
  `true` or `false`. Typed model arguments (`quality`, `detector_params`, `decomposition`, `candidate_testing`, `did`,
  `expected_patterns`) are re-validated as strict JSON, so the same holds inside them.
* **Dates are ISO strings, `YYYY-MM-DD`, and nothing else.** The SDK's lax parsing reads `0` and `"86400"` as Unix timestamps
  (`event_date: 0` and `"0"` ran as 1970-01-01, and a window starting at `0` became a window starting on 1970-01-01) and `"2026-10-14T00:00:00"` as a date.
  All of those are refused, for bare date arguments, window ends and the dates inside prior results.
* **Seeds.** `confirm_change` has `seed` and `n_bootstrap`; the same seed gives the same interval. Every other tool is
  deterministic without one.
* **Prior results are typed models.** Pass a tool's `structuredContent` unchanged as the matching argument of the next
  tool (the schema is the analytics model's own). Keep the same `dataset_id`, population, `quality` and
  `apply_exclusions` through a chain so each step rests on the same days. `quality` carries the exclusions; pass the report
  from `check_data_quality`, or let each tool compute the default one.
* **Insufficient data is a result.** `status: "insufficient_data"` / `"insufficient"` / `"unavailable"` /
  `"needs_input"`, with a reason, comes back as an ordinary result (`isError: false`), exactly as the analytics return it.
  An invalid argument is a tool error (`isError: true`) that names the problem and never includes a traceback or a
  server path. A crash inside the analytics returns the SDK's generic message and is logged to stderr.

### Prior results are bound to their source by reproducing them

Matching dates or session totals does not show that a prior result came from the dataset and population the caller names.
Reproduced on the first version of this adapter: a DiD result from a dataset with a decline was accepted against another
dataset with identical sessions and a rise, and `project_impact` returned -0.206 pp where the data give +0.195 pp; a stale
candidate-testing result sized a rollback in the wrong direction (+0.585 pp where the data give -0.523 pp); and a DiD result
for one population was accepted for another population with the same sessions (-0.189 pp where the data give +0.067 pp).

So the adapter recomputes what it can with the existing analytics, from the named dataset and the settings recorded in the
result itself, and requires the supplied result to equal the recomputation (floats to a relative 1e-9, so a result that
travelled as JSON text still matches). A result that does not reproduce is refused with the fields that differ. Nothing is
altered; an accepted result is passed to the analytics unchanged.

| Argument | Where | What is reproduced |
|---|---|---|
| `quality` | every tool that takes it | `check_data_quality` on the dataset with the report's own `params`. A hand-edited report is refused; change exclusions with `detector_params` or `apply_exclusions=false` instead. |
| `did` | `project_impact` | the DiD on the rebuilt population at the result's event date, `cov`, `weights` and level. |
| `candidate_testing` | `design_experiment` | the exclusions, every stored DiD and impact, and the joint timing analysis at the declared onsets (the series the candidate test used), at the interval level the result was made at. That level is read from the stored intervals, or from the share sets, which record it even when nothing was estimable. |
| `decomposition` | `test_candidates`, `test_event_scope` | each window's totals **and** the sessions and orders of every segment of every partition it lists, after the window's own excluded days, with no segment missing; the windows must also equal the windows given. Totals alone are not enough: moving orders between mobile and desktop leaves them unchanged and points the localization at the wrong segment. |

Limits. A change point (`match_event_candidates`) is not checked: it records too few of its settings to recompute
faithfully, and the tool only joins its dates to the event log. A result is checked on what it stores, so a
candidate-testing result with no DiD and no timing analysis has little to check. The check costs a recomputation: about
0.13 s for a quality report, and about 0.2 s more for a DiD (see the timing table).

## Measured timing

Over real HTTP (the SDK client against the production app, one persistent session), 10 calls per tool on the reference
data, including loading the files, running the analytics and serializing the result. Sandbox hardware, not a benchmark.

| Tool | median ms | max ms | response KB |
|---|---|---|---|
| `list_datasets` | 50 | 62 | 0.6 |
| `check_data_quality` | 178 | 284 | 7.9 |
| `confirm_change` | 214 | 323 | 3.3 |
| `detect_change_point` | 213 | 302 | 2.5 |
| `match_event_candidates` | 51 | 60 | 1.3 |
| `decompose_change` | 318 | 408 | 164.0 |
| `test_candidates` (no prior results) | 525 | 574 | 16.6 |
| `test_candidates` (decomposition and quality passed) | 448 | 539 | 16.6 |
| `test_event_scope` | 481 | 661 | 6.5 |
| `estimate_did` | 272 | 377 | 1.0 |
| `check_pre_trends` | 233 | 333 | 0.3 |
| `run_placebo` | 281 | 359 | 0.9 |
| `project_impact` | 272 | 365 | 0.9 |
| `power_analysis` | 7 | 8 | 1.0 |
| `design_experiment` | 387 | 483 | 10.5 |

The slowest call is 661 ms (re-measured after the binding checks and the per-segment decomposition check were added; runs vary
by about 10%). A new connection (initialize) takes about 35 ms, and a cold connection plus the heaviest call 653 ms. `tests/test_mcp_server.py` asserts that the median of five calls to every tool is under 2 seconds (and no call over 6 s),
and the same for a cold connection plus `test_candidates`. A wall-clock limit on a shared machine otherwise tests the
machine: with the one core shared with three busy loops, the heaviest tool's slowest call crossed 2 s while its median stayed
within the limit, so the earlier max-of-three assertion could fail under load without any change to the code.

Two sizes to know about: `tools/list` is about 270 KB of JSON Schema (the tools that accept prior results embed those
models, 9 to 31 KB of input schema each), and `decompose_change` returns about 164 KB because it lists every segment. Agents that load all
tool schemas into context can use Kiro's `disabledTools` for the tools they do not need.

## What the tests prove

`tests/test_mcp_server.py` (real HTTP and the official client), `tests/test_mcp_cli.py` (real processes),
`tests/test_mcp_datasets.py` (configuration) and `tests/test_mcp_review.py` (the PR #7 review: binding, strict values, the
answer key behind a link, malformed configuration):

* **Initialization and discovery** with both the automatic and the legacy handshake; 14 tools, read-only annotations,
  object schemas that are valid JSON Schema, `additionalProperties: false`, and no tool with a path or URL argument.
* **Schemas.** Output schemas are generated from the analytics models; every published default equals the analytics
  default; every analytics parameter is exposed; arguments (including full prior results) and results validate against
  the published schemas.
* **Equivalence.** Each tool's `structuredContent` equals `model_dump(mode="json")` of the direct analytics call, for a
  case per tool. `quality`, `apply_exclusions` and `comparison` are each shown to change the analysis and to be honoured by
  every tool that takes them. A multi-step chain over HTTP equals the same chain called directly.
* **Determinism** across repeated calls and across a fresh server instance; seeds are passed through.
* **Binding.** Variants of the reference data (`tests/mcp_variants.py`) with the same sessions but the opposite effect, two
  equal-sized populations, and no outage: a result from one is refused for another, an edited result is refused field by
  field, a stale quality report is refused by every tool that takes one, a decomposition with the same totals but different segments is
  refused, results made at 80%, 90% and 99% are accepted unchanged, and a matching result is returned unchanged.
* **Strict values.** `true`, `"0.5"`, `"false"`, `1.0` for an integer, timestamps and datetime strings for dates, and bad
  values inside nested models are each refused, and a JSON integer is still accepted for a float.
* **Insufficient data and invalid input**: results versus errors, with the messages checked.
* **Read-only, no answer key, no network.** An audit hook records every file open and socket connect during all tools:
  `ground_truth.json` (which sits in the same directory) is never opened, nothing under the data directory is opened for
  writing, the data files' hashes and modification times are unchanged, and the only connections are to loopback. A
  configuration that names a file called `ground_truth*` is rejected at startup.
* **Mutation checks.** The adapter was mutated 13 ways in the first round, 11 more after the first review and 7 more after the second (each binding check
  disabled, `quality` unchecked in one tool, the decomposition check removed, a lax `mde_pp`, lax nested models, the
  hard-link check removed, a non-string `events` dropped silently, the resolved path not kept). Each is caught by a named
  test, with one exception: removing the check of the resolved target's name changes nothing, because the identity check
  catches the same case (a symlink to a `ground_truth*` file is the same file as that sibling). The two layers overlap on
  purpose.

## Notes and limits

* **SDK.** `mcp` 2.x renamed `FastMCP` to `MCPServer`; `requirements.txt` now pins `mcp>=2.3,<3` (it said `>=1.20`, which
  would allow an API this code does not support). The SDK's argument models ignore unknown keys, so `create_server`
  registers the tools while the SDK's argument-model base forbids extras, then restores it; a test checks the setting does
  not leak. That relies on an SDK internal, so a test fails loudly if a later SDK changes it.
* **Strict values and the SDK.** Strict validation is added in this adapter (per-field strict types, and typed models
  re-validated as strict JSON); the published schemas are unchanged.
* **Concurrency.** Tools are synchronous and the SDK runs them on worker threads; each call loads its own copy of the data.
* **Not implemented:** authentication, per-client dataset permissions, uploading or writing data, resources and prompts.
  Task 8 (the orchestrator) is not started.
