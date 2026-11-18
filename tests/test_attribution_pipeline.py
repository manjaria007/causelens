"""Small-N scenario checks of the full selection pipeline, with margins taken from the measured rates.

Authoritative rates (N = 150 per scenario) come from ``tests/calibration/run_attribution.py`` and are recorded in
docs/candidate-testing.md. The truth labels here are used only to grade.
"""
from __future__ import annotations

import warnings
from collections import Counter

import pytest
from scenarios import CHECKOUT, HERO, SCENARIOS, run_pipeline, scenario_data

N = 8
warnings.filterwarnings("ignore")


def outcomes(name: str) -> Counter:
    out: Counter = Counter()
    for seed in range(1, N + 1):
        _, res = run_pipeline(*scenario_data(seed, name))
        if res is None:
            out["stopped"] += 1
            continue
        sup = [c for c in res.candidates if c.verdict == "supported"]
        out["supported:" + (",".join(sorted(c.event for c in sup)) or "none")] += 1
        out["ambiguous_supported"] += sum(c.attribution.ambiguous for c in sup)
        out["rejected:" + ",".join(sorted(c.event for c in res.candidates if c.verdict == "rejected"))] += 1
    return out


@pytest.fixture(scope="module")
def results():
    return {name: outcomes(name) for name in ("planted", "hero_immediate", "same_day", "none", "neither")}


def test_truth_labels_exist():
    assert SCENARIOS["planted"].cause == CHECKOUT and SCENARIOS["hero_immediate"].cause == HERO


def test_planted_cause_is_usually_recovered_and_hero_is_never_supported(results):
    r = results["planted"]
    assert r[f"supported:{CHECKOUT}"] >= 0.5 * N
    assert not any(k.startswith("supported:") and HERO in k for k in r)


def test_swapped_true_cause_is_recovered_not_checkout(results):
    r = results["hero_immediate"]
    assert r[f"supported:{HERO}"] >= 0.6 * N
    assert not any(k.startswith("supported:") and CHECKOUT in k for k in r)


@pytest.mark.parametrize("name", ["same_day", "none", "neither"])
def test_indistinguishable_or_absent_causes_are_not_attributed(results, name):
    r = results[name]
    wrong = sum(v for k, v in r.items() if k.startswith("supported:") and k != "supported:none")
    assert wrong <= 1
