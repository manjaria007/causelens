"""Property (design.md, "Correctness properties" #7): zeroing a share of one segment's
orders on one day gets that day flagged."""
from __future__ import annotations

import numpy as np
import pandas as pd
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from causelens.analytics import check_data_quality

DEVICES = ("desktop", "mobile", "tablet")
N_DAYS = 45  # 10+ clean days on each side of any injected day (window needs >= 7 each side)
FIRST_INJECTABLE, LAST_INJECTABLE = 10, N_DAYS - 11


def _clean_frame(seed: int, base_rate: float, base_sessions: int) -> pd.DataFrame:
    """Plausible daily data: weekday effects, day-level noise, binomial order counts."""
    rng = np.random.default_rng(seed)
    weekday = np.array([1.00, 0.98, 0.97, 0.99, 1.05, 1.15, 1.10])
    rows = []
    for i, day in enumerate(pd.date_range("2026-08-01", periods=N_DAYS)):
        day_noise = rng.normal(1.0, 0.03)
        for k, device in enumerate(DEVICES):
            lam = base_sessions * (1.0 + 0.1 * k) * weekday[day.weekday()] * day_noise
            sessions = int(rng.poisson(lam))
            p = base_rate * (1.0 + 0.2 * k) * rng.normal(1.0, 0.015)
            rows.append(
                {
                    "date": day.strftime("%Y-%m-%d"),
                    "device": device,
                    "browser": "chrome",
                    "channel": "organic",
                    "customer_type": "new",
                    "sessions": sessions,
                    "orders": int(rng.binomial(sessions, p)),
                }
            )
    return pd.DataFrame(rows)


@settings(
    max_examples=60,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
    derandomize=True,
)
@given(
    seed=st.integers(0, 2**31 - 1),
    device=st.sampled_from(DEVICES),
    day_index=st.integers(FIRST_INJECTABLE, LAST_INJECTABLE),
    loss=st.floats(0.5, 0.9),  # share of the day's orders that go unrecorded: clearly detectable
    base_rate=st.floats(0.03, 0.06),
    base_sessions=st.integers(8_000, 20_000),
)
def test_injected_order_loss_on_one_segment_day_is_flagged(
    seed, device, day_index, loss, base_rate, base_sessions
):
    df = _clean_frame(seed, base_rate, base_sessions)
    target_day = pd.Timestamp("2026-08-01") + pd.Timedelta(days=day_index)
    mask = (df["date"] == target_day.strftime("%Y-%m-%d")) & (df["device"] == device)
    df.loc[mask, "orders"] = np.floor(df.loc[mask, "orders"] * (1 - loss)).astype(int)

    report = check_data_quality(df)

    flags = report.flags_for(target_day.date(), f"device={device}")
    assert flags, f"loss={loss:.2f} on {device} {target_day.date()} was not flagged"
    assert flags[0].kind == "possible_tracking_issue"  # sessions were left untouched
    assert flags[0].relative_deviation < 0
    assert target_day.date() in report.excluded_dates


@settings(max_examples=30, deadline=None, derandomize=True)
@given(seed=st.integers(0, 2**31 - 1), base_rate=st.floats(0.03, 0.06))
def test_clean_data_without_injection_is_not_flagged(seed, base_rate):
    report = check_data_quality(_clean_frame(seed, base_rate, 12_000))
    assert report.flags == []
