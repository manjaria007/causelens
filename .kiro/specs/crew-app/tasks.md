# Crew app implementation plan

All boxes start unchecked: cloud preparation is not native Kiro/Crew proof. Run one parent task at a time in Kiro.

- [x] 1. Verify the actual Mac/gateway environment
  - [x] 1.1 Confirm the Git remote is `manjaria007/causelens`; back up the unstaged Mac MCP config before updating it
  - [x] 1.2 Install Crew, run `kirocrew setup --agent-only` and `kirocrew doctor`, and verify the selected Kiro agent backend is authenticated (the interactive wizard was not run and is not required for this app)
  - [x] 1.3 Verify fifteen native MCP tools, including `daily_series`, with a fresh attached call in a native Kiro session (the MCP-panel screenshot is task 5.2)
- [x] 2. Build and validate the installable app
  - [x] 2.1 Scaffold `crew/causelens/` and validate the manifest against installed Crew
  - [x] 2.2 Add three native agent definitions and app-contained prompts generated from scientist rules
  - [x] 2.3 Package the MCP runtime/data and validate its supported relative launcher from a copied app install
- [x] 3. Integrate the console and native investigation
  - [x] 3.1 Add the Crew UI page using the supported mount/SDK contract (verified inside the installed Crew dashboard: three agent tabs, static label, srcdoc link navigation)
  - [x] 3.2 Verify native agents make real MCP calls and preserve all qualified numerical evidence (Detective and Scientist on app 0.1.2, Experiment designer on 0.1.3)
  - [x] 3.3 Exercise insufficient-evidence and missing-data behavior without fixed reference claims (native Detective sessions on a no-change and a missing-day dataset)
- [x] 4. Install and enable in Crew
  - [x] 4.1 Configure the documented opt-in registry and verify CauseLens appears in Apps → Discover
  - [x] 4.2 Install from Discover, review trust, enable, and run `kirocrew app dev causelens`
- [x] 5. Capture genuine proof
  - [x] 5.1 Run the reference question in Crew; capture Activity and export the transcript through the session menu (historical, persistent, memory-enabled 0.1.6 Detective session; Crew 0.13.1 exports no temporary session; the agents are separate sessions, so there is no parent/child pair)
  - [x] 5.2 Capture Crew evidence of the CauseLens MCP server with fifteen tools: Crew's MCP Servers page after a probe, `causelens:causelens` Online with 15 tools including `daily_series` (not a Kiro CLI screenshot)
  - [x] 5.3 Observe a fresh automatic Stop-hook log block after a real native agent turn
  - [x] 5.4 Update `KIRO_NOTES.md`, run required final checks once, and open a reviewable PR (PR #13, draft and unmerged until the owner's final evidence review)
