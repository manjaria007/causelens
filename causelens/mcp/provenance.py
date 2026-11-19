"""Bind a prior result to the data it came from, by reproducing it.

A prior result (a quality report, a decomposition, a DiD estimate, a candidate-testing result) arrives as JSON. Matching
dates or session totals does not show it came from the dataset and population the caller names: a decline from one dataset
passes those checks against another dataset with the same sessions and the opposite effect. So the adapter recomputes what
it can with the existing analytics, from the named dataset and the settings recorded in the result itself, and requires the
supplied result to equal the recomputation (floats to a relative 1e-9).

* quality report: ``check_data_quality`` with the report's own ``params``.
* DiD result: ``did_on_panel`` at its event date with its own ``cov``, ``weights`` and interval level.
* candidate-testing result: the exclusions, every stored DiD and impact, and the joint timing analysis at the declared
  onsets (the same series the candidate test used).
* decomposition: for every partition and segment it lists, the sessions and orders of each window after the window's own
  excluded days (not only the window totals, which two different segmentations can share), and no segment may be missing.

This only refuses. It never changes a value. A result that cannot be reproduced from the stated inputs is refused with the
fields that differ. Two limits: a change point is not checked (it records too few of its settings to recompute faithfully,
and it carries no dataset-derived number into another tool), and a result is checked only on what it stores, so a
candidate-testing result with no DiD and no timing analysis has little to check.
"""
import math
from typing import Any

import pandas as pd
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import BaseModel

from causelens.analytics import (
    CandidateTestingResult,
    DataQualityReport,
    DecompositionResult,
    DidResult,
    build_panel,
    check_data_quality,
    project_impact,
)
from causelens.analytics.did import Panel, did_on_panel
from causelens.analytics.timing import analyze_timing
from causelens.mcp.datasets import LoadedDataset

REL_TOL = 1e-9
ABS_TOL = 1e-12
MAX_REPORTED = 8


def _short(value: Any) -> str:
    text = repr(value)
    return text if len(text) <= 48 else text[:45] + "..."


def differences(expected: Any, supplied: Any, path: str = "") -> list[str]:
    """Where two JSON-like values differ (floats compared to a relative 1e-9), as ``path: supplied X, recomputed Y``."""
    if isinstance(expected, dict) and isinstance(supplied, dict):
        out: list[str] = []
        for key in list(expected) + [k for k in supplied if k not in expected]:
            sub = f"{path}.{key}" if path else str(key)
            if key not in expected or key not in supplied:
                out.append(f"{sub}: present in only one of them")
            else:
                out += differences(expected[key], supplied[key], sub)
        return out
    if isinstance(expected, list) and isinstance(supplied, list):
        if len(expected) != len(supplied):
            return [f"{path}: supplied has {len(supplied)} items, recomputed has {len(expected)}"]
        return [d for i, (e, s) in enumerate(zip(expected, supplied, strict=True)) for d in differences(e, s, f"{path}[{i}]")]
    numeric = (int, float)
    if isinstance(expected, numeric) and isinstance(supplied, numeric) and not isinstance(expected, bool) and not isinstance(supplied, bool):
        same = math.isclose(float(expected), float(supplied), rel_tol=REL_TOL, abs_tol=ABS_TOL)
    else:
        same = expected == supplied
    return [] if same else [f"{path}: supplied {_short(supplied)}, recomputed {_short(expected)}"]


def _dump(model: BaseModel) -> Any:
    return model.model_dump(mode="json")


def _compare(expected: BaseModel, supplied: BaseModel, prefix: str = "") -> list[str]:
    return differences(_dump(expected), _dump(supplied), prefix)


def _listing(diffs: list[str]) -> str:
    shown = "; ".join(diffs[:MAX_REPORTED])
    return shown + (f"; and {len(diffs) - MAX_REPORTED} more" if len(diffs) > MAX_REPORTED else "")


def require_quality(quality: DataQualityReport | None, data: LoadedDataset) -> None:
    """The report must be what the detector gives for this dataset with the report's own settings."""
    if quality is None:
        return
    diffs = _compare(check_data_quality(data.metrics, data.events, quality.params), quality)
    if diffs:
        raise ToolError(
            f"The quality report was not produced from this dataset with its own detector settings ({_listing(diffs)}). "
            "Pass the report that check_data_quality returns for this dataset_id (with detector_params if you changed them), "
            "or leave quality out. To switch the exclusions off, use apply_exclusions=false."
        )


def require_did(did: DidResult, panel: Panel) -> None:
    """The DiD result must be what ``did_on_panel`` gives on this panel at its event date with its own settings."""
    level = did.effect_pp.level if did.effect_pp is not None else 0.95
    expected = did_on_panel(panel, did.event_date, cov=did.cov, weights=did.weights, level=level)
    diffs = _compare(expected, did)
    if diffs:
        raise ToolError(
            f"The DiD result was not computed on this dataset and population ({_listing(diffs)}). Pass the same dataset_id, "
            "focal, comparison, quality and apply_exclusions that produced it."
        )


def _segment_key(labels: dict[str, str], dimensions: list[str]) -> tuple[str, ...]:
    return tuple(str(labels.get(d, "")) for d in dimensions)


def _table(frame: pd.DataFrame, dims: list[str]) -> dict[tuple[str, ...], tuple[int, int]]:
    """(sessions, orders) per segment, keyed by the tuple of dimension values."""
    grouped = frame.groupby(dims)[["sessions", "orders"]].sum()
    out: dict[tuple[str, ...], tuple[int, int]] = {}
    for key, sessions, orders in zip(grouped.index, grouped["sessions"], grouped["orders"], strict=True):
        parts = key if isinstance(key, tuple) else (key,)
        out[tuple(str(p) for p in parts)] = (int(sessions), int(orders))
    return out


def require_decomposition(decomposition: DecompositionResult | None, data: LoadedDataset) -> None:
    """Every segment's sessions and orders in each window must match this dataset, after the window's own excluded days.

    Window totals alone are not enough: moving orders between segments leaves every total unchanged and still points a
    localization at the wrong segment. So each listed segment of each partition is recomputed, and a segment that the data
    have but the result lacks is reported.
    """
    if decomposition is None or decomposition.status != "ok":
        return
    days = data.metrics["date"]
    diffs: list[str] = []
    grouped: dict[str, pd.DataFrame] = {}
    for window in (decomposition.pre, decomposition.post):
        excluded = pd.to_datetime(list(window.excluded_dates))
        rows = (days >= pd.Timestamp(window.start)) & (days <= pd.Timestamp(window.end)) & ~days.isin(excluded)
        for field in ("sessions", "orders"):
            have = int(data.metrics.loc[rows, field].sum())
            if have != getattr(window, field):
                diffs.append(f"{window.name} window {field}: supplied {getattr(window, field):,}, this dataset gives {have:,}")
        grouped[window.name] = data.metrics.loc[rows]
    for partition in decomposition.partitions:
        dims = list(partition.dimensions)
        if not all(d in data.metrics.columns for d in dims):
            diffs.append(f"partition {partition.name}: unknown dimension in {dims}")
            continue
        counts = {name: _table(frame, dims) for name, frame in grouped.items()}
        listed = set()
        for seg in partition.segments:
            key = _segment_key(seg.labels, dims)
            listed.add(key)
            for window_name, field, supplied in (
                ("pre", "sessions", seg.sessions_pre), ("post", "sessions", seg.sessions_post),
                ("pre", "orders", seg.orders_pre), ("post", "orders", seg.orders_post),
            ):
                have = counts[window_name].get(key, (0, 0))[0 if field == "sessions" else 1]
                if have != supplied:
                    diffs.append(f"{seg.label} ({partition.name}) {window_name} {field}: supplied {supplied:,}, this dataset gives {have:,}")
        for window_name, table in counts.items():
            for key, (sessions, orders) in table.items():
                if key not in listed and (sessions > 0 or orders > 0):
                    label = ", ".join(f"{d}={v}" for d, v in zip(dims, key, strict=True))
                    diffs.append(f"{label} ({partition.name}) has traffic in this dataset's {window_name} window but is missing from the result")
        if len(diffs) > 4 * MAX_REPORTED:
            break
    if diffs:
        raise ToolError(
            f"The decomposition was not computed on this dataset ({_listing(sorted(set(diffs), key=diffs.index))}). Pass the "
            "dataset_id, windows and quality that produced it."
        )


def _timing_level(result: CandidateTestingResult) -> float:
    """The interval level the result was made at. Every share set records it, even when nothing was estimable (no step, no
    total, no DiD), so a 90% result with no interval to read still reproduces. 95% only if nothing at all carries a level."""
    timing = result.timing
    assert timing is not None
    levels = [iv.level for iv in (timing.total_step_pp, *(c.timing.step_pp for c in result.candidates if c.timing is not None)) if iv is not None]
    levels += [c.share_set.level for c in timing.candidates.values()]
    return levels[0] if levels else 0.95


def require_candidate_result(
    result: CandidateTestingResult, data: LoadedDataset, quality: DataQualityReport | None, apply_exclusions: bool
) -> None:
    """Reproduce what a candidate-testing result stores about the data: exclusions, DiDs, impacts and the timing analysis."""
    if result.status != "ok" or result.focal is None or result.exclusions is None:
        return
    try:
        panel = build_panel(data.metrics, result.focal.segments, events=data.events, quality=quality, apply_exclusions=apply_exclusions)
    except ValueError as exc:
        raise ToolError(f"The candidate-testing result's focal population cannot be rebuilt from this dataset: {exc}") from exc
    diffs = _compare(panel.disclosure, result.exclusions, "exclusions")
    for i, cand in enumerate(result.candidates):
        did = cand.differential_effect
        if did is None:
            continue
        level = did.effect_pp.level if did.effect_pp is not None else 0.95
        expected = did_on_panel(panel, did.event_date, cov=did.cov, weights=did.weights, level=level)
        diffs += _compare(expected, did, f"candidates[{i}].differential_effect")
        if cand.impact is not None:
            diffs += _compare(project_impact(expected, panel), cand.impact, f"candidates[{i}].impact")
    if result.timing is not None and result.candidates:
        level = _timing_level(result)
        usable = panel.usable_days
        series = panel.daily.loc[usable, "diff_pp"].to_numpy(float)
        onsets = {c.event: c.onset_date for c in result.candidates}
        diffs += _compare(analyze_timing(usable, series, onsets, alpha=result.timing.alpha, level=level), result.timing, "timing")
    if diffs:
        raise ToolError(
            f"The candidate-testing result was not computed on this dataset and population ({_listing(diffs)}). Pass the "
            "dataset_id, quality and apply_exclusions that produced it, so the experiment is sized from the same days."
        )
