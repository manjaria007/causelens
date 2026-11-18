"""The reference dataset as files, for the MCP tests.

Uses ``data/metrics_daily.csv`` and ``data/events.csv`` when they exist (the metrics file is generated, not committed).
Otherwise it writes the same seed-42 data to a temporary directory with the simulator's own generator, in memory. The
answer key is never opened by anything here.
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def reference_files(tmp_path_factory) -> tuple[Path, Path]:
    metrics, events = ROOT / "data" / "metrics_daily.csv", ROOT / "data" / "events.csv"
    if metrics.is_file() and events.is_file():
        return metrics, events
    from causelens.sim.generate import generate

    out = tmp_path_factory.mktemp("reference")
    frame_m, frame_e, _ = generate(42)
    frame_m.to_csv(out / "metrics_daily.csv", index=False)
    frame_e.to_csv(out / "events.csv", index=False)
    return out / "metrics_daily.csv", out / "events.csv"
