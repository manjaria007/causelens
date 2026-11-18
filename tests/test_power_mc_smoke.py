"""A seconds-long smoke test of the Monte Carlo helper in tests/calibration/run_power_mc.py.

The real check (hundreds of thousands of replications per configuration) is run by the script and recorded in
docs/experiment-design.md; it is deliberately not part of the fast suite.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def load():
    path = Path(__file__).parent / "calibration" / "run_power_mc.py"
    spec = importlib.util.spec_from_file_location("run_power_mc", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["run_power_mc"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_the_planned_test_rejects_near_alpha_under_the_null_and_near_target_under_the_alternative():
    mc = load()
    r = mc.simulate(mc.Config("smoke", 0.05, 1.0), reps=6000, seed=7)
    assert 0.02 < r["null"]["per_comparison"] / 6000 < 0.08  # nominal 0.05, SE about 0.003
    assert 0.74 < r["alt"]["per_comparison"] / 6000 < 0.86  # target 0.80, SE about 0.005


def test_the_helper_is_seeded_and_reports_the_family_wise_rate():
    mc = load()
    cfg = mc.Config("two arms", 0.04, -0.6, k=2)
    a, b = mc.simulate(cfg, reps=3000, seed=3), mc.simulate(cfg, reps=3000, seed=3)
    assert a["null"] == b["null"] and a["alt"] == b["alt"]
    assert a["null"]["any"] >= a["null"]["per_comparison"]
