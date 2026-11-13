"""Property tests for ``check_data_quality`` (design.md, "Correctness properties" #7).

Two kinds of test, kept separate because they make different promises:

1. *Guaranteed* properties use **bounded** fixtures: every value is drawn from a known
   interval, so the detector's decision follows from arithmetic, not from luck. The bounds
   and the argument are written next to the fixture. Hypothesis can therefore search the
   whole parameter space without producing a legitimate counterexample.
2. *Statistical* tests use **binomial** fixtures and assert an explicitly stated error rate
   over a fixed list of seeds. Individual unlucky draws are expected there (for example, with
   a 50% order loss on a noisy 8,000-session series the robust z-score is only about 5), so no
   per-draw guarantee is claimed.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from hypothesis import given, settings
from hypothesis import strategies as st

from causelens.analytics import check_data_quality

DEVICES = ("desktop", "mobile", "tablet")
N_DAYS = 45
HALF = 7  # default window_days // 2
# Injected days keep a full 7-day baseline on both sides.
FIRST_INJECTABLE, LAST_INJECTABLE = HALF, N_DAYS - HALF - 1

WEEKDAY_TRAFFIC = np.array([1.00, 0.98, 0.97, 0.99, 1.05, 1.15, 1.10])
WEEKDAY_CONV = np.array([1.02, 1.01, 1.00, 1.00, 0.98, 0.97, 0.99])
SESSION_NOISE = 0.05  # sessions multiplied by 1 +/- 5%
RATE_NOISE = 0.01  # conversion multiplied by 1 +/- 1%


def _bounded_frame(seed: int, base_rate: float, base_sessions: int) -> pd.DataFrame:
    """Daily data where every value lies in a known interval (no sampling noise).

    ``rate = base_rate * device_mult * weekday_conv * (1 +/- 1%)`` and
    ``sessions = base_sessions * device_mult * weekday_traffic * (1 +/- 5%)``;
    ``orders = round(sessions * rate)``.

    Guarantees (device multipliers only rescale a series, so they cancel in ratios):

    * Clean series: the rate varies within [0.97*0.99, 1.02*1.01] = [0.9603, 1.0302] of its
      base, a relative spread of 7.3% (+0.4% rounding) < ``min_relative_deviation`` (10%),
      so no clean day can be flagged. Sessions vary within [0.97*0.95, 1.15*1.05] -> 31% < 40%
      (``sessions_min_relative_deviation``), so no clean day is a volume anomaly.
    * Injected loss >= 50% on one day: the day's rate is at most 0.5 * 1.0302 / 0.9603 = 53.6%
      of the baseline median, a drop of at least 46.4% of the median. The MAD of values in an
      interval is at most half its width, so 1.4826*MAD <= 5.4% of the median; the binomial floor
      sqrt((1-p)/(p*s)) is at most 6.8% of the median (p >= 0.0288, s >= 7,372). The scale is
      therefore <= 6.8% and k*scale = 5*6.8% = 34% < 46.4%: the day is always flagged.
    """
    rng = np.random.default_rng(seed)
    rows = []
    for i, day in enumerate(pd.date_range("2026-08-01", periods=N_DAYS)):
        wd = day.weekday()
        for k, device in enumerate(DEVICES):
            sess_noise = rng.uniform(-SESSION_NOISE, SESSION_NOISE)
            rate_noise = rng.uniform(-RATE_NOISE, RATE_NOISE)
            sessions = round(base_sessions * (1 + 0.1 * k) * WEEKDAY_TRAFFIC[wd] * (1 + sess_noise))
            rate = base_rate * (1 + 0.2 * k) * WEEKDAY_CONV[wd] * (1 + rate_noise)
            rows.append(
                {
                    "date": day.strftime("%Y-%m-%d"),
                    "device": device,
                    "browser": "chrome",
                    "channel": "organic",
                    "customer_type": "new",
                    "sessions": sessions,
                    "orders": round(sessions * rate),
                }
            )
    return pd.DataFrame(rows)


def _binomial_frame(seed: int, base_rate: float, base_sessions: int) -> pd.DataFrame:
    """Simulator-like data with Poisson sessions and binomial orders (statistical fixture)."""
    rng = np.random.default_rng(seed)
    rows = []
    for day in pd.date_range("2026-08-01", periods=N_DAYS):
        day_noise = rng.normal(1.0, 0.03)
        for k, device in enumerate(DEVICES):
            lam = base_sessions * (1.0 + 0.1 * k) * WEEKDAY_TRAFFIC[day.weekday()] * day_noise
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


def _inject_loss(df: pd.DataFrame, device: str, day_index: int, loss: float) -> pd.Timestamp:
    day = pd.Timestamp("2026-08-01") + pd.Timedelta(days=day_index)
    mask = (df["date"] == day.strftime("%Y-%m-%d")) & (df["device"] == device)
    df.loc[mask, "orders"] = np.floor(df.loc[mask, "orders"] * (1 - loss)).astype(int)
    return day


# ------------------------------------------------------------------ guaranteed properties


@settings(max_examples=100, deadline=None, derandomize=True)
@given(
    seed=st.integers(0, 2**31 - 1),
    device=st.sampled_from(DEVICES),
    day_index=st.integers(FIRST_INJECTABLE, LAST_INJECTABLE),
    loss=st.floats(0.5, 1.0),
    base_rate=st.floats(0.03, 0.06),
    base_sessions=st.integers(8_000, 20_000),
)
def test_injected_order_loss_on_one_segment_day_is_flagged(
    seed, device, day_index, loss, base_rate, base_sessions
):
    df = _bounded_frame(seed, base_rate, base_sessions)
    day = _inject_loss(df, device, day_index, loss)

    report = check_data_quality(df)

    flags = report.flags_for(day.date(), f"device={device}")
    assert flags, f"loss={loss:.2f} on {device} {day.date()} was not flagged"
    assert flags[0].kind == "possible_tracking_issue"  # sessions were left untouched
    assert flags[0].relative_deviation < 0
    assert not flags[0].baseline_truncated  # full baseline on both sides by construction
    assert day.date() in report.excluded_dates
    # Only the injected day (and the dimension values containing the injected device) flag.
    assert {f.date for f in report.flags} == {day.date()}


@settings(max_examples=100, deadline=None, derandomize=True)
@given(
    seed=st.integers(0, 2**31 - 1),
    base_rate=st.floats(0.03, 0.06),
    base_sessions=st.integers(8_000, 20_000),
)
def test_bounded_clean_data_is_never_flagged(seed, base_rate, base_sessions):
    assert check_data_quality(_bounded_frame(seed, base_rate, base_sessions)).flags == []


@settings(max_examples=100, deadline=None, derandomize=True)
@given(
    seed=st.integers(0, 2**31 - 1),
    device=st.sampled_from(DEVICES),
    change_day=st.integers(3, N_DAYS - 3),
    relative_change=st.floats(-0.5, 0.5).filter(lambda x: abs(x) >= 0.1),
)
def test_persistent_step_only_flags_days_with_truncated_baselines(
    seed, device, change_day, relative_change
):
    """A step that persists cannot flag a day whose window has 7 usable days on each side.

    For such a day the window holds <= 7 values from the other side of the step, so the
    median stays on the day's own side (or sits mid-gap when exactly 7/7 and the MAD equals
    that half gap, giving |z| < 1). Flags are only possible near the ends of the series, and
    those are marked ``baseline_truncated``.
    """
    df = _bounded_frame(seed, 0.04, 12_000)
    after = (df["device"] == device) & (df["date"] >= (pd.Timestamp("2026-08-01") + pd.Timedelta(days=change_day)).strftime("%Y-%m-%d"))
    df.loc[after, "orders"] = np.round(df.loc[after, "orders"] * (1 + relative_change)).astype(int)

    report = check_data_quality(df)

    assert all(f.baseline_truncated for f in report.flags), [
        (f.date, f.segment, f.baseline_days_before, f.baseline_days_after) for f in report.flags
        if not f.baseline_truncated
    ]


# ------------------------------------------------------------------ statistical tests
# Fixed seed lists make these deterministic. Thresholds were set with margin from measured
# rates over seeds 0-299 (see docs/data-quality.md): 0/300 false-alarm series, 299/300
# detections at 50% loss, 300/300 at 70%.

SEEDS = range(150)


def test_statistical_false_alarm_rate_on_clean_binomial_data():
    """At most 2% of clean 45-day series (3 devices x 4 dimension values) contain any flag."""
    alarms = sum(bool(check_data_quality(_binomial_frame(s, 0.03, 12_000)).flags) for s in SEEDS)
    assert alarms / len(SEEDS) <= 0.02, f"{alarms}/{len(SEEDS)} clean series produced a flag"


def test_statistical_detection_rate_for_injected_loss_on_binomial_data():
    """At 50% loss on the noisiest setting (3% rate, 8,000 sessions) detection is >= 97%;
    at 70% loss it is >= 99%. Single draws can miss at 50%: that is why this is not a
    per-draw property."""
    for loss, minimum in [(0.5, 0.97), (0.7, 0.99)]:
        hits = 0
        for s in SEEDS:
            rng = np.random.default_rng(10_000 + s)
            day_index = int(rng.integers(FIRST_INJECTABLE, LAST_INJECTABLE + 1))
            device = DEVICES[int(rng.integers(len(DEVICES)))]
            df = _binomial_frame(s, 0.03, 8_000)
            day = _inject_loss(df, device, day_index, loss)
            hits += bool(check_data_quality(df).flags_for(day.date(), f"device={device}"))
        assert hits / len(SEEDS) >= minimum, f"loss={loss}: detected {hits}/{len(SEEDS)}"
