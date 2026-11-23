# Design: CauseLens Crew integration

Reuse the completed CauseLens package. Crew supplies the native agent/chat surface and an app page;
the current deterministic tools remain the numerical engine. Do not replace the existing pipeline.

## Validate against installed Crew

The cloud preparation used Crew 0.13.1. Confirm the version on the actual gateway host and use its
scaffold and validator before writing configuration:

```bash
kirocrew app init --help
kirocrew app init causelens --dir crew --ui
kirocrew agent validate --print-schema
```

Inspect where the scaffold was written before editing it. The manifest requires `name`, `version`,
`displayName` and `description`. Source documentation describes app-contained agent prompts such as
`file://{APP_ROOT}/prompts/detective.md`. Do not invent template variables in `app.json`.

## Installable layout

The app root is `crew/causelens/`. Keep its agent definitions, generated prompt files, UI build and
relative MCP entry point inside it. Crew copies this subtree when installing and excludes `.venv`,
`.git` and `node_modules`; reaching `../../causelens` in the source checkout will not survive that copy.
Use a documented packaging/install step to bring in the required runtime code and observed data.
Do not include `data/ground_truth.json` in the app bundle or its accessible tool inputs.

Current source documentation specifically anchors an existing relative `.py` argument to a bare
`python` stdio command at registration. It does not generally rebase arbitrary Bash arguments.
Verify that behavior in the installed release and choose a contained launcher accordingly. A repo's
workspace MCP config and a Crew app manifest are different launch contexts.

## Roles and evidence

The three native definitions carry the scientist rules plus their existing role responsibilities.
Use supported Crew coordination and record actual tools called by each role. Shared observations may
use existing typed results/artifacts; avoid requiring the model to reconstruct arithmetic or edit values.
Keep the answer key outside the agent path. Qualify the reference attribution by its evaluated lag,
DiD assumptions and moderate release-ambiguous evidence.

## UI

Use the installed scaffold's supported ESM `mount` contract. In the source manifest reference,
`ui.entry: "dist/index.mjs"` resolves under `APP_ROOT/ui/dist/index.mjs`. A packaged console can be
shown as an app-contained asset, with a clear label if it is a static reference result. For a native
chat surface, use documented `ChatEmbed`/chat-launcher support, or a permission-scoped app backend.
Do not invent `callTool` SDK methods or an `/api/mcp/call` route.

## Local app and Discover

Install and enable the app in the local Library first, review its trust request, then run dev mode.
For personal Discover visibility, use the documented external-registry workflow and an explicit
repo-root `app-registry.json` entry selecting `crew/causelens`. Validate its actual schema rather
than guessing. Public official-store publication is a separate maintainer workflow, not part of this task.

## Official references

- https://kiro.dev/docs/crew/installation/
- https://kiro.dev/docs/crew/apps/
- https://kiro.dev/docs/crew/apps/manifest/
- https://github.com/kirodotdev/KiroCrew/blob/bac37ae9a1cd8bfbcb698c33151587ccad91852d/docs/app-kit/manifest-reference.md
- https://github.com/kirodotdev/KiroCrew/blob/bac37ae9a1cd8bfbcb698c33151587ccad91852d/docs/app-kit/api-reference.md
- https://github.com/kirodotdev/KiroCrew/blob/bac37ae9a1cd8bfbcb698c33151587ccad91852d/docs/app-kit/publishing-guide.md
