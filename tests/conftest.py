from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from causelens.sim.generate import generate

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="session")
def reference() -> tuple[pd.DataFrame, pd.DataFrame]:
    """Seed-42 reference scenario generated in memory (no files touched)."""
    metrics, events, _ = generate(42)
    return metrics, events


@pytest.fixture(scope="session")
def ground_truth() -> dict:
    """Planted answer. Use only for test/evaluation assertions, never inside analytics."""
    return json.loads((ROOT / "data" / "ground_truth.json").read_text())
