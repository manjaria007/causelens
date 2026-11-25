# Crew and public-demo handoff

The ten metric-investigation tasks and the complete [crew-app specification](../.kiro/specs/crew-app/tasks.md)
are implemented and merged, including PR #13 at `d57e4b7`. Native installation, three-role runs, Temporary
sessions, page sending and the fifteen-tool Crew MCP panel are recorded in [the evidence index](crew-evidence/README.md).
The steps below explain how to reproduce setup on another host; they are not unfinished implementation tasks.

## What was prepared here

- A clone-relative workspace MCP command: `/bin/bash scripts/mcp_stdio.sh`. Start Kiro from the clone root;
  the launcher resolves its own root and `PYTHONPATH`. No sandbox-specific path is required.
- A result-driven console headline. It names a likely cause only when the completed investigation has
  one supported candidate with an allowed DiD attribution; other results receive a neutral headline.
- The generated static reference console at `docs/demo/index.html` and `docs/.nojekyll` for Pages.
- An isolated cloud installation of Crew 0.13.1, with `setup --agent-only` and `doctor` run. This historical
  preparation is distinct from the later native Mac installation and proof in `docs/crew-evidence/`.

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

## Prompt to reproduce the installed app

> Work only in `manjaria007/causelens`. Follow the README's "Run it in Kiro Crew" instructions for the
> already implemented app. Preserve existing Mac settings and sessions. Install and enable CauseLens,
> review its app-specific trust request, and verify the three role tabs and fifteen MCP tools. Start any
> new investigation in Temporary mode and confirm its effective mode before sending. Record actual
> results and limitations; do not rerun completed implementation tasks or claim a static preview is a
> native agent run. Use `docs/submission/video-script.md` if preparing submission footage.

## Capture native proof on the Mac

Use a short screen recording plus the original exported files:

1. **Crew session.** Ask why checkout conversion fell in a fresh Temporary session. Show real MCP calls
   and the qualified answer. The three native roles use separate sessions with handoffs, not a parent/child
   conversation. Crew 0.13.1 refuses Temporary-session exports. The recorded session-menu JSON/Markdown
   exports are therefore labelled historical, persistent and memory-enabled; tool calls are in the separate
   slot read-back. A static console or terminal probe is not a native agent run.
2. **MCP panel.** In Crew's MCP Servers page, probe `causelens:causelens`, show **Online, 15 tools**, and show
   `daily_series` in the list. Label this Crew evidence, not a Kiro CLI screenshot. Make a fresh attached call.
3. **Repository Stop hook.** In native Kiro CLI V3, verify `.venv/bin/python -m ruff --version`. Capture UTC
   time and the current tail of `.pytest_cache/stop-hook-runs.log`, let a harmless agent turn finish, then
   capture a new automatic start/end block with this clone's `cwd` and `ruff_exit=0`. Do not invoke the
   hook command manually to manufacture proof. [Hook schema](https://kiro.dev/docs/hooks/)

Crew has a separate **Agent Capabilities → Hooks** screen. Configure a Crew hook there if proving Crew's
own Stop event; do not assume Crew imports the repo's CLI hook. Its Test button is manual testing only.
[Crew hooks](https://kiro.dev/docs/crew/capabilities/hooks/)

## Enable the public reference console

The reference console is at **https://manjaria007.github.io/causelens/demo/**; the owner confirmed it opens
on November 25, 2026. It is a static exported investigation. The steps below are for configuring a new fork.

In [repository Pages settings](https://github.com/manjaria007/causelens/settings/pages), choose **Deploy from
a branch → main → /docs → Save**. Wait for the Pages deployment and check the `/demo/` URL.

In the repository About settings, use this website URL and remove the leading `Description:` from the text:

> AI investigator for business metric changes, with statistical evidence and experiment recommendations. Built with Kiro.

These settings require an authenticated GitHub settings session. Preparation of the files alone did not prove
deployment; the current live status comes from the later owner verification.

Regenerate the static console from the clone root after preparing the reference dataset:

```bash
python -m causelens.ui --out docs/demo/index.html
```

This is a static reference demo, not a live hosted-model session. Keep the moderate evidence label, lag
ambiguity, uncertainty, alternatives and data-quality warning visible.
