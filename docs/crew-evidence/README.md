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
