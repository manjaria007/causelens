"""Calibration of candidate testing through the full selection pipeline.

Runs quality -> confirm_change -> decompose_change -> test_candidates on simulated scenarios (see
``scenarios.py``), grades each run against that scenario's explicit truth and reports **incorrect
attribution as well as recovery**.

    python tests/calibration/run_attribution.py --scenarios planted hero_immediate --n 150 --out results.json
    python tests/calibration/run_attribution.py --report results.json

Grades (per run):

* ``correct``                 the truth is identified (or, for no/indistinguishable cause, nothing is supported)
* ``unmet``                   the truth is not identified but nothing wrong is claimed (an inconclusive outcome)
* ``stopped``                 ``confirm_change`` stopped the investigation (no change to explain)
* ``incorrect_flagged``       a wrong event is ``supported`` but release attribution is marked ambiguous
* ``incorrect_unflagged``     a wrong event is ``supported`` or the true event ``rejected``, with no ambiguity flag

Everything is simulated in memory; the simulator and ground truth files are never touched.
"""
from __future__ import annotations

import argparse
import collections
import json
import math
import sys
import warnings
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
warnings.filterwarnings("ignore")

from scenarios import CHECKOUT, HERO, SCENARIOS, run_pipeline, scenario_data


def record(seed: int, scenario: str) -> dict:
    metrics, events = scenario_data(seed, scenario)
    stage, res = run_pipeline(metrics, events)
    rec: dict = {"seed": seed, "stage": stage, "status": None, "verdict": {}, "lag1": {}, "strength": {}, "ambiguous": {}, "step_sig": {}}
    if res is None:
        return rec
    rec["status"] = res.status
    for c in res.candidates:
        rec["verdict"][c.event] = c.verdict
        rec["ambiguous"][c.event] = c.attribution.ambiguous
        rec["strength"][c.event] = c.evidence.strength if c.evidence else None
        by_lag = {o.lag_days: o.verdict for o in c.lag_sensitivity}
        rec["lag1"][c.event] = by_lag.get(1)
    return rec


def grade(scenario: str, rec: dict) -> str:
    if rec["stage"] != "tested":
        return "stopped"
    v = rec["verdict"]
    supported = [e for e, x in v.items() if x == "supported"]
    amb = any(rec["ambiguous"].get(e, False) for e in supported)
    truth = SCENARIOS[scenario]
    wrong_flag = "incorrect_flagged" if amb else "incorrect_unflagged"
    if truth.cause is None:  # nothing (or nothing separable) acted: any supported event is an incorrect attribution
        return wrong_flag if supported else "correct"
    if scenario in ("planted", "weak_12", "weak_6", "hero_immediate"):
        cause = truth.cause
        other = HERO if cause == CHECKOUT else CHECKOUT
        if other in supported or v.get(cause) == "rejected":
            return wrong_flag if amb else "incorrect_unflagged"
        return "correct" if v.get(cause) == "supported" else "unmet"
    if scenario == "hero_delayed":  # Hero acted after a one-day delay; the lag-0 headline can only see Checkout
        if CHECKOUT in supported:
            return wrong_flag
        return "unmet"
    if scenario == "checkout_delayed":  # truth is Checkout at lag 1; nothing may be supported at the declared lag 0
        if supported:
            return wrong_flag
        return "unmet"
    if scenario == "gradual_hero":
        return wrong_flag if CHECKOUT in supported else ("unmet" if HERO not in supported else "correct")
    raise KeyError(scenario)


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (c - h, c + h)


def pct(k: int, n: int) -> str:
    lo, hi = wilson(k, n)
    return f"{k}/{n} ({100 * k / n:.0f}%, {100 * lo:.0f}-{100 * hi:.0f}%)" if n else "-"


def summarize(results: dict[str, list[dict]]) -> str:
    lines = ["| scenario | n | correct | unmet (inconclusive) | stopped | incorrect, flagged ambiguous | incorrect, unflagged |", "|---|---|---|---|---|---|---|"]
    detail = []
    for scenario, recs in results.items():
        n = len(recs)
        g = collections.Counter(grade(scenario, r) for r in recs)
        lines.append(
            f"| {scenario} | {n} | {pct(g['correct'], n)} | {pct(g['unmet'], n)} | {pct(g['stopped'], n)} | "
            f"{pct(g['incorrect_flagged'], n)} | {pct(g['incorrect_unflagged'], n)} |"
        )
        tested = [r for r in recs if r["stage"] == "tested"]
        outcomes = collections.Counter(
            (r["verdict"].get(CHECKOUT, "not a candidate"), r["verdict"].get(HERO, "not a candidate")) for r in tested
        )
        strengths = collections.Counter(s for r in tested for s in r["strength"].values() if s)
        amb = sum(any(r["ambiguous"].values()) for r in tested)
        lag1 = collections.Counter(r["lag1"].get(CHECKOUT) for r in tested if CHECKOUT in r["lag1"])
        detail.append(
            f"- **{scenario}** (n={n}): (Checkout, Hero) verdicts "
            + ", ".join(f"({a}, {b}) x{k}" for (a, b), k in outcomes.most_common())
            + f"; strengths of supported candidates {dict(strengths)}; attribution ambiguous in {pct(amb, len(tested))}"
            + (f"; Checkout verdict at lag 1: {dict(lag1)}" if lag1 else "")
        )
    return "\n".join(lines) + "\n\n" + "\n".join(detail)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenarios", nargs="*", default=list(SCENARIOS))
    ap.add_argument("--n", type=int, default=20)
    ap.add_argument("--start", type=int, default=1)
    ap.add_argument("--out", type=Path)
    ap.add_argument("--report", type=Path)
    args = ap.parse_args()
    if args.report:
        print(summarize(json.loads(args.report.read_text())))
        return
    store: dict[str, list[dict]] = json.loads(args.out.read_text()) if args.out and args.out.exists() else {}
    for scenario in args.scenarios:
        store[scenario] = [record(s, scenario) for s in range(args.start, args.start + args.n)]
        if args.out:
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(json.dumps(store))
    print(summarize({k: v for k, v in store.items() if k in args.scenarios}))


if __name__ == "__main__":
    main()
