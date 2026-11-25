# CauseLens demo video

Target: **2:48**, with a hard editing limit of **2:50**. Record the application on the owner's Mac and publish the reviewed cut as a **public YouTube video under three minutes**. This is the Frankenstein submission demo; Best Demo consideration is automatic. Recording and publication are still owner actions: no video or YouTube URL is supplied here.

## Storyboard and narration

The narration below is approximately 300 spoken words. Keep text and tool rows readable at 1080p. Record longer source clips, then remove waiting with an on-screen **“Waiting trimmed”** caption. The finished video's length is not an end-to-end runtime claim.

| Time | Picture and captions | Narration |
|---|---|---|
| 0:00–0:14 | Actual CauseLens page in Crew. Title: **“Why did checkout conversion fall?”** Caption: **“Synthetic reference data”**. | Conversion fell. A release shipped nearby, but that coincidence cannot explain why. CauseLens investigates the change, challenges competing explanations, and recommends the experiment that would settle the uncertainty. |
| 0:14–0:38 | Briefly show Kiro's requirements, design and completed task plan, then `.kiro/steering/scientist-rules.md`. Show the three native Crew agent tabs. Caption: **“Three roles · separate chats · manual handoff”**. | Kiro drove the implementation task by task from these specs, steering rules and scientist rules. The native Crew app brings three specialist roles together with deterministic analytics over MCP. This demonstration uses synthetic metrics and logged events; the planted answer stays outside the app. |
| 0:38–1:01 | Detective's genuine Temporary chat: expand a successful MCP call and its response, then show the qualified localization. Keep the September tracking exclusion and FDR disclosure visible. | First, the Detective checks data quality before looking for a behavioral change. It flags the September tracking anomaly, confirms that conversion moved, estimates the onset, and localizes the decline to mobile Safari, with multiple-comparison control. Every finding stays tied to a recorded tool response. |
| 1:01–1:36 | Show the manual handoff of the dataset and windows, then the Scientist's separate Temporary chat. Expand `test_candidates` or `estimate_did`; keep the actual output visible beside the answer. Caption: **“Conditional on lag 0 · moderate evidence · release attribution ambiguous”**. | I hand the windows to the Scientist in a separate Temporary chat. Checkout is supported only under the immediate-effect assumption, with moderate evidence. The differential estimate is about minus zero point six eight percentage points; its interval, sample sizes and assumptions are visible. Hero with a one-day delay fits the same onset. These data cannot distinguish the releases. |
| 1:36–1:58 | Experiment designer's separate Temporary chat and expanded `design_experiment` response. Highlight the planning assumptions and rollback arms. Use the optional numerical caption below only if its source fields are visible. | Next, a separate Experiment designer sizes a randomized rollback, holding other releases fixed. The tool supplies the baseline, provisional effect target, session assumptions, sample size and duration. Its baseline uses the current post-change experience. The proposed experiment tests the release's current contribution. |
| 1:58–2:19 | Public GitHub Pages reference console, opened at its verified URL. Keep **“Static reference result”** on screen throughout. Click a provenance link to a tool-output panel. Caption: **“Static reference panels · generated from logged tool results”**. | The public reference console makes the evidence easy to inspect: quality, timing, localization, candidate checks and experiment design. These panels are a static reference result. The panels update when explicitly regenerated. The native agent answers come from the recorded Crew chats. |
| 2:19–2:39 | Crew's MCP page or existing genuine screenshot: **15 tools**, including `daily_series`. Cut to the timestamped automatic Kiro CLI Ruff Stop block, then the Temporary-mode indicator. Label each surface correctly. | Here are the fifteen registered MCP tools and Kiro's automatically fired Ruff Stop hook. Temporary sessions block shared memory retrieval and learning, keeping previous investigations out of these new chats. |
| 2:39–2:48 | Return to the Scientist's conditional answer and the proposed experiment. End card: repository and verified public reference-console URL. | CauseLens turns a metric alert into a qualified conclusion and a concrete next experiment. |

### Numerical and scientific captions

- Beside the Scientist's **actual recorded tool output**, use: **“Differential effect ≈ −0.680 pp; 95% CI ≈ [−0.774, −0.585]; 73 pre / 18 post days.”** These are display approximations of the reviewed reference result, not replacements for the full tool values. If the filmed run differs, update the caption from that run or omit it.
- Keep the assumptions beside the estimate: parallel trends, unaffected comparison traffic, no other concurrent differential change, and the evaluated lag. Show the tool's check results and ambiguity reason. Hero is rejected only as the **sole immediate explanation**; Hero at lag 1 remains indistinguishable from Checkout at lag 0.
- Optional Designer caption, **only when the corresponding fields from the filmed `design_experiment` response are visible**: **“10,216 sessions per arm · 7 days planned.”** Preserve the tool's provisional MDE label and session-independence/no-carryover assumptions. The duration includes whole-week rounding; do not portray it as measured experiment completion.
- Never equate the roughly −0.658 pp segment change with the roughly −0.680 pp differential estimate. Avoid an unconditional “Checkout caused the drop” title.

## Owner readiness and source shots

- [ ] Open the installed CauseLens page in the actual Mac Crew dashboard. Verify the three roles' fresh sessions report `temporary` **before sending**. Leave the memory database, historical sessions, other apps and global settings untouched.
- [ ] Capture at least one fresh successful MCP call through the page, with its response expanded. `list_datasets` is a short recording option; the longer role results can be shown from their genuine completed Temporary chats. Do not imply that three separate chats are an orchestrated parent/child run. Make the manual handoff visible.
- [ ] Have the reference Scientist and Designer outputs ready, including their assumptions, intervals and evidence labels. If using several sessions, caption the role changes and use the same dataset/windows/settings; do not splice incompatible results into one apparent investigation.
- [ ] Verify the public reference-console URL opens successfully before filming it. Until then, film the packaged console inside Crew with the same static label; do not claim a live Pages deployment.
- [ ] Prepare `.kiro/specs/metric-investigation/`, `.kiro/specs/crew-app/` and `.kiro/steering/scientist-rules.md`. Show the implementation history and task plan rather than claiming a prepared document alone proves Kiro usage.
- [ ] Prepare the actual automatic Stop-hook block in `.pytest_cache/stop-hook-runs.log`, with timestamp, clone `cwd` and `ruff_exit=0`. It is a **Kiro CLI Ruff hook**, not a pytest run or a Crew hook. Do not execute the hook command manually to manufacture evidence.
- [ ] Show the Crew MCP screenshot as **Crew evidence**, not a Kiro CLI MCP-panel screenshot. Confirm the fifteen-tool list and `daily_series` are legible.
- [ ] Keep the app's observed metrics/events separate from `data/ground_truth.json`, which is reserved for evaluation and excluded from the packaged app. Do not open the answer key during the investigation clip.
- [ ] Close unrelated tabs and notifications. Hide tokens, authentication screens, local usernames/home paths and private messages before recording; review every frame before publication. Save raw footage outside the repository.
- [ ] Review the final cut at normal speed: readable source responses, explicit static/native labels, no unconditional attribution, no invented Activity or child transcript, and duration at most 2:50. Upload only the reviewed cut and verify YouTube visibility is **Public**.

Useful existing evidence, with its limits:

| Source | Appropriate use |
|---|---|
| [Native evidence index](../crew-evidence/README.md) | Version and provenance guide; distinguish historical memory-enabled sessions from current Temporary sessions. |
| [Temporary role runs](../crew-evidence/runs-0.1.7-temporary/) and `07-temporary-*.png` | Genuine native role results and mode evidence; slot read-backs are not session-menu exports, and their stored tool outputs can be truncated. |
| [Page-send evidence](../crew-evidence/page-send-0.1.8/) and [page screenshot](../crew-evidence/09-page-temporary-send.png) | Actual embedded Temporary send, including a successful `list_datasets` call. The earlier unsuccessful tool-name attempt is documented; do not describe the run as flawless. |
| [Crew MCP screenshot](../crew-evidence/08-crew-mcp-causelens-15-tools.png) and [probe record](../crew-evidence/mcp-probe/) | Crew 0.13.1 registration/probe evidence for fifteen tools. |
| [Kiro notes](../../KIRO_NOTES.md) | Actual Kiro work, automatic Ruff-hook observations and native-run limitations. |
| [Historical session-menu export](../crew-evidence/export-0.1.6-historical/) | Label **“Historical · persistent · memory-enabled”** if shown. It is not clean Temporary-session proof; exported files omit tool rows. Crew refuses Temporary-session export, so preserve that guard. |

## Prompt for Kiro CLI on the owner's Mac

```text
Prepare and help record the CauseLens demo on this Mac using
docs/submission/video-script.md. This is recording work, not implementation.
Do not rerun the full test suite, alter datasets, delete sessions or memory,
change global Crew settings, or bypass Temporary-session export restrictions.

Verify the existing clone and installed CauseLens app, then arrange the actual
Crew UI and relevant source windows for the 2:48 storyboard. Use fresh Temporary
sessions for any new native calls and verify their mode before the first send.
Use the reviewed role prompts and preserve the same dataset, windows and
candidate-test settings across manual handoffs. Never copy a planted answer.

Capture a real successful MCP call through the page and its expanded response,
then the three genuine role chats with the Scientist's conditional moderate
answer and the Designer's tool-returned plan. Existing completed Temporary
role chats may be used when available; label separate sessions accurately.
If a call fails or a response is truncated, show or report that limitation and
do not fill the gap with a plausible answer.

Use the Mac's screen recorder (Shift-Command-5) for the actual-device footage.
If you cannot operate it, arrange the shots and give me the exact recorder steps;
do not claim that a video has been recorded. Keep raw footage outside the repo.
Capture the Kiro specs/steering, real automatic Ruff Stop log, and fifteen-tool
Crew MCP panel. Label the GitHub Pages console as static, after verifying its URL.

Trim waiting with clear captions, preserve the tool values and assumptions,
and keep the final cut at most 2:50. Check for credentials and private material.
Report the real recording file and any missing shots. Do not publish or invent
a YouTube URL; I will review the cut and publish it publicly.
```
