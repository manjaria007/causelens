"""Datasets that look like the reference data to a session or day count, but are not it. For the MCP binding tests.

``rising`` has the same sessions on every row but the opposite effect (a rise where the reference has a decline), ``twin``
gives mobile/chrome the same daily sessions as mobile/safari (so two populations are equal-sized), and ``plain`` has
no tracking outage, so its data-quality report excludes nothing. Built in memory from the reference frame; the
reference files and the answer key are never touched.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

ONSET = pd.Timestamp("2026-10-14")
KEYS = ["date", "channel", "customer_type"]


def _focal(df: pd.DataFrame) -> pd.Series:
    return (df["device"] == "mobile") & (df["browser"] == "safari")


def rising(metrics: pd.DataFrame) -> pd.DataFrame:
    """Same sessions everywhere. Mobile Safari orders after the onset are +25% of the no-effect level (the reference has -25%)."""
    out = metrics.copy()
    sel = _focal(out) & (pd.to_datetime(out["date"]) >= ONSET)
    undone = out.loc[sel, "orders"].to_numpy(float) / 0.75  # the planted effect multiplies conversion by 0.75
    out.loc[sel, "orders"] = np.minimum(np.round(undone * 1.25), out.loc[sel, "sessions"]).astype("int64")
    return out


def twin(metrics: pd.DataFrame) -> pd.DataFrame:
    """mobile/chrome gets mobile/safari's sessions for the same (date, channel, customer type), and orders in proportion."""
    out = metrics.copy()
    safari = out[_focal(out)].set_index(KEYS)["sessions"]
    chrome = (out["device"] == "mobile") & (out["browser"] == "chrome")
    keys = pd.MultiIndex.from_frame(out.loc[chrome, KEYS])
    new_sessions = safari.reindex(keys).to_numpy()
    assert not np.isnan(new_sessions).any(), "every mobile/chrome row needs a mobile/safari row with the same keys"
    old_sessions = out.loc[chrome, "sessions"].to_numpy(float)
    ratio = np.divide(new_sessions, old_sessions, out=np.ones_like(old_sessions), where=old_sessions > 0)
    out.loc[chrome, "sessions"] = new_sessions.astype("int64")
    out.loc[chrome, "orders"] = np.minimum(np.round(out.loc[chrome, "orders"].to_numpy(float) * ratio), new_sessions).astype("int64")
    return out


def plain(metrics: pd.DataFrame) -> pd.DataFrame:
    """The reference data without the 2026-09-20 tracking outage (desktop orders restored to their neighbours' rate)."""
    out = metrics.copy()
    day = pd.to_datetime(out["date"])
    desktop = out["device"] == "desktop"
    neighbours = desktop & day.isin([pd.Timestamp("2026-09-19"), pd.Timestamp("2026-09-21")])
    rate = out.loc[neighbours, "orders"].sum() / out.loc[neighbours, "sessions"].sum()
    hit = desktop & (day == pd.Timestamp("2026-09-20"))
    out.loc[hit, "orders"] = np.round(out.loc[hit, "sessions"].to_numpy(float) * rate).astype("int64")
    return out
