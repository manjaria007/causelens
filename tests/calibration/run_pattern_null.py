"""Null calibration of the ``uniform`` pattern test with sparse segments.

Truth is uniform (no segment's rate changes). Each dataset has two dense segments and one sparse segment seen on
``k`` days in each window. Reports the false-contradiction rate with Wilson 95% intervals, per ``k``.

    python tests/calibration/run_pattern_null.py --n 400
"""
from __future__ import annotations

import argparse
import sys
import warnings
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
warnings.filterwarnings("ignore")

from run_attribution import wilson

from causelens.analytics import validate_metrics
from causelens.analytics.patterns import uniform_test

PRE_W, POST_W = (date(2026, 8, 1), date(2026, 8, 20)), (date(2026, 8, 21), date(2026, 8, 30))


def dataset(seed: int, k: int, rare_sessions: int = 4000) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    rows = []
    pre_days = set(rng.choice(20, size=k, replace=False)) if k else set()
    post_days = set(20 + rng.choice(10, size=k, replace=False)) if k else set()
    for i in range(30):
        day = (pd.Timestamp("2026-08-01") + pd.Timedelta(days=i)).strftime("%Y-%m-%d")
        common = 1 + rng.normal(0, 0.1)
        for dev in ("a", "b"):
            s = int(rng.poisson(4000))
            rows.append({"date": day, "device": dev, "browser": "chrome", "channel": "organic", "customer_type": "new",
                         "sessions": s, "orders": int(rng.binomial(s, 0.04 * common))})
        if i in pre_days | post_days:
            rows.append({"date": day, "device": "rare", "browser": "chrome", "channel": "organic", "customer_type": "new",
                         "sessions": rare_sessions, "orders": int(rng.binomial(rare_sessions, 0.04 * common))})
    return validate_metrics(pd.DataFrame(rows))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=400)
    args = ap.parse_args()
    print("| active days of the sparse segment per window | datasets | falsely contradicted | rate (95% Wilson) | sparse segment excluded |")
    print("|---|---|---|---|---|")
    for k in (0, 1, 2, 3, 5, 10):
        bad = excluded = 0
        for seed in range(args.n):
            df = dataset(seed, k)
            days = pd.DatetimeIndex(sorted(df["date"].unique()))
            r = uniform_test(df, days, PRE_W, POST_W)
            bad += r.status == "contradicted"
            part = next((p for p in r.partitions if p.dimension == "device"), None)
            excluded += bool(part and "rare" in part.excluded)
        lo, hi = wilson(bad, args.n)
        print(f"| {k} | {args.n} | {bad} | {100 * bad / args.n:.1f}% ({100 * lo:.1f}-{100 * hi:.1f}%) | {excluded}/{args.n} |")


if __name__ == "__main__":
    main()
