# Native Crew evidence (crew-app spec)

Files observed on the owner's Mac with Crew 0.13.1. Transcripts were read back from the gateway with `GET /api/chat/slots/<slot>` (the endpoint
the page's chat embed reads); they are not the session menu's "Export to a file". The owner's device name and home path are redacted.
Tool outputs inside a transcript are stored by Crew truncated to 8,000 characters.

| File | What |
|---|---|
| `01-apps-discover.png`, `02-discover-causelens-detail.png` | Apps → Discover listing CauseLens from the `causelens-dev` registry, before install |
| `03-causelens-page.png`, `04-tab-*.png`, `05-console-after-link-click.png`, `pagecheck.json` | The installed page inside the dashboard: tabs, static label, a console source-link click |
| `slot-causelens-detective.json`, `06-detective-native-transcript.png` | **History: the first native Detective run (app 0.1.1)**, with the two failures (event match refused; decomposition too large) |
| `runs-0.1.2/detective-run2.json` | Fresh Detective session on app 0.1.2, reference data |
| `runs-0.1.2/scientist-run2.json` | Fresh Scientist session, reference data |
| `runs-0.1.2/experiment-designer-run2.json` | Fresh Experiment designer session (its `design_experiment` call was refused; see KIRO_NOTES) |
| `runs-0.1.2/detective-nochange.json` | Fresh Detective session with the installed data swapped for a no-change simulation, then restored |
| `runs-0.1.2/detective-missingdays.json` | Fresh Detective session with two days removed from the installed data, then restored |
| `runs-0.1.2/experiment-designer-run3-app-0.1.3.json` | Fresh Experiment designer session on app 0.1.3, small-input `design_experiment` |
| `runs-0.1.4/*.json` | History: fresh sessions on app 0.1.4 (Detective run3, Scientist run3, Designer run4, no-change Detective). Added thousands separators and two truncated numbers; see KIRO_NOTES |
| `runs-0.1.5/*.json` | History: fresh sessions on app 0.1.5. Numbers exact, but the no-change answer cited "past sessions" from Crew's carried-over memory |
| `runs-0.1.6/*.json` | History: fresh sessions on app 0.1.6 (Detective run5, Scientist run5, Designer run6, no-change Detective with the data swapped and restored). Every number matches a replayed tool output as a whole token |
| `runs-0.1.7-temporary/*.json`, `07-temporary-*.png` | Current: the three roles and the no-change control in fresh Crew **temporary** sessions (slot created with `memory_mode: "temporary"` before the first message, mode checked), memory-store counts before and after, and the real page creating one temporary slot per tab |

A new slot is not a clean context: Crew consolidates finished sessions into its shared memory store and later sessions can see those memories.
Runs from the 0.1.3 Designer run onward may have seen memories of earlier runs. The 0.1.6 prompts tell the agents to ignore them.
From app 0.1.7 every new CauseLens chat is temporary: Crew reads no stored memory into it and saves none from it.

| File | What |
|---|---|
| `08-crew-mcp-causelens-15-tools.png`, `mcp-probe/` | Crew's MCP Servers page after "Probe MCP servers": `causelens:causelens` Online, 15 tools including `daily_series`. Crew evidence, not a Kiro CLI panel; the home path is shown as `~` |
| `09-page-temporary-send.png`, `page-send-0.1.8/` | One message sent through the page's embed in a fresh temporary Detective chat (app 0.1.8): request carries `memory_mode: "temporary"`, slot stays temporary, answer completes |
| `10-session-menu-export.png`, `export-0.1.6-historical/` | **Historical, persistent, memory-enabled** 0.1.6 Detective session exported through the session menu (JSON and Markdown), with its Crew activity record. Crew does not export temporary sessions |

| File | What |
|---|---|
| `standalone-install-0.1.8/` | **New standalone install**, separate from the evidence above: CauseLens installed through Discover from `manjaria007/causelens-crew-app` `main` (`a165a0c`), dev mode off; Sources change, app-only trust prompt and install log, MCP probe with 15 tools, and one Detective run in a fresh Temporary session from the app page (tool calls and answer) |
