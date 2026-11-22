"""Task 9: the ground-truth evaluation (Requirements 10.1 to 10.3).

Grading is tested on hand-built findings against the real answer key, so each criterion can be failed on its own. The runner and the
command line are tested with real investigations on a few seeds. The 20-seed run itself is a command (``python -m causelens.eval``),
recorded in ``docs/evaluation.md``; it is not repeated inside the suite.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

import causelens.eval.__main__ as cli
from causelens.eval import (
    CRITERIA,
    EvalError,
    EvalReport,
    Findings,
    SeedOutcome,
    grade,
    run_seed,
    summarize,
    wilson_interval,
)
from causelens.eval import runner as eval_runner
from causelens.eval.runner import MIN_SEEDS, REFERENCE_SEED, load_key
from causelens.sim.generate import generate

ROOT = Path(__file__).resolve().parents[1]
CHECKOUT, HERO = "Checkout v4.2", "Homepage hero refresh"
SEGMENT = "device=mobile, browser=safari"


# --------------------------------------------------------------------------- the interval

@given(st.integers(1, 400).flatmap(lambda n: st.tuples(st.integers(0, n), st.just(n))))
def test_wilson_interval_contains_the_rate_and_stays_in_zero_one(pair):
    k, n = pair
    lo, hi = wilson_interval(k, n)
    assert 0.0 <= lo <= k / n <= hi <= 1.0


@given(st.integers(1, 100), st.integers(0, 99))
def test_wilson_interval_is_monotone_in_the_number_of_passes(n, k):
    k = min(k, n - 1)
    lo_a, hi_a = wilson_interval(k, n)
    lo_b, hi_b = wilson_interval(k + 1, n)
    assert lo_a <= lo_b and hi_a <= hi_b


def test_wilson_interval_known_values_and_errors():
    assert wilson_interval(14, 20) == pytest.approx((0.4812, 0.8545), abs=5e-4)
    assert wilson_interval(0, 20)[0] == 0.0 and wilson_interval(20, 20)[1] == 1.0
    for bad in ((0, 0), (-1, 5), (6, 5)):
        with pytest.raises(ValueError):
            wilson_interval(*bad)


# --------------------------------------------------------------------------- grading

def passing(**override) -> Findings:
    base = {
        "status": "completed", "supported": [CHECKOUT], "rejected": [HERO], "localized_segments": [SEGMENT],
        "flagged_dates": ["2026-09-20"], "excluded_dates": ["2026-09-20"], "intervals": {CHECKOUT: (-0.774, -0.585)}, "strength": "moderate",
    }
    return Findings(**{**base, **override})


def failed(grade_) -> set[str]:
    return {c.name for c in grade_.criteria if not c.passed}


def test_a_correct_investigation_passes_every_criterion(ground_truth):
    g = grade(passing(), ground_truth)
    assert g.passed and failed(g) == set() and [c.name for c in g.criteria] == list(CRITERIA)
    assert g.wrong_event_supported == [] and g.has_estimate


@pytest.mark.parametrize("override,expected", [
    ({"supported": []}, {"identifies_cause"}),
    ({"supported": [HERO]}, {"identifies_cause"}),
    ({"supported": [CHECKOUT, HERO]}, {"identifies_cause"}),  # naming the decoy too is not identifying the cause
    ({"localized_segments": None}, {"localizes_segment"}),
    ({"localized_segments": ["browser=safari"]}, {"localizes_segment"}),
    ({"localized_segments": ["device=mobile, browser=chrome"]}, {"localizes_segment"}),
    ({"localized_segments": [SEGMENT, "device=desktop"]}, {"localizes_segment"}),
    ({"rejected": []}, {"rejects_decoy"}),
    ({"flagged_dates": []}, {"flags_data_quality"}),
    ({"excluded_dates": []}, {"flags_data_quality"}),
    ({"flagged_dates": ["2026-09-21"], "excluded_dates": ["2026-09-21"]}, {"flags_data_quality"}),
    ({"intervals": {}}, {"ci_covers_truth"}),
    ({"intervals": {HERO: (-1.0, 0.0)}}, {"ci_covers_truth"}),  # an interval for another event does not count
    ({"intervals": {CHECKOUT: (-0.57, -0.41)}}, {"ci_covers_truth"}),
    ({"intervals": {CHECKOUT: (-0.90, -0.70)}}, {"ci_covers_truth"}),
])
def test_each_criterion_fails_on_its_own(ground_truth, override, expected):
    g = grade(passing(**override), ground_truth)
    assert failed(g) == expected and not g.passed


def test_every_decoy_must_be_rejected_not_just_one(ground_truth):
    key = json.loads(json.dumps(ground_truth))
    key["grading"]["must_reject"] = [HERO, "Search ranking tweak v3.9"]
    assert "rejects_decoy" in failed(grade(passing(rejected=[HERO]), key))
    assert "rejects_decoy" in failed(grade(passing(rejected=["Search ranking tweak v3.9"]), key))
    assert "rejects_decoy" not in failed(grade(passing(rejected=[HERO, "Search ranking tweak v3.9"]), key))


def test_segment_order_in_the_label_does_not_matter(ground_truth):
    assert grade(passing(localized_segments=["browser=safari, device=mobile"]), ground_truth).criteria[1].passed


def test_a_stopped_investigation_fails_what_it_did_not_establish(ground_truth):
    g = grade(passing(status="stopped_change_not_confirmed", supported=[], rejected=[], localized_segments=None, intervals={}), ground_truth)
    assert failed(g) == {"identifies_cause", "localizes_segment", "rejects_decoy", "ci_covers_truth"} and not g.has_estimate


def test_a_wrongly_supported_event_is_listed_as_an_incorrect_attribution(ground_truth):
    assert grade(passing(supported=[HERO]), ground_truth).wrong_event_supported == [HERO]
    assert grade(passing(supported=[CHECKOUT, HERO]), ground_truth).wrong_event_supported == [HERO]


@given(st.floats(-3, 3, allow_nan=False), st.floats(0, 3, allow_nan=False))
def test_the_interval_criterion_is_exactly_coverage(ground_truth, lower, width):
    truth = ground_truth["planted_cause"]["true_effect_in_segment_pp"]
    g = grade(passing(intervals={CHECKOUT: (lower, lower + width)}), ground_truth)
    assert g.criteria[4].passed == (lower <= truth <= lower + width)


def test_interval_ends_are_inclusive(ground_truth):
    truth = ground_truth["planted_cause"]["true_effect_in_segment_pp"]
    assert grade(passing(intervals={CHECKOUT: (truth, truth + 1)}), ground_truth).criteria[4].passed
    assert grade(passing(intervals={CHECKOUT: (truth - 1, truth)}), ground_truth).criteria[4].passed


# --------------------------------------------------------------------------- the answer key

def test_the_reference_key_on_disk_is_the_key_the_simulator_makes(ground_truth):
    assert load_key(REFERENCE_SEED, generate(REFERENCE_SEED)[2]) == ground_truth


def test_a_stale_or_missing_reference_key_is_refused(tmp_path):
    generated = generate(REFERENCE_SEED)[2]
    stale = json.loads(json.dumps(generated))
    stale["planted_cause"]["true_effect_in_segment_pp"] = -0.1
    (tmp_path / "stale.json").write_text(json.dumps(stale))
    with pytest.raises(EvalError, match="not the key the simulator produces"):
        load_key(REFERENCE_SEED, generated, tmp_path / "stale.json")
    with pytest.raises(EvalError, match="cannot read"):
        load_key(REFERENCE_SEED, generated, tmp_path / "absent.json")
    (tmp_path / "bad.json").write_text("{not json")
    with pytest.raises(EvalError, match="cannot read"):
        load_key(REFERENCE_SEED, generated, tmp_path / "bad.json")


def test_other_seeds_are_graded_against_their_own_generated_key():
    generated = generate(43)[2]
    assert load_key(43, generated) is generated
    assert generated["planted_cause"]["true_effect_in_segment_pp"] != generate(REFERENCE_SEED)[2]["planted_cause"]["true_effect_in_segment_pp"]


def test_the_investigation_is_served_the_metrics_and_events_and_nothing_else(monkeypatch):
    seen: dict = {}
    real = eval_runner.investigate_files

    async def spy(metrics: Path, events: Path):
        seen["files"] = sorted(p.name for p in metrics.parent.iterdir())
        seen["same_directory"] = metrics.parent == events.parent
        return await real(metrics, events)

    monkeypatch.setattr(eval_runner, "investigate_files", spy)
    run_seed(43)
    assert seen == {"files": ["events.csv", "metrics_daily.csv"], "same_directory": True}


def test_no_investigation_code_reads_the_answer_key():
    for package in ("agents", "analytics"):
        for path in (ROOT / "causelens" / package).glob("*.py"):
            assert "ground_truth" not in path.read_text(), path


# --------------------------------------------------------------------------- real runs

@pytest.fixture(scope="module")
def reference_outcome() -> SeedOutcome:
    return run_seed(REFERENCE_SEED)


def test_the_reference_investigation_passes_every_criterion_against_the_key_file(reference_outcome):
    o = reference_outcome
    assert o.passed and all(c.passed for c in o.criteria) and o.has_estimate and o.wrong_event_supported == []
    f = o.findings
    assert f.supported == [CHECKOUT] and f.rejected == [HERO] and f.localized_segments == [SEGMENT] and f.excluded_dates == ["2026-09-20"]
    assert f.strength == "moderate" and f.status == "completed" and set(f.intervals) == {CHECKOUT}


def test_extract_reads_the_same_findings_from_a_result(reference_outcome):
    again = reference_outcome.findings
    assert Findings.model_validate_json(again.model_dump_json()) == again


def test_a_seed_is_graded_deterministically():
    assert run_seed(43).model_dump_json() == run_seed(43).model_dump_json()


def test_an_investigation_that_does_not_reach_a_conclusion_fails_the_criteria_it_missed():
    o = run_seed(43)  # the change is real here but no candidate is supported
    assert not o.passed and o.findings.supported == [] and not o.has_estimate
    missed = {c.name for c in o.criteria if not c.passed}
    assert {"identifies_cause", "ci_covers_truth"} <= missed and "flags_data_quality" not in missed
    assert o.wrong_event_supported == []


# --------------------------------------------------------------------------- aggregation

def outcome(seed: int, ok: dict[str, bool], *, estimate: bool = True, wrong: list[str] | None = None, status: str = "completed",
            strength: str = "moderate") -> SeedOutcome:
    from causelens.eval.grading import Criterion

    findings = Findings(status=status, supported=[], rejected=[], localized_segments=None, flagged_dates=[], excluded_dates=[],
                        intervals={}, strength=strength)
    return SeedOutcome(
        seed=seed, passed=all(ok.get(c, True) for c in CRITERIA), findings=findings, wrong_event_supported=wrong or [], has_estimate=estimate,
        criteria=[Criterion(name=c, passed=ok.get(c, True), detail="") for c in CRITERIA], n_calls=12,
    )


def test_summary_counts_each_criterion_and_lists_the_failing_seeds():
    outcomes = [outcome(42, {}), outcome(43, {"identifies_cause": False, "ci_covers_truth": False}, estimate=False, strength="insufficient"),
                outcome(44, {"ci_covers_truth": False}), outcome(45, {}, wrong=[HERO])]
    r = summarize(outcomes)
    rates = {x.name: x for x in r.rates}
    assert (rates["identifies_cause"].passes, rates["identifies_cause"].failed_seeds) == (3, [43])
    assert (rates["ci_covers_truth"].passes, rates["ci_covers_truth"].failed_seeds) == (2, [43, 44])
    assert (r.all_criteria.passes, r.all_criteria.n, r.all_criteria.failed_seeds) == (2, 4, [43, 44])
    assert r.coverage_given_estimate.n == 3 and r.coverage_given_estimate.passes == 2  # seed 43 had no interval and is not counted here
    assert r.wrong_event_supported_seeds == [45] and r.strength_counts == {"insufficient": 1, "moderate": 3}
    assert r.reference_seed == 42 and r.reference_passed is True and r.below_minimum
    for x in r.rates:
        assert (x.wilson_lower, x.wilson_upper) == wilson_interval(x.passes, x.n)


def test_summary_without_the_reference_seed_or_any_estimate():
    r = summarize([outcome(50, {"ci_covers_truth": False}, estimate=False)])
    assert r.reference_seed is None and r.reference_passed is None and r.coverage_given_estimate is None


def test_the_minimum_number_of_seeds_is_flagged_not_assumed():
    many = [outcome(42 + i, {}) for i in range(MIN_SEEDS)]
    assert not summarize(many).below_minimum and summarize(many[:-1]).below_minimum


def test_summary_refuses_an_empty_or_repeated_run():
    with pytest.raises(EvalError):
        summarize([])
    with pytest.raises(EvalError, match="distinct"):
        eval_runner.evaluate([42, 42])


# --------------------------------------------------------------------------- the command

def test_the_default_run_is_twenty_consecutive_seeds_from_the_reference():
    args = cli.build_parser().parse_args([])
    assert (args.seeds, args.first_seed, args.allow_fewer_seeds) == (MIN_SEEDS, REFERENCE_SEED, False) and MIN_SEEDS == 20


def test_fewer_than_twenty_seeds_needs_an_explicit_flag(capsys):
    assert cli.main(["--seeds", "5"]) == 2 and "at least 20" in capsys.readouterr().err
    assert cli.main(["--seeds", "0", "--allow-fewer-seeds"]) == 2


def test_a_small_run_prints_the_table_and_writes_json(tmp_path, capsys):
    out = tmp_path / "report.json"
    code = cli.main(["--seeds", "2", "--allow-fewer-seeds", "--json", str(out)])
    text = capsys.readouterr().out
    assert code == 0
    for name in (*CRITERIA, "all_criteria"):
        assert name in text
    assert "Reference seed 42" in text and "PASS" in text and "fewer than 20 seeds" in text and "Wilson" in text
    report = EvalReport.model_validate_json(out.read_text())
    assert report.seeds == [42, 43] and report.reference_passed is True and report.below_minimum
    assert json.loads(out.read_text())["rates"][0]["name"] == "identifies_cause"


def test_a_stale_key_is_an_error_not_a_grade(tmp_path, capsys):
    stale = json.loads((ROOT / "data" / "ground_truth.json").read_text())
    stale["grading"]["must_identify_event"] = HERO
    key = tmp_path / "ground_truth.json"
    key.write_text(json.dumps(stale))
    assert cli.main(["--seeds", "1", "--allow-fewer-seeds", "--truth", str(key)]) == 2
    assert "not the key the simulator produces" in capsys.readouterr().err


def test_the_exit_status_follows_the_reference_seed_only(monkeypatch, capsys):
    base = summarize([outcome(42, {}), outcome(43, {"identifies_cause": False})])
    failing = summarize([outcome(42, {"rejects_decoy": False}), outcome(43, {})])
    for report, expected in ((base, 0), (failing, 1), (summarize([outcome(50, {"identifies_cause": False})]), 0)):
        monkeypatch.setattr(cli, "evaluate", lambda seeds, truth, report=report: report)
        assert cli.main(["--seeds", "1", "--allow-fewer-seeds"]) == expected
    text = capsys.readouterr().out
    assert "FAIL" in text and "rejects_decoy" in text and "no pass/fail verdict" in text


def test_the_report_text_is_free_of_cause_claims_and_unlabelled_numbers():
    text = cli.render(summarize([outcome(42, {}), outcome(43, {"ci_covers_truth": False})]))
    assert not re.search(r"\b(caused|because of|due to)\b", text)
