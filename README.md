# CauseLens

An AI investigator for business metrics: it works out *why* a KPI moved, refuses to
overclaim, and tells you what to test next.

Built for Kiroween with spec-driven development in Kiro:

- `.kiro/steering/` – product, tech and **scientist rules** every agent follows
- `.kiro/specs/metric-investigation/` – requirements (EARS), design, task plan

## Quick start

```bash
python -m pip install -r requirements.txt
python -m causelens.sim.generate          # writes data/metrics_daily.csv, events.csv, ground_truth.json
```

## The reference case

A synthetic store (Aug–Oct 2026, ~40k sessions/day) with things planted for the
investigation to find:

| Date | Event | Truth |
|---|---|---|
| 2026-10-14 | Checkout v4.2 | **Real cause**: −25% conversion for mobile Safari only |
| 2026-10-13 | Homepage hero refresh | **Decoy**: touches everyone, no effect |
| 2026-09-20 | Analytics tag deploy | **Tracking outage**: 60% of desktop orders not recorded |

`data/ground_truth.json` holds the planted answer so every investigation is graded.
