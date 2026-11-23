# Crew app implementation plan

All boxes start unchecked: cloud preparation is not native Kiro/Crew proof. Run one parent task at a time in Kiro.

- [ ] 1. Verify the actual Mac/gateway environment
  - [ ] 1.1 Confirm the Git remote is `manjaria007/causelens`; back up the unstaged Mac MCP config before updating it
  - [ ] 1.2 Install Crew, run interactive setup and doctor, and verify the selected Kiro agent backend is authenticated
  - [ ] 1.3 Reconnect CauseLens and verify fifteen native MCP tools, including `daily_series`
- [ ] 2. Build and validate the installable app
  - [ ] 2.1 Scaffold `crew/causelens/` and validate the manifest against installed Crew
  - [ ] 2.2 Add three native agent definitions and app-contained prompts generated from scientist rules
  - [ ] 2.3 Package the MCP runtime/data and validate its supported relative launcher from a copied app install
- [ ] 3. Integrate the console and native investigation
  - [ ] 3.1 Add the Crew UI page using the supported mount/SDK contract
  - [ ] 3.2 Verify native agents make real MCP calls and preserve all qualified numerical evidence
  - [ ] 3.3 Exercise insufficient-evidence and missing-data behavior without fixed reference claims
- [ ] 4. Install and enable in Crew
  - [ ] 4.1 Configure the documented opt-in registry and verify CauseLens appears in Apps → Discover
  - [ ] 4.2 Install from Discover, review trust, enable, and run `kirocrew app dev causelens`
- [ ] 5. Capture genuine proof
  - [ ] 5.1 Run the reference question in Crew; capture Activity and export the parent/child transcript
  - [ ] 5.2 Capture a native MCP screenshot showing fifteen tools
  - [ ] 5.3 Observe a fresh automatic Stop-hook log block after a real native agent turn
  - [ ] 5.4 Update `KIRO_NOTES.md`, run required final checks once, and open a reviewable PR
