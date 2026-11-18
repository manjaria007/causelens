"""Simulated scenarios for candidate-testing validation (tests and the calibration runner).

Everything here is in memory. The simulator module is never edited: its constants are overridden for one
call and restored, and no file is written. Truth labels are explicit and live only in this module and in the
tests; analytics code never sees them.

Scenarios (the logged events are always Hero on 2026-10-13 and Checkout on 2026-10-14):

====================  =====================================================================================
``planted``           the reference scenario: mobile Safari -25% from 14 Oct (Checkout, lag 0)
``hero_immediate``    swapped true cause: the same effect starts 13 Oct (Hero, lag 0)
``hero_delayed``      Hero acts with a one-day delay: effect from 14 Oct. Identical data to ``planted``
``checkout_delayed``  Checkout acts with a one-day delay: effect from 15 Oct
``same_day``          Hero is logged on 14 Oct too and the effect starts 14 Oct: indistinguishable events
``weak_12`` / ``weak_6``  Checkout lag 0 with a -12% / -6% relative effect
``neither``           the effect starts 20 Oct, with no logged event
``none``              no effect at all
``staged``            Hero contributes half (-12.5%) on 13 Oct, Checkout the rest (-25% from 14 Oct)
``gradual_hero``      Hero ramps in over 13-15 Oct (-8%, -17%, -25%)
====================  =====================================================================================
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import numpy as np
import pandas as pd

import causelens.sim.generate as G

HERO, CHECKOUT = "Homepage hero refresh", "Checkout v4.2"
HERO_DATE, CHECKOUT_DATE = date(2026, 10, 13), date(2026, 10, 14)
PRE = ("2026-08-01", "2026-10-13")
POST = ("2026-10-14", "2026-10-31")

_ORIG = {"RELEASE_DATE": G.RELEASE_DATE, "RELEASE_RELATIVE_EFFECT": G.RELEASE_RELATIVE_EFFECT, "is_treated": G.is_treated}


@dataclass(frozen=True)
class Truth:
    """What actually happened, for grading only."""

    cause: str | None  # event name that acted, or None
    lag: int | None  # days after the event date at which it acted
    note: str


def simulate(seed: int, *, effect_start: date | None = None, rel: float | None = None, uniform: bool = False) -> tuple[pd.DataFrame, pd.DataFrame]:
    """One simulated dataset with the planted effect's start date / size / reach overridden (in memory only)."""
    G.RELEASE_DATE = effect_start or _ORIG["RELEASE_DATE"]
    G.RELEASE_RELATIVE_EFFECT = _ORIG["RELEASE_RELATIVE_EFFECT"] if rel is None else rel
    G.is_treated = (lambda d, b: True) if uniform else _ORIG["is_treated"]
    try:
        metrics, events, _ = G.generate(seed)
    finally:
        for k, v in _ORIG.items():
            setattr(G, k, v)
    events = events.copy()
    events.loc[events.name == HERO, "date"] = HERO_DATE.isoformat()
    events.loc[events.name == CHECKOUT, "date"] = CHECKOUT_DATE.isoformat()
    return metrics, events


def thin(metrics: pd.DataFrame, seed: int, factors: dict[str, float]) -> pd.DataFrame:
    """Exact binomial thinning of mobile+safari orders: Binomial(n, p) thinned by f is Binomial(n, p*f).

    Keys are ISO dates, or ``">=ISO"`` for every date from then on.
    """
    rng = np.random.default_rng(10_000 + seed)
    out = metrics.copy()
    seg = (out.device == "mobile") & (out.browser == "safari")
    day = pd.to_datetime(out["date"])
    for key, f in factors.items():
        sel = seg & (day >= pd.Timestamp(key[2:])) if key.startswith(">=") else seg & (day == pd.Timestamp(key))
        idx = out.index[sel]
        out.loc[idx, "orders"] = rng.binomial(out.loc[idx, "orders"].to_numpy(), f)
    return out


SCENARIOS: dict[str, Truth] = {
    "planted": Truth(CHECKOUT, 0, "Checkout acts immediately"),
    "hero_immediate": Truth(HERO, 0, "swapped cause: Hero acts immediately"),
    "hero_delayed": Truth(HERO, 1, "Hero acts after a one-day delay (data identical to 'planted')"),
    "checkout_delayed": Truth(CHECKOUT, 1, "Checkout acts after a one-day delay"),
    "same_day": Truth(None, None, "two events on the same date: the data cannot separate them"),
    "weak_12": Truth(CHECKOUT, 0, "Checkout acts immediately, effect -12%"),
    "weak_6": Truth(CHECKOUT, 0, "Checkout acts immediately, effect -6%"),
    "neither": Truth(None, None, "an unlogged change on 20 Oct"),
    "none": Truth(None, None, "no change at all"),
    "staged": Truth(None, None, "Hero and Checkout each contribute half"),
    "gradual_hero": Truth(HERO, None, "Hero ramps in over three days"),
}


def scenario_data(seed: int, name: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    if name in ("planted", "hero_delayed"):
        return simulate(seed)
    if name == "hero_immediate":
        return simulate(seed, effect_start=HERO_DATE)
    if name == "checkout_delayed":
        return simulate(seed, effect_start=date(2026, 10, 15))
    if name == "same_day":
        m, e = simulate(seed)
        e = e.copy()
        e.loc[e.name == HERO, "date"] = CHECKOUT_DATE.isoformat()
        return m, e
    if name == "weak_12":
        return simulate(seed, rel=-0.12)
    if name == "weak_6":
        return simulate(seed, rel=-0.06)
    if name == "neither":
        return simulate(seed, effect_start=date(2026, 10, 20))
    if name == "none":
        return simulate(seed, rel=0.0)
    m, e = simulate(seed, rel=0.0)
    if name == "staged":
        return thin(m, seed, {"2026-10-13": 0.875, ">=2026-10-14": 0.75}), e
    if name == "gradual_hero":
        return thin(m, seed, {"2026-10-13": 0.92, "2026-10-14": 0.84, ">=2026-10-15": 0.75}), e
    raise KeyError(name)


def run_pipeline(metrics: pd.DataFrame, events: pd.DataFrame, *, candidates: list[str] | None = None, **kwargs):
    """The selection pipeline up to candidate testing: quality -> confirm -> localize -> candidates -> verdicts.

    Returns ``(stage, result)``; ``stage`` is ``"stopped: change not confirmed"`` when ``confirm_change`` stops
    the investigation (Requirement 2.3), ``"tested"`` otherwise (``result`` is the CandidateTestingResult).
    """
    from causelens.analytics import check_data_quality, confirm_change, decompose_change
    from causelens.analytics.candidates import test_candidates

    quality = check_data_quality(metrics, events)
    confirmed = confirm_change(metrics, PRE, POST, events, quality=quality)
    if not confirmed.is_real:
        return "stopped: change not confirmed", None
    decomposition = decompose_change(metrics, PRE, POST, events, quality=quality)
    result = test_candidates(metrics, events, PRE, POST, decomposition=decomposition, quality=quality,
                             candidates=candidates, **kwargs)
    return "tested", result
