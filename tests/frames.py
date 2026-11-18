"""Small synthetic frames for Task 3 tests (single segment, one row per day)."""
from __future__ import annotations

import numpy as np
import pandas as pd

WEEKDAY_TRAFFIC = np.array([1.00, 0.98, 0.97, 0.99, 1.05, 1.15, 1.10])
WEEKDAY_CONV = np.array([1.02, 1.01, 1.00, 1.00, 0.98, 0.97, 0.99])
START = "2026-08-01"  # a Saturday


def to_frame(sessions: np.ndarray, orders: np.ndarray, start: str = START) -> pd.DataFrame:
    days = pd.date_range(start, periods=len(sessions)).strftime("%Y-%m-%d")
    return pd.DataFrame(
        {
            "date": days,
            "device": "desktop",
            "browser": "chrome",
            "channel": "organic",
            "customer_type": "new",
            "sessions": np.asarray(sessions, dtype=int),
            "orders": np.asarray(orders, dtype=int),
        }
    )


def bounded_series(
    seed: int,
    n_days: int = 80,
    base_rate: float = 0.04,
    base_sessions: int = 12_000,
    step_day: int | None = None,
    step: float = 0.0,
    weekday: bool = True,
    rate_noise: float = 0.01,
    session_noise: float = 0.05,
) -> pd.DataFrame:
    """Daily data with bounded noise: conversion = base * weekday * (1 +/- rate_noise) * (1+step after
    ``step_day``), sessions bounded likewise. No binomial sampling noise, so outcomes can be argued."""
    rng = np.random.default_rng(seed)
    days = pd.date_range(START, periods=n_days)
    wd = days.day_of_week.to_numpy()
    sess = base_sessions * (WEEKDAY_TRAFFIC[wd] if weekday else 1.0) * (1 + rng.uniform(-session_noise, session_noise, n_days))
    sess = np.round(sess)
    rate = base_rate * (WEEKDAY_CONV[wd] if weekday else 1.0) * (1 + rng.uniform(-rate_noise, rate_noise, n_days))
    if step_day is not None:
        rate = np.where(np.arange(n_days) >= step_day, rate * (1 + step), rate)
    return to_frame(sess, np.round(sess * rate))


def binomial_series(
    seed: int,
    n_days: int = 80,
    base_rate: float = 0.04,
    base_sessions: int = 12_000,
    step_day: int | None = None,
    step: float = 0.0,
) -> pd.DataFrame:
    """Poisson sessions, binomial orders, weekday effects, day-level noise: a realistic noisy series."""
    rng = np.random.default_rng(seed)
    days = pd.date_range(START, periods=n_days)
    wd = days.day_of_week.to_numpy()
    sess = rng.poisson(base_sessions * WEEKDAY_TRAFFIC[wd] * rng.normal(1.0, 0.03, n_days))
    p = base_rate * WEEKDAY_CONV[wd] * rng.normal(1.0, 0.015, n_days)
    if step_day is not None:
        p = np.where(np.arange(n_days) >= step_day, p * (1 + step), p)
    return to_frame(sess, rng.binomial(sess, p))


def day(i: int, start: str = START):
    return (pd.Timestamp(start) + pd.Timedelta(days=i)).date()


DEFAULT_SEGMENT = {"device": "desktop", "browser": "chrome", "channel": "organic", "customer_type": "new"}


def segmented(
    segments: list[dict],
    n_days: int = 28,
    split: int = 14,
    start: str = START,
    noise_seed: int | None = None,
) -> pd.DataFrame:
    """Rows for several segments with hand-computable numbers.

    Each spec gives any of the four dimensions (others default to ``DEFAULT_SEGMENT``), plus
    ``sessions=(per_day_pre, per_day_post)`` and ``rate=(pre, post)``. Days before ``split`` use the
    pre values. A zero session count drops the segment from that window. Orders are
    ``round(sessions * rate)`` per day, or binomial when ``noise_seed`` is given.
    """
    rng = np.random.default_rng(noise_seed) if noise_seed is not None else None
    days = pd.date_range(start, periods=n_days).strftime("%Y-%m-%d")
    rows = []
    for i, d in enumerate(days):
        post = i >= split
        for spec in segments:
            sessions = spec["sessions"][1 if post else 0]
            if sessions <= 0:
                continue
            rate = spec["rate"][1 if post else 0]
            orders = int(rng.binomial(sessions, rate)) if rng is not None else round(sessions * rate)
            rows.append({**DEFAULT_SEGMENT, **{k: spec[k] for k in DEFAULT_SEGMENT if k in spec},
                         "date": d, "sessions": int(sessions), "orders": int(orders)})
    return pd.DataFrame(rows)[["date", *DEFAULT_SEGMENT, "sessions", "orders"]]


def focal_comparison_frame(
    seed: int,
    n_days: int = 70,
    steps: dict[int, float] | None = None,
    base_rate: float = 0.04,
    sessions_focal: int = 6000,
    sessions_comparison: int = 14000,
    trend_per_day: float = 0.0,
    start: str = START,
) -> pd.DataFrame:
    """Two segments (focal = mobile/safari, comparison = desktop/chrome) with binomial orders.

    ``steps`` maps a day index to a *relative* change of the focal conversion that starts that day and persists
    (changes accumulate: ``{30: -0.1, 31: -0.1}`` is a staged drop). ``trend_per_day`` adds a linear relative
    drift to the focal conversion. Sessions are Poisson with a weekday pattern.
    """
    rng = np.random.default_rng(seed)
    days = pd.date_range(start, periods=n_days)
    wd = days.day_of_week.to_numpy()
    level = np.ones(n_days)
    for k, rel in (steps or {}).items():
        level[k:] *= 1 + rel
    level *= 1 + trend_per_day * np.arange(n_days)
    rows = []
    for name, dev, brw, base_s, mult in (("focal", "mobile", "safari", sessions_focal, level), ("comp", "desktop", "chrome", sessions_comparison, np.ones(n_days))):
        sess = rng.poisson(base_s * WEEKDAY_TRAFFIC[wd] * rng.normal(1.0, 0.03, n_days))
        p = np.clip(base_rate * WEEKDAY_CONV[wd] * rng.normal(1.0, 0.012, n_days) * mult, 0, 0.9)
        orders = rng.binomial(sess, p)
        for i, d in enumerate(days):
            rows.append({"date": d.strftime("%Y-%m-%d"), "device": dev, "browser": brw, "channel": "organic",
                         "customer_type": "new", "sessions": int(sess[i]), "orders": int(orders[i])})
    return pd.DataFrame(rows)
