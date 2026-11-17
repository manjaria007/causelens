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
