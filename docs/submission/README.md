# Submission package

Drafts for the owner's review; no Devpost entry, video upload or bonus eligibility is claimed from these files.

| Material | File or URL | Status |
|---|---|---|
| Public reference console | https://manjaria007.github.io/causelens/demo/ | Owner confirmed it opens on November 25, 2026; static reference investigation |
| Source code | https://github.com/manjaria007/causelens | Implementation and Crew evidence merged in `d57e4b7` |
| Standalone Crew app | https://github.com/manjaria007/causelens-crew-app | Published installation copy; fresh Mac install and Detective run pending |
| Devpost description and Crew bonus text | [devpost-draft.md](devpost-draft.md) | Draft for review |
| Kiro usage write-up | [kiro-writeup.md](kiro-writeup.md) | Draft for review |
| Video script and Mac recording instructions | [video-script.md](video-script.md) | 2:48 outline; recording and public upload pending |
| Published video | Not supplied | Add the real public YouTube URL to the README and Devpost after upload |

## Deadline and submission checks

Checked against the [official rules](https://kiroween.devpost.com/rules) and [event page](https://kiroween.devpost.com/) on November 25, 2026.
The deadline is **December 4, 2026, 2:00 PM PST / 5:00 PM EST**. Aim to submit by **December 3** as a buffer, not as a different official deadline.

- [ ] Confirm registration, personal/team eligibility and that the submitted work meets the new-project requirement. Disclose preparation done before the Kiro build.
- [ ] Review the drafts and select **Frankenstein** as the proposed category.
- [ ] Add the repository and working-demo URLs. Keep the demo's static-reference label clear.
- [ ] Record and review the video, keep it under three minutes, publish it publicly on YouTube, and add its URL.
- [ ] Paste the Kiro usage write-up and the Crew app features/repository link.
- [ ] Verify the standalone Crew install below, then use its repository URL in the Crew bonus field. Best Demo has no additional entry steps; eligible submissions are considered automatically.
- [ ] Save and submit the Devpost form, then verify its submitted status and public links.

## Crew bonus: standalone installation repository

The prize table uses **"separate repo submission"** and says the GitHub account must match the overall submission. To cover that reading, the app is now published separately at https://github.com/manjaria007/causelens-crew-app, under the same owner as the main project. It contains `app.json` at the root, the MIT license, packaged runtime/data/UI and installation instructions. `SOURCE.json` pins the source commit and app subtree; development history and Kiro specs remain in the main project.

The root registry points directly to that repository's `main` and has no `subdirectory` field. The source app remains in `crew/causelens/`; no implementation was moved or deleted. This publication avoids depending on a subdirectory interpretation, but does not claim organizer approval or eligibility on every other condition.

The standalone package passed manifest, agent, file-identity and MCP subprocess checks. A new install from this repository and a native Detective run on the Mac are still pending; the earlier native Crew evidence comes from the original source. Follow [the switch and verification instructions](../crew-standalone-install.md), record actual results, then use the separate URL in the bonus field. No organizer response is needed to proceed with that check.

## Final evidence and remaining owner work

The [native evidence index](../crew-evidence/README.md) separates the Temporary role runs, real page send, Crew MCP screenshot and historical persistent-session exports. The three roles are separate sessions, not a parent/child conversation. Keep those distinctions in the video and write-up.

Use the recording prompt in [video-script.md](video-script.md) with Kiro CLI on the Mac. Review the final cut before publishing. Once the real video URL is available, replace the README's literal `<YouTube link>` placeholder. These drafts do not need another numerical calibration or full test run; the code is unchanged.
