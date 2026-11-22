# Ground-truth evaluation (Task 9)

`python -m causelens.eval --seeds 20` runs a full investigation on each of 20 simulated datasets and grades it against the
simulator's answer key (Requirements 10.1 to 10.3). It is the check that the whole pipeline (quality, confirmation, change point,
localization, candidate tests, estimate) still recovers the planted answer.

```bash
python -m causelens.eval --seeds 20                 # seeds 42 to 61, prints the table; exit 1 if seed 42 fails a criterion
python -m causelens.eval --seeds 20 --json out.json # also writes the full report (every seed's findings and criteria)
python -m causelens.eval --seeds 3 --allow-fewer-seeds   # development only; the report says it is below the minimum
```

Options: `--first-seed` (default 42, the reference), `--truth` (the reference key, default `data/ground_truth.json`). Fewer than 20
seeds is refused unless `--allow-fewer-seeds` is given. Exit status is 0 when the reference seed passes every criterion, 1 when it
fails one, 2 for bad arguments or an untrustworthy answer key. The rates over all seeds are reported, not gated: the requirement is to
report how often each criterion passes, and no threshold was specified.

## What is graded

For each seed the simulator's metrics and events are written to a private temporary directory and served by the Task 7 MCP server
(in process, through the official client). The Task 8 orchestrator investigates with the same windows every time (before the planted
release, and from it to the end of the data) and the default template reasoner. The answer key is not written there. It is loaded before the run (seed 42's is also
verified then), never passed to the server or the orchestrator, and used only for grading. A test checks the directory holds only `metrics_daily.csv` and `events.csv`; the server also refuses to
serve any file named like the key.

For seed 42 the key is `data/ground_truth.json` itself, checked to equal what the simulator produces for that seed (a stale or edited
file is an error, not a grade). For other seeds the key is the one the simulator produces for that seed, with that seed's own true
effect.

Five criteria (Requirement 10.2). A criterion passes only on positive evidence in the result, so an investigation that stopped, found
no estimate, or never localized fails what it did not establish.

| Criterion | Passes when |
|---|---|
| `identifies_cause` | the only supported candidate is Checkout v4.2 |
| `localizes_segment` | the localization is exactly one segment, mobile x Safari |
| `rejects_decoy` | Homepage hero refresh has verdict `rejected` (not merely unsupported) |
| `flags_data_quality` | 2026-09-20 is both flagged and excluded |
| `ci_covers_truth` | the 95% difference-in-differences interval for Checkout v4.2 contains the seed's true segment effect (`true_effect_in_segment_pp`) |

The overall result passes only if all five pass. Also reported: how often a wrong event was supported (an incorrect attribution),
the status and evidence-strength counts, and how often the interval contains the true effect among the runs that produced one (the criterion
counts a run with no interval as a miss; this separates "no estimate" from "an estimate that missed"). Rates carry 95% Wilson
intervals.

## Result on seeds 42 to 61

Run once on the committed code; the command is deterministic, so it reproduces.

| Criterion | Passes | Rate | 95% Wilson interval | Failed seeds |
|---|---|---|---|---|
| `identifies_cause` | 16/20 | 80% | [58.4%, 91.9%] | 43, 45, 52, 53 |
| `localizes_segment` | 19/20 | 95% | [76.4%, 99.1%] | 59 |
| `rejects_decoy` | 18/20 | 90% | [69.9%, 97.2%] | 43, 52 |
| `flags_data_quality` | 20/20 | 100% | [83.9%, 100.0%] | none |
| `ci_covers_truth` | 14/20 | 70% | [48.1%, 85.5%] | 43, 45, 52, 53, 57, 59 |
| **all criteria** | **14/20** | **70%** | **[48.1%, 85.5%]** | 43, 45, 52, 53, 57, 59 |

* The reference seed (42) passes every criterion: Checkout v4.2 supported and the only supported event, Homepage hero refresh
  rejected, mobile x Safari, 2026-09-20 flagged, interval [-0.774, -0.585] pp against the true -0.6618 pp, strength `moderate`.
* A wrong event was supported in 0 of 20 runs.
* Evidence strength: `moderate` in 16 runs and `insufficient` in 4. No run reached `strong`: release attribution is ambiguous on this
  data (Hero at lag 1 shares Checkout's onset), which caps strength at `moderate` by design.
* Containment among the 16 runs that produced an interval: 14 of them contain the true effect (88%, Wilson [64.0%, 96.5%]). This is
  a count over selected runs, not a measurement of the interval's 95% coverage. The 16 are the runs in which the investigation
  supported a candidate, a selection made by the investigation itself; the four that supported none have no interval and are
  excluded. The 16 intervals are also not all for the same population: seed 59's is for a broader focal group (`browser=safari`) than
  the planted segment. How often the method's intervals cover their target is a property tested on null data in
  `docs/candidate-testing.md`, not by this evaluation.

### The seeds that did not pass

* **43, 45, 52, 53** (four runs): the change was real and localized, but no candidate was supported, so there is no estimate. All four
  have strength `insufficient`; the investigation then recommends a randomized experiment rather than naming a cause. Seeds 43 and 52
  also did not reject the decoy (it stays `inconclusive`). This is the tests being conservative, not a wrong attribution, and it matches
  the 83% recovery rate measured over 150 seeds in `docs/candidate-testing.md`.
* **57**: Checkout supported, but the interval [-0.862, -0.689] pp misses the true -0.6560 pp by 0.033 pp.
* **59**: the localization named `browser=safari`, a broader set than mobile x Safari, so `localizes_segment` failed. The interval
  for that wider focal group, [-0.571, -0.412] pp, misses the true -0.6566 pp, which is the effect in the narrower segment. Both
  misses are consistent with the focal group being diluted by unaffected traffic; this was not investigated further.

## What this does and does not show

* It is one simulated scenario with the effect planted in one place. It measures whether the pipeline recovers that answer, not how it
  behaves on real data or other kinds of change. `docs/candidate-testing.md` reports the other scenarios (delayed effects,
  indistinguishable events, weak effects, no change).
* 20 seeds give wide intervals (the all-criteria rate is between about 48% and 86%). The seeds are consecutive, 42 to 61, and were not
  chosen after seeing results.
* The grader compares the investigation's own interval with the simulator's true segment effect. The estimand is the differential effect
  against all other traffic, which the planted effect (a mobile Safari reduction with unaffected others) makes close to the segment
  effect, so a small gap between them is expected.
* The default reasoner writes the report text; the criteria read the structured result only, so a different reasoner cannot change a
  grade except through the tools' own numbers.
* Nothing was tuned to improve these rates. No analytics code changed in this task.

## Code

`causelens/eval/`: `stats.py` (Wilson interval), `grading.py` (`extract` reads the result, `grade` applies the five criteria),
`runner.py` (per-seed run, key handling, aggregation into `EvalReport`), `report.py` (plain text), `__main__.py` (the command).
Tests are in `tests/test_eval.py`: each criterion fails on its own, the key checks, a real seed-42 run graded against the key file, the
aggregation, the command and its exit statuses, and property tests of the interval and of the coverage criterion. The 20-seed run itself
is not repeated inside the test suite.
