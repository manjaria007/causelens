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

A bad configuration (missing file, duplicate id, a file named like the answer key) prints one line to stderr and exits
with status 2 before anything is served.

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
  that are pooled. Integer options (lags, counts, seeds) are strict: `1.5`, `true` and `"5"` are rejected, never rounded.
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

### Rebuilding a population from a prior result

`project_impact` and `design_experiment` need more than a prior result: they need the traffic behind it.

* `project_impact(dataset_id, did, focal, comparison, quality, apply_exclusions)` rebuilds the panel and requires it to
  match the `DidResult`: the same excluded dates, usable days before and after the event, and focal and comparison
  sessions. A mismatch is an error that names the differing fields (for example a different `comparison`, or
  `apply_exclusions` not matching), so a DiD result is never scaled by traffic it did not use.
* `design_experiment(dataset_id, candidate_testing, post_window, quality, apply_exclusions, ...)` takes its population
  from the focal segment in `candidate_testing`. The call rebuilds that population and fails if it removes different days
  than the candidate test did (a different dataset, `quality` or `apply_exclusions`).
* `test_candidates` and `test_event_scope` reject a `decomposition` computed for different windows.

These checks only reject; they never change a number.

## Measured timing

Over real HTTP (the SDK client against the production app, one persistent session), 10 calls per tool on the reference
data, including loading the files, running the analytics and serializing the result. Sandbox hardware, not a benchmark.

| Tool | median ms | max ms | response KB |
|---|---|---|---|
| `list_datasets` | 50 | 58 | 0.6 |
| `check_data_quality` | 177 | 269 | 7.9 |
| `confirm_change` | 211 | 303 | 3.3 |
| `detect_change_point` | 203 | 298 | 2.5 |
| `match_event_candidates` | 49 | 58 | 1.3 |
| `decompose_change` | 314 | 407 | 164.0 |
| `test_candidates` (no prior results) | 507 | 571 | 16.6 |
| `test_candidates` (decomposition and quality passed) | 282 | 385 | 16.6 |
| `test_event_scope` | 513 | 573 | 6.5 |
| `estimate_did` | 273 | 366 | 1.0 |
| `check_pre_trends` | 232 | 335 | 0.3 |
| `run_placebo` | 278 | 372 | 0.9 |
| `project_impact` | 103 | 236 | 0.9 |
| `power_analysis` | 6 | 7 | 1.0 |
| `design_experiment` | 211 | 312 | 10.5 |

The slowest call is 573 ms. A new connection (initialize) takes about 35 ms, and a cold connection plus the heaviest call
645 ms. `tests/test_mcp_server.py` asserts every tool stays under 2 seconds (3 repeats each) and that a cold connection
plus `test_candidates` does too.

Two sizes to know about: `tools/list` is about 270 KB of JSON Schema (the tools that accept prior results embed those
models, 9 to 31 KB of input schema each), and `decompose_change` returns about 164 KB because it lists every segment. Agents that load all
tool schemas into context can use Kiro's `disabledTools` for the tools they do not need.

## What the tests prove

`tests/test_mcp_server.py` (real HTTP and the official client), `tests/test_mcp_cli.py` (real processes) and
`tests/test_mcp_datasets.py` (configuration):

* **Initialization and discovery** with both the automatic and the legacy handshake; 14 tools, read-only annotations,
  object schemas that are valid JSON Schema, `additionalProperties: false`, and no tool with a path or URL argument.
* **Schemas.** Output schemas are generated from the analytics models; every published default equals the analytics
  default; every analytics parameter is exposed; arguments (including full prior results) and results validate against
  the published schemas.
* **Equivalence.** Each tool's `structuredContent` equals `model_dump(mode="json")` of the direct analytics call, for a
  case per tool. `quality`, `apply_exclusions` and `comparison` are each shown to change the analysis and to be honoured by
  every tool that takes them. A multi-step chain over HTTP equals the same chain called directly.
* **Determinism** across repeated calls and across a fresh server instance; seeds are passed through.
* **Insufficient data and invalid input**: results versus errors, with the messages checked.
* **Read-only, no answer key, no network.** An audit hook records every file open and socket connect during all tools:
  `ground_truth.json` (which sits in the same directory) is never opened, nothing under the data directory is opened for
  writing, the data files' hashes and modification times are unchanged, and the only connections are to loopback. A
  configuration that names a file called `ground_truth*` is rejected at startup.
* **Mutation checks.** I mutated the adapter thirteen ways (unknown arguments accepted, a drifted default, each consistency
  check disabled, `quality` / `apply_exclusions` / `comparison` dropped in individual tools, a tool not marked read-only, a
  loader that reads the answer key, events not passed, swapped windows). Each is caught by a named test. Two of these
  were not caught at first (`apply_exclusions` ignored by one tool, and `comparison` ignored by `project_impact`), which is
  why those arguments now have their own tests.

## Notes and limits

* **SDK.** `mcp` 2.x renamed `FastMCP` to `MCPServer`; `requirements.txt` now pins `mcp>=2.3,<3` (it said `>=1.20`, which
  would allow an API this code does not support). The SDK's argument models ignore unknown keys, so `create_server`
  registers the tools while the SDK's argument-model base forbids extras, then restores it; a test checks the setting does
  not leak. That relies on an SDK internal, so a test fails loudly if a later SDK changes it.
* **Concurrency.** Tools are synchronous and the SDK runs them on worker threads; each call loads its own copy of the data.
* **Not implemented:** authentication, per-client dataset permissions, uploading or writing data, resources and prompts.
  Task 8 (the orchestrator) is not started.
