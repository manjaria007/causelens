# Crew and public-demo handoff

The ten metric-investigation tasks are merged, including PR #11 at `f3eba9d`.
The separate [crew-app specification](../.kiro/specs/crew-app/tasks.md) is ready for Kiro to implement.
Prepared specification/configuration files are not evidence of a native Crew run.

## What was prepared here

- A clone-relative workspace MCP command: `/bin/bash scripts/mcp_stdio.sh`. Start Kiro from the clone root;
  the launcher resolves its own root and `PYTHONPATH`. No sandbox-specific path is required.
- A result-driven console headline. It names a likely cause only when the completed investigation has
  one supported candidate with an allowed DiD attribution; other results receive a neutral headline.
- The generated static reference console at `docs/demo/index.html` and `docs/.nojekyll` for Pages.
- An isolated cloud installation of Crew 0.13.1, with `setup --agent-only` and `doctor` run. This does not
  install Crew or authenticate a native session on the user's Mac. Full local setup and proof remain below.

## Start in the correct repository

From the existing CauseLens clone on the Mac:

```bash
pwd
git remote get-url origin
git status --short
```

The remote must be `manjaria007/causelens`. The parent directory's name does not make this the One Number project.
Back up the existing uncommitted Mac MCP override before replacing it with the merged portable config:

```bash
causelens_backup_dir=$(mktemp -d /tmp/causelens-handoff.XXXXXX)
cp .kiro/settings/mcp.json "$causelens_backup_dir/mcp.json"
git diff -- .kiro/settings/mcp.json > "$causelens_backup_dir/mcp.patch"
git fetch origin
```

With the backup confirmed, have Kiro restore only this tracked file, switch to `main`, and fast-forward
from `origin/main`. Reapply any unrelated server entries from the backup if there are any; keep the new
CauseLens entry relative. Leave other uncommitted files alone. Do not commit the old Mac path. Launch
the new native Kiro session from the clone root.

## Install Crew on the actual gateway host

Use the official desktop installer or the official CLI installer:

```bash
curl -fsSL https://download.crew.kiro.dev/cli.sh | sh
kirocrew setup
kirocrew doctor
kirocrew gateway
```

Crew requires Python 3.12+; its installer can provision it. Verify the actual selected backend and its sign-in
in doctor on this machine. Follow Crew's current setup prompt for missing authentication; the cloud install
does not share the Mac's Kiro login. The prebuilt wheel does not need Node; a UI build may need Node 24 LTS.
See [official installation](https://kiro.dev/docs/crew/installation/).

## Prompt to give Kiro

> Work only in the CauseLens repository, `manjaria007/causelens`. Read `.kiro/specs/crew-app/requirements.md`,
> `design.md`, `tasks.md`, the existing steering, and `docs/crew-handoff.md`. Preserve my uncommitted Mac MCP
> settings via the documented backup. Implement the crew-app spec one parent task at a time. Use the actual
> installed Crew scaffold and schemas. Create the installable `crew/causelens/` app, three native agents with
> scientist-rule prompts, a supported relative MCP launcher that survives app installation, and the console UI.
> Preserve the numerical engine and answer-key separation. Configure the personal Discover registry,
> install and enable from its listing, use app dev mode,
> and run an actual Crew investigation with recorded MCP calls. Capture the native evidence below. Record
> actual results and limitations in `KIRO_NOTES.md`; do not invent transcripts, screenshots, hook execution
> or model responses. Open a PR with focused tests and final required checks.

## Capture native proof on the Mac

Use a short screen recording plus the original exported files:

1. **Crew session.** In a genuine native session, ask why checkout conversion fell. Show real MCP calls
   and the final qualified answer. If demonstrating three agents, show their actual Activity entries and
   outputs. Session-tab menu → **Export to a file**. Export child output promptly. A static console or a
   terminal MCP probe is not a Crew transcript. [Sessions](https://kiro.dev/docs/crew/chat/sessions/)
2. **MCP panel.** Reconnect CauseLens, show it running with **15 tools**, and show `daily_series` in the list.
   Make a fresh attached call. The historical 14-tool CLI verification predates this tool.
3. **Repository Stop hook.** In native Kiro CLI V3, verify `.venv/bin/python -m ruff --version`. Capture UTC
   time and the current tail of `.pytest_cache/stop-hook-runs.log`, let a harmless agent turn finish, then
   capture a new automatic start/end block with this clone's `cwd` and `ruff_exit=0`. Do not invoke the
   hook command manually to manufacture proof. [Hook schema](https://kiro.dev/docs/hooks/)

Crew has a separate **Agent Capabilities → Hooks** screen. Configure a Crew hook there if proving Crew's
own Stop event; do not assume Crew imports the repo's CLI hook. Its Test button is manual testing only.
[Crew hooks](https://kiro.dev/docs/crew/capabilities/hooks/)

## Enable the public reference console

The intended URL is **https://manjaria007.github.io/causelens/demo/**. It is not confirmed live until Pages
has been enabled and that URL has been opened successfully.

In [repository Pages settings](https://github.com/manjaria007/causelens/settings/pages), choose **Deploy from
a branch → main → /docs → Save**. Wait for the Pages deployment and check the `/demo/` URL.

In the repository About settings, use this website URL and remove the leading `Description:` from the text:

> Autonomous AI investigation agent that explains why business metrics changed, validates causes with
> statistical and causal analysis, and recommends the next experiment. Built for Kiroween 2026 with Kiro.

These settings require an authenticated GitHub settings session. The available connector has no Pages/About
mutation tools, and the cloud GitHub CLI failed authentication; no settings change or public deployment is
claimed from preparing the files.

Regenerate the static console from the clone root after preparing the reference dataset:

```bash
python -m causelens.ui --out docs/demo/index.html
```

This is a static reference demo, not a live hosted-model session. Keep the moderate evidence label, lag
ambiguity, uncertainty, alternatives and data-quality warning visible.
