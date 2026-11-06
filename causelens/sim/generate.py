"""Synthetic e-commerce dataset with a planted cause, a decoy and a tracking outage.

Writes three files to data/:
  metrics_daily.csv   one row per date x segment: sessions, orders
  events.csv          the change log an analyst would see (releases, campaigns, incidents)
  ground_truth.json   what was actually planted, so an investigation can be graded

The story (all dates 2026):
  - 2026-10-14  Checkout v4.2 release. TRUE CAUSE: cuts conversion ~25% (relative)
                for mobile + Safari only (an iOS payment-sheet bug). Nothing else changes.
  - 2026-10-13  Homepage hero refresh. DECOY: hits every visitor, zero effect on conversion.
                One day earlier than the release, so the overall series looks like it
                could be either.
  - 2026-09-20  Tracking outage: 60% of desktop orders not recorded that day. A data-quality
                trap - a real-looking dip with no behavioural cause.
  - Weekly seasonality and mild noise throughout.

Usage:  python -m causelens.sim.generate [--seed 42] [--out data]
"""
from __future__ import annotations

import argparse
import json
from datetime import date, timedelta
from itertools import product
from pathlib import Path

import numpy as np
import pandas as pd

START = date(2026, 8, 1)
END = date(2026, 10, 31)

RELEASE_DATE = date(2026, 10, 14)
DECOY_DATE = date(2026, 10, 13)
OUTAGE_DATE = date(2026, 9, 20)

RELEASE_RELATIVE_EFFECT = -0.25
OUTAGE_ORDER_LOSS = 0.60

TOTAL_DAILY_SESSIONS = 40_000

DEVICES = [("desktop", 0.40, 1.35), ("mobile", 0.55, 0.80), ("tablet", 0.05, 1.00)]
BROWSERS = {
    "desktop": [("chrome", 0.70, 1.00), ("safari", 0.15, 1.02), ("edge", 0.15, 0.95)],
    "mobile":  [("chrome", 0.40, 1.00), ("safari", 0.55, 1.05), ("other", 0.05, 0.85)],
    "tablet":  [("chrome", 0.35, 1.00), ("safari", 0.65, 1.00)],
}
CHANNELS = [("organic", 0.45, 1.00), ("paid", 0.30, 0.85), ("email", 0.10, 1.60),
            ("direct", 0.15, 1.20)]
CUSTOMER = [("new", 0.60, 0.75), ("returning", 0.40, 1.40)]

BASE_CONVERSION = 0.030
WEEKDAY_TRAFFIC = [1.00, 0.98, 0.97, 0.99, 1.05, 1.15, 1.10]
WEEKDAY_CONV = [1.02, 1.01, 1.00, 1.00, 0.98, 0.96, 0.99]


def _segments():
    for (dev, dev_share, dev_m) in DEVICES:
        for (br, br_share, br_m), (ch, ch_share, ch_m), (cu, cu_share, cu_m) in product(
            BROWSERS[dev], CHANNELS, CUSTOMER
        ):
            yield {
                "device": dev,
                "browser": br,
                "channel": ch,
                "customer_type": cu,
                "share": dev_share * br_share * ch_share * cu_share,
                "conv_mult": dev_m * br_m * ch_m * cu_m,
            }


def is_treated(device: str, browser: str) -> bool:
    return device == "mobile" and browser == "safari"


def generate(seed: int = 42) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    rng = np.random.default_rng(seed)
    segs = list(_segments())
    days = [START + timedelta(d) for d in range((END - START).days + 1)]

    rows = []
    post_actual_p_sessions = 0.0
    post_cf_p_sessions = 0.0
    post_sessions_total = 0
    treated_post_actual = 0.0
    treated_post_cf = 0.0
    treated_post_sessions = 0

    for i, d in enumerate(days):
        wd = d.weekday()
        trend = 1.0 + 0.0008 * i
        day_noise = rng.normal(1.0, 0.03)
        conv_day = WEEKDAY_CONV[wd] * rng.normal(1.0, 0.015)

        for s in segs:
            lam = TOTAL_DAILY_SESSIONS * s["share"] * WEEKDAY_TRAFFIC[wd] * trend * day_noise
            sessions = int(rng.poisson(max(lam, 0)))
            p_cf = min(BASE_CONVERSION * s["conv_mult"] * conv_day, 0.95)
            p = p_cf

            treated = is_treated(s["device"], s["browser"])
            if treated and d >= RELEASE_DATE:
                p = p_cf * (1 + RELEASE_RELATIVE_EFFECT)

            orders = int(rng.binomial(sessions, p)) if sessions else 0

            if d == OUTAGE_DATE and s["device"] == "desktop":
                orders = int(rng.binomial(orders, 1 - OUTAGE_ORDER_LOSS))

            if d >= RELEASE_DATE:
                post_actual_p_sessions += p * sessions
                post_cf_p_sessions += p_cf * sessions
                post_sessions_total += sessions
                if treated:
                    treated_post_actual += p * sessions
                    treated_post_cf += p_cf * sessions
                    treated_post_sessions += sessions

            rows.append({
                "date": d.isoformat(),
                "device": s["device"],
                "browser": s["browser"],
                "channel": s["channel"],
                "customer_type": s["customer_type"],
                "sessions": sessions,
                "orders": orders,
            })

    metrics = pd.DataFrame(rows)

    events = pd.DataFrame([
        {
            "date": "2026-08-18",
            "type": "release",
            "name": "Search ranking tweak v3.9",
            "scope": "all",
            "description": "Minor relevance change to on-site search.",
        },
        {
            "date": "2026-09-02",
            "type": "campaign",
            "name": "Labor Day email blast",
            "scope": "channel=email",
            "description": "One-day promotional email.",
        },
        {
            "date": "2026-09-20",
            "type": "incident",
            "name": "Analytics tag deploy",
            "scope": "device=desktop",
            "description": "Tag manager change; rolled back next day.",
        },
        {
            "date": DECOY_DATE.isoformat(),
            "type": "release",
            "name": "Homepage hero refresh",
            "scope": "all",
            "description": "New homepage hero imagery and copy.",
        },
        {
            "date": RELEASE_DATE.isoformat(),
            "type": "release",
            "name": "Checkout v4.2",
            "scope": "all",
            "description": "New payment step with Apple Pay / Google Pay sheet.",
        },
    ])

    overall_effect_pp = 100 * (post_actual_p_sessions - post_cf_p_sessions) / post_sessions_total
    seg_effect_pp = 100 * (treated_post_actual - treated_post_cf) / treated_post_sessions

    truth = {
        "seed": seed,
        "window": {"start": START.isoformat(), "end": END.isoformat()},
        "planted_cause": {
            "event": "Checkout v4.2",
            "date": RELEASE_DATE.isoformat(),
            "affected_segment": {"device": "mobile", "browser": "safari"},
            "relative_effect_in_segment": RELEASE_RELATIVE_EFFECT,
            "true_effect_in_segment_pp": round(seg_effect_pp, 4),
            "true_effect_on_overall_conversion_pp": round(overall_effect_pp, 4),
        },
        "decoys": [
            {
                "event": "Homepage hero refresh",
                "date": DECOY_DATE.isoformat(),
                "true_effect_pp": 0.0,
                "why_tempting": "Lands one day before the drop and touches every visitor.",
            }
        ],
        "data_quality_traps": [
            {
                "date": OUTAGE_DATE.isoformat(),
                "kind": "tracking_outage",
                "scope": {"device": "desktop"},
                "orders_lost_share": OUTAGE_ORDER_LOSS,
                "correct_reading": "Tracking loss, not a behavioural change.",
            }
        ],
        "grading": {
            "must_identify_event": "Checkout v4.2",
            "must_localize_to": {"device": "mobile", "browser": "safari"},
            "must_reject": ["Homepage hero refresh"],
            "ci_must_cover": "true_effect_in_segment_pp",
            "must_flag_data_quality": [OUTAGE_DATE.isoformat()],
        },
    }

    return metrics, events, truth


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--out", default="data")
    args = ap.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    metrics, events, truth = generate(args.seed)
    metrics.to_csv(out / "metrics_daily.csv", index=False)
    events.to_csv(out / "events.csv", index=False)
    (out / "ground_truth.json").write_text(json.dumps(truth, indent=2))

    print(f"wrote {len(metrics):,} rows, {len(events)} events -> {out}/")


if __name__ == "__main__":
    main()
