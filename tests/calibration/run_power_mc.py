"""Seeded Monte Carlo check of ``power_analysis``: does the planned test behave as the plan says?

For each configuration the script takes the planned integer sizes from ``power_analysis`` and simulates the
planned test: a two-sided pooled two-proportion z-test of each treatment arm against one shared control, at
``alpha / n_comparisons`` per comparison.

* **Null** (every arm has the baseline rate): the per-comparison rejection rate should be near ``alpha / k`` and the
  family-wise rate (any comparison rejects) should not exceed ``alpha``.
* **Alternative** (every treatment arm has ``baseline + mde_pp / 100``): the per-comparison rejection rate should be
  near the target power. The plan's number is a normal approximation, so a small gap is expected, and a larger
  one is expected where the plan flags ``approximation_reliable = False``.

    python tests/calibration/run_power_mc.py --reps 400000 --seed 20261113

Uncertainty is a 95% Wilson interval on each simulated rate. Everything is in memory; no data file is read.
"""
from __future__ import annotations

import argparse
import math
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from scipy import stats

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from run_attribution import wilson

from causelens.analytics import power_analysis


@dataclass(frozen=True)
class Config:
    name: str
    baseline: float
    mde_pp: float
    alpha: float = 0.05
    power: float = 0.8
    ratio: float = 1.0
    k: int = 1


CONFIGS = [
    Config("5% baseline, +1.0 pp", 0.05, 1.0),
    Config("reference-like: 1.99%, +0.585 pp", 0.0199, 0.585),
    Config("4%, -0.6 pp, treatment 2x control", 0.04, -0.6, ratio=2.0),
    Config("4%, -0.6 pp, treatment 0.5x control", 0.04, -0.6, ratio=0.5),
    Config("4%, -0.6 pp, 2 comparisons", 0.04, -0.6, k=2),
    Config("30%, +3 pp, 3 comparisons, power 0.9", 0.30, 3.0, power=0.9, k=3),
    Config("1%, +0.2 pp, alpha 0.01", 0.01, 0.2, alpha=0.01),
    Config("sparse: 0.1%, -0.09 pp", 0.001, -0.09),
]


def pooled_z_rejects(x_c: np.ndarray, n_c: int, x_t: np.ndarray, n_t: int, alpha: float) -> np.ndarray:
    """Two-sided pooled two-proportion z-test; a degenerate pooled variance never rejects."""
    pc, pt = x_c / n_c, x_t / n_t
    pbar = (x_c + x_t) / (n_c + n_t)
    se = np.sqrt(pbar * (1.0 - pbar) * (1.0 / n_c + 1.0 / n_t))
    with np.errstate(divide="ignore", invalid="ignore"):
        z = np.where(se > 0, (pt - pc) / se, 0.0)
    return np.abs(z) > stats.norm.isf(alpha / 2.0)


def simulate(cfg: Config, reps: int, seed: int) -> dict:
    plan = power_analysis(cfg.baseline, cfg.mde_pp, alpha=cfg.alpha, power=cfg.power, allocation_ratio=cfg.ratio, n_comparisons=cfg.k)
    rng = np.random.default_rng(seed)
    p0, p1, nc, nt, a = plan.baseline, plan.target_rate, plan.n_control, plan.n_treatment, plan.alpha_per_comparison
    out: dict = {"cfg": cfg, "plan": plan}
    for label, p_treat in (("null", p0), ("alt", p1)):
        x_c = rng.binomial(nc, p0, size=reps)
        rej = np.column_stack([pooled_z_rejects(x_c, nc, rng.binomial(nt, p_treat, size=reps), nt, a) for _ in range(cfg.k)])
        out[label] = {"per_comparison": int(rej[:, 0].sum()), "any": int(rej.any(axis=1).sum()), "reps": reps}
    return out


def fmt(k: int, n: int) -> str:
    lo, hi = wilson(k, n)
    return f"{100 * k / n:.2f}% ({100 * lo:.2f}-{100 * hi:.2f}%)"


def report(results: list[dict]) -> str:
    lines = [
        (
            "| configuration | n control / treatment | alpha per comparison | null rejection, per comparison | null rejection, any comparison (alpha) "
            "| normal-approx power | simulated power, per comparison (target) | reliable? |"
        ),
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in results:
        c, p, reps = r["cfg"], r["plan"], r["null"]["reps"]
        reliable = "yes" if p.approximation_reliable else "no: min expected count " + format(p.min_expected_count, ".1f")
        lines.append(
            f"| {c.name} | {p.n_control:,} / {p.n_treatment:,} | {p.alpha_per_comparison:g} | {fmt(r['null']['per_comparison'], reps)} | "
            f"{fmt(r['null']['any'], reps)} (<= {100 * p.alpha:g}%) | {p.achieved_power:.4f} | "
            f"{fmt(r['alt']['per_comparison'], reps)} ({p.target_power:g}) | {reliable} |"
        )
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reps", type=int, default=400_000)
    ap.add_argument("--seed", type=int, default=20261113)
    args = ap.parse_args()
    results = [simulate(c, args.reps, args.seed + i) for i, c in enumerate(CONFIGS)]
    print(report(results))
    print(f"\nreps per configuration: {args.reps:,}; seed {args.seed} (+ configuration index); two-sided pooled z-test.")
    off = [
        r["cfg"].name for r in results
        if r["plan"].approximation_reliable and not math.isclose(r["alt"]["per_comparison"] / args.reps, r["plan"].target_power, abs_tol=0.01)
    ]
    if off:
        print("power differs from the target by more than 1 point where the approximation is flagged reliable:", off)


if __name__ == "__main__":
    main()
