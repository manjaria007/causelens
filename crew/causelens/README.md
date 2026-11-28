# CauseLens (Kiro Crew app)

Three native Crew agents (Detective, Scientist, Experiment designer) that investigate why a business metric moved, using the CauseLens read-only
analytics over MCP. The numerical engine, identification rules and scientist rules are the repository's; this app packages them for Crew.
It ships a small reference dataset (observed metrics and events only; no answer key).

Published copy for installation: https://github.com/manjaria007/causelens-crew-app.

## Layout

| Path | What |
|---|---|
| `app.json` | manifest: agents, skill, the MCP server (`python3 mcp_server.py`), UI page |
| `mcp_server.py` | relative launcher; puts `runtime/` on the path and serves `data/` |
| `requirements.txt` | runtime dependencies Crew provisions for the stdio server |
| `runtime/causelens/` | generated: the analytics and MCP code (with `PACKAGE.json` hashes) |
| `data/` | generated: `metrics_daily.csv` (seed 42) and `events.csv` |
| `prompts/` | generated: one prompt per agent with `scientist-rules.md` verbatim (`SOURCE.json` records its hash and commit) |
| `agents/`, `skills/` | native agent definitions and the coordination skill |
| `ui/` | the Crew page (source in `ui/src`, built `ui/dist/index.mjs`) and the packaged static reference console |

Build the page: `cd ui && npm ci --legacy-peer-deps && npm run build` (the flag is needed because `@kirocrew/app-sdk` is provided by the Crew host and is not on npm). Test it: `npm test` (unit tests, a render against test stubs, and a link-click regression in headless Chrome over an HTTP parent page; none of it is Crew). The browser test skips itself without Chrome; set `CHROME` to choose one.

Regenerate the generated parts from the repository root: `python scripts/package_crew_app.py` (`--check` reports drift).

## Install

```bash
kirocrew app install crew/causelens && kirocrew app enable causelens   # local, for preflight
kirocrew app dev causelens                                              # development mode
```

Or add the repository's root `app-registry.json` as an app registry and install the standalone published copy from Apps → Discover. See the
[repository run instructions](https://github.com/manjaria007/causelens#run-it-in-kiro-crew) and
[native evidence](https://github.com/manjaria007/causelens/tree/main/docs/crew-evidence).

New app chats use Crew's Temporary mode: stored memory is neither read nor learned from these sessions. Crew 0.13.1
does not export Temporary sessions; the evidence index distinguishes their records from the historical persistent-session export.
