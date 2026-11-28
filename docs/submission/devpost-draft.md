# CauseLens

**Tagline:** Investigate metric changes with reproducible statistics, honest uncertainty and a testable next step.

Owner-review draft. The public reference-console link was verified by the owner; add the real published video URL before submission. Nothing has been submitted.

## Inspiration

A conversion drop often arrives beside several releases and a tracking incident. Separating a measurement problem, a coincident event and evidence for an explanation is harder than telling a convincing story.

## What it does

CauseLens checks data quality, confirms and dates a change, localizes affected segments, tests explanations, estimates differential impact and recommends an experiment. Its console shows each step's evidence. Deterministic tools supply the numbers; agents interpret recorded responses under scientist rules.

## How it was built

Python, pandas, SciPy and statsmodels form the engine, with Pydantic results and pytest/Hypothesis checks. Fifteen read-only MCP tools connect it to Kiro and three native Crew roles: Detective, Scientist and Experiment designer. The app packages its runtime and observed data, a React page and a labelled static reference console.

## Reference result and limits

On the synthetic reference, the tools flag September 20's tracking incident and localize the decline to mobile Safari. Checkout v4.2 is supported **under the immediate-effect assumption, lag 0**; Hero is rejected as the sole immediate explanation. Evidence is **moderate**: Hero acting one day later has the same onset, preventing separation of the releases.

The DiD estimate is −0.680 percentage points, with a 95% interval of −0.774 to −0.585, using 73 pre-period and 18 post-period days. Attribution assumes parallel trends, unaffected comparison traffic and no concurrent differential change; passing diagnostics does not prove those assumptions.

The planted immediate-effect scenario was recovered in 124/150 simulated runs: 83%, with a 95% Wilson interval of 76–88%. This is scenario-specific simulation evidence, not general accuracy. Delayed effects can be attributed to the wrong release; staged effects also expose failures. Full tables and limitations are in [candidate testing](../candidate-testing.md) and [evaluation](../evaluation.md).

## Frankenstein category rationale

CauseLens stitches together statistical identification, a synthetic test bed, typed MCP tools, Kiro specifications, native Crew agents and an evidence console. The useful combination is an agent interface whose numerical work remains inspectable and reproducible.

## Challenges, accomplishments and lessons

Challenges included exposure scope versus expected effect pattern, adjacent-release ambiguity, oversized responses and session memory. Compact typed responses preserve the full API; Crew chats use verified Temporary settings. Native runs demonstrate real calls and an insufficient-evidence control. The roles use separate sessions with a handoff, not automated parent/child coordination.

Matching numbers is insufficient: estimand, uncertainty and wording matter. Next steps are broader scenarios, real-data evaluation and stronger narrative checks.

## Crew bonus description and links

The CauseLens Crew app provides the three native roles, a copied-install-safe MCP launcher, fifteen tools and a console page. Native evidence is indexed in [the evidence directory](../crew-evidence/README.md).

- Project: https://github.com/manjaria007/causelens
- Reference demo: https://manjaria007.github.io/causelens/demo/
- Crew app repository: https://github.com/manjaria007/causelens-crew-app
- Development source and history: https://github.com/manjaria007/causelens/tree/main/crew/causelens

The standalone app repository is under the same GitHub account as the project and has `app.json` at its root. It contains the packaged app and MIT license, with a source-commit link back to the Kiro specifications and development history. Before submission, verify a fresh native Crew install from that repository and record its Detective run; the existing native evidence predates this publication. See the [submission checklist](README.md).

Deadline: **December 4, 2026, 2 p.m. PST / 5 p.m. EST**. Supply a **public YouTube demo under three minutes**. Best Demo consideration is automatic. Check the [official rules](https://kiroween.devpost.com/rules) before submission.
