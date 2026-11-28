# Verify the standalone Crew installation

The main project retains its development history, specifications and `crew/causelens/` source package. The standalone installation repository is `https://github.com/manjaria007/causelens-crew-app`, with `app.json` at the root and the same app name, `causelens`. Its registry entry has no `subdirectory` field.

Publishing the copy and validating its MCP process do not prove a new native Crew installation. Record the Mac reinstall and agent run below only after they actually happen.

## Kiro instructions on the Mac

Work only on CauseLens. Preserve unrelated apps, the Mac MCP override, old transcript files and Crew's memory database. Inspect the installed app's current source and dev-mode status before switching; back up any local app edits. If you want an export of a historical persistent session, export it before uninstalling; Crew refuses Temporary-session menu exports. Uninstall preserves transcript files and app data by default, but removes the app's grants and native resume pointers. Do not promise the old conversations remain resumable.

1. Check that the public standalone repository has `app.json`, `LICENSE`, `runtime/`, observed `data/`, and `ui/dist/index.mjs` at the expected paths. Read its `SOURCE.json`.
2. Disable CauseLens dev mode if active (`kirocrew app dev causelens --off`), then uninstall the old app with `kirocrew app uninstall causelens`. Do not pass `--purge-data`.
3. In **Apps → Discover → Sources**, replace the old CauseLens registry source with `https://github.com/manjaria007/causelens-crew-app`. Refresh and confirm its registry entry points at the new repository's `main`, without `subdirectory`. Preserve other registry sources. Duplicate `causelens` entries may select the old source, so remove that ambiguity.
4. Install from Discover, review the app-specific trust prompt, and enable CauseLens. Approve only this app/source; leave global third-party execution unchanged. Keep dev mode off so the test uses the installed copy, not a checkout.
5. Confirm the installed source is `manjaria007/causelens-crew-app`, version `0.1.8`, the three agents resolve their tools, and Crew reports `causelens:causelens` online with fifteen tools including `daily_series`.
6. From the app page start a new **Temporary** Detective session. Confirm its effective mode, then ask **"Why did checkout conversion fall?"** Let it call the real MCP tools. Verify the reference dataset, September 20 exclusion and the localized segment from the current tool output. Detective supplies when/where, not a causal verdict. Do not insert the planted answer into its prompt.
7. Save a redacted read-back of the Temporary session and its tool calls, and screenshots showing the new source and MCP status. Temporary chats cannot be exported through Crew's session menu; do not create a persistent session just for an export.
8. Append actual results, installed source commit, versions and any errors to `KIRO_NOTES.md` and the evidence index. State that this is a new standalone install, separate from the earlier native evidence. Do not overwrite historical evidence or mark this verification complete beforehand.

The direct CLI alternative, if Discover cannot load the registry, is:

```bash
kirocrew app install https://github.com/manjaria007/causelens-crew-app --ref main
kirocrew app enable causelens
```

Label a CLI installation accurately; it does not prove a Discover installation. Do not rerun the statistical calibration or full test suite for a publication copy with unchanged runtime.

## Devpost links

- Main project repository: https://github.com/manjaria007/causelens
- Crew app repository: https://github.com/manjaria007/causelens-crew-app
- Static reference demo: https://manjaria007.github.io/causelens/demo/

Use the separate app URL in the Crew bonus field once publication and the new install are verified. This does not claim organizer approval or a completed Devpost submission.
