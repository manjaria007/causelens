# Investigation console (Task 10)

`python -m causelens.ui` runs an investigation and writes the console as one self-contained HTML file: no script, no network, inline
SVG. It shows the investigation as six steps with the evidence for each (Requirement 8.3).

```bash
python -m causelens.sim.generate               # once: data/metrics_daily.csv and data/events.csv
python -m causelens.ui --out console.html      # runs the Task 8 orchestrator through an in-process MCP server, offline
python -m causelens.ui --server http://127.0.0.1:8000/mcp --out console.html   # or against a running server
```

Options: `--metrics`, `--events`, `--no-events`, `--dataset-id`, `--pre START END`, `--post START END` (defaults are the reference
windows), `--question`, `--out`. Exit 0 when the page was written, including for an investigation that stopped (the page shows why);
2 for bad arguments or missing data. The command uses the template reasoner. **No hosted model is called, and a live hosted-model
integration remains unverified.** Library use: `render_html(build_view(investigation))`.

## The six steps

| Step | Shows | Evidence panel |
|---|---|---|
| 1 Detect | data-quality result, the change with its interval, effect size next to the noise-test p-value, what more data would be needed if it is not confirmed | |
| 2 Locate | most plausible onset, the plausible dates, logged events whose dates coincide | **Level before and after, with the change point** |
| 3 Localize | the localized segment and its share of the change and of traffic, FDR family, what remains unexplained | **Segment waterfall** |
| 4 Test | each candidate: verdict at its stated lag, hypothesis, reasons, the ground it was rejected on, lag sensitivity, ambiguity, share confidence set, and the alternatives considered | |
| 5 Estimate | differential effect with interval and sample sizes, the overall effect and lost orders, pre-trend and placebo checks, the assumptions, whether a causal claim is allowed | **Difference-in-differences chart** and **placebo chart** |
| 6 Recommend | the experiment (hypothesis, unit, metric, guardrails, minimum detectable effect, sample size, duration), or why none was designed | |

The orchestrator's seven stages map onto these steps; "detect" is validate plus confirm, because a tracking break has to be ruled out
before a change is believed. A step that did not run (the investigation stopped, or there is no event log) is shown as such with the
reason, never left blank.

## What is always on the page

* **Evidence strength** (`strong`, `moderate`, `weak`, `insufficient`) with the reason and the tool's own reasons, at the top, in every
  run. A run that stopped says so and says why.
* **Data-quality warning** whenever days were excluded or flags were raised: the excluded days, the tool's exclusion statement, the flags
  with their linked incidents, and that a tracking break is a valid explanation.
* **Uncertainty**: every estimate is shown with its interval, level and sample sizes (or the statement that the tool reports none, for
  placebo dates). A p-value is shown only next to the effect it belongs to.
* **Alternatives**: every candidate with the reason it was kept or set aside, and the alternatives the test tool lists. A rejection is
  worded as a rejection of a named hypothesis on the stated ground (exposure, declared pattern, or sole-source timing), not as the event
  contributing nothing.
* **Assumptions** beside the estimate, and a causal claim only where `assess_claim` allows it, labelled as a separate attribution.
* **Where the numbers come from**: every number carries a superscript call number linking to a table of the logged MCP calls (role,
  stage, tool, result, arguments by name). Hovering a number shows its exact value and its position in that call's result.

## How numbers are kept honest

The view (`causelens/ui/view.py`) is built from the structured result and, for the panels, from the logged MCP results through a
`Tracer`, so each number carries its call and pointer. `build_view` re-reads every one from the log (`verify_traces`) before anything is
drawn. The renderer (`render.py`) only formats traced values and draws geometry from them; it never calls a tool or computes a figure.
There are no chart-axis tick labels, because those would be numbers nobody returned: charts are labelled with traced values only.

Tests parse the rendered page and require that every number a reader sees (other than step numbers and call-number superscripts) is a
traced value, tool-written text or a validated statement; that a number inserted into the page is caught; that every interval's
bounds and level appear; that nothing is hidden; that there is no script, external resource or inline handler; and that text from the
data is escaped (an event named `<img src=x onerror=...>` is shown as text).

## Limits

* **No daily series.** The design asks for a series with the change point. No MCP tool returns daily values, and this task was to use
  existing results only, so the panel shows the level of conversion in each window (with its interval) against the change point,
  the plausible range, the candidate events and the excluded days. A true daily series would need a new tool (for example a
  `daily_conversion` tool over the same exclusions); that is a Task 7 addition, not made here.
* The waterfall draws the localized partition's top 12 segments by rank and states the partition's size; contributions are the tool's
  own and are not added across partitions.
* Display precision is fixed (for example three decimals for pp); exact values are in the tooltip. Orders and sessions are shown as
  whole numbers.
* The page is static: no filtering, no re-running from the page. The written summaries come from whichever reasoner the run used;
  the command uses the template reasoner.
* Not checked in other browsers or with a screen reader. Checked by screenshot in headless Chrome.
