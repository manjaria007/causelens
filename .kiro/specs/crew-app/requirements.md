# Requirements: CauseLens Crew app

This is a separate integration after the ten metric-investigation tasks. The numerical engine,
identification rules, simulator and reference answer key remain authoritative and are not redesigned.
This specification was prepared as supporting work from the user's Crew delivery request; it is not
evidence that a native Kiro or Crew session has already run it.

## Installation and portability

1. Provide an installable app under `crew/causelens/`, with a valid `app.json`, three native agent
   definitions (Detective, Scientist and Experiment designer), and a Crew UI page showing the console.
2. Build prompts from `.kiro/steering/scientist-rules.md`, preserving its rules in each app-contained
   prompt. Record the source revision. Do not claim the existing Python role classes are native Crew agents.
3. Start the CauseLens MCP server through a relative launcher supported by the installed Crew version.
   The app must work after Crew copies it out of the source checkout. Required code, prompts and data
   must be packaged or installed explicitly; a relative path back into the original repo is insufficient.
4. No `/projects/sandbox/`, `/workspace/` or personal Mac path may be required by a committed app launcher.
   Installation and runtime dependencies must be declared. Never package credentials or the answer key.

## Investigation behavior

5. Use the existing read-only analytics through MCP. Discover its tools rather than invent a new SDK
   method; the current server exposes fifteen tools, including `daily_series`.
6. Give each native role an appropriate limited tool set. Every reported numerical result must come
   from an actual recorded tool response. No model arithmetic, fabricated calls or hard-coded verdicts.
7. On the reference data, a real Crew session must identify Checkout v4.2 under its evaluated lag,
   localize to mobile Safari, distinguish the hero hypothesis, and report the September 20 tracking issue.
   The reference evidence label is moderate because of lag ambiguity. Keep uncertainty and assumptions visible.
8. If evidence is insufficient, say so and recommend the experiment; do not print the planted answer.
9. Display the console in a supported Crew UI mount. Clearly distinguish the existing template-written
   console from any narrative actually produced by a live native agent.

## Installation and evidence

10. Install, trust and enable the app locally, and support `kirocrew app dev causelens` during development.
    Local installation appears in Library. Discover visibility requires a configured registry; do not
    claim the app is in the official catalog without a separate accepted submission.
11. Save a genuine Crew transcript with visible MCP calls and the final qualified answer. If demonstrating
    three native agents, capture their actual Activity entries and outputs. SDK logs are not native-session proof.
12. Record CLI/Crew versions and actual setup/doctor results. Capture the native Kiro MCP panel with fifteen
    tools, and prove a Stop hook fired automatically after an agent turn with a fresh timestamped log block.
    Do not substitute a manually invoked hook for automatic activation.

## Verification

13. Validate the app and agent schemas using the installed Crew release, test a copied install location,
    and run relevant app, MCP and console checks. Preserve the existing analytics decisions and reference data.
14. Use targeted checks while editing; run the full required suite once after the final source change.
    Update `KIRO_NOTES.md` after each completed block, recording limitations instead of invented proof.
