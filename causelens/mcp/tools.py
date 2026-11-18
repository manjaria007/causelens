"""The MCP tools: a thin, read-only adapter over ``causelens.analytics``.

Every tool

* takes a ``dataset_id`` (data-dependent tools) that resolves to configured local files, plus typed JSON parameters;
* calls the existing analytics function with the same defaults and returns its result model unchanged, so the
  published output schema is the analytics model's own schema and nothing is re-computed or reshaped here;
* turns invalid input into a clear tool error, and leaves insufficient-data results as ordinary results.

The only logic beyond plumbing is a consistency check for the two tools that must rebuild a population from a prior
result (``project_impact`` and ``design_experiment``), and for a prior decomposition used with different windows.
Those checks reject a mismatch loudly; they never alter a number.

This module deliberately has no ``from __future__ import annotations``: the SDK builds each tool's input and output
schema from the real annotations.
"""
import functools
from collections.abc import Callable
from datetime import date
from typing import Literal, ParamSpec, TypeVar

import pandas as pd
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

from causelens.analytics import (
    CandidateMatchResult,
    CandidateTestingResult,
    CandidateVerdict,
    ChangePointResult,
    ConfirmChangeResult,
    DataQualityReport,
    DecompositionResult,
    DetectorParams,
    DidResult,
    ExpectedPattern,
    ExperimentDesign,
    ImpactResult,
    PlaceboResult,
    PowerResult,
    PreTrendResult,
)
from causelens.analytics import build_panel as _build_panel
from causelens.analytics import check_data_quality as _check_data_quality
from causelens.analytics import check_pre_trends as _check_pre_trends
from causelens.analytics import confirm_change as _confirm_change
from causelens.analytics import decompose_change as _decompose_change
from causelens.analytics import design_experiment as _design_experiment
from causelens.analytics import detect_change_point as _detect_change_point
from causelens.analytics import estimate_did as _estimate_did
from causelens.analytics import match_event_candidates as _match_event_candidates
from causelens.analytics import power_analysis as _power_analysis
from causelens.analytics import project_impact as _project_impact
from causelens.analytics import run_placebo as _run_placebo
from causelens.analytics import test_candidates as _test_candidates
from causelens.analytics import test_event_scope as _test_event_scope
from causelens.analytics.loaders import DIMENSIONS
from causelens.mcp.datasets import DatasetCatalog, DatasetError, DatasetRegistry
from causelens.mcp.models import (
    AlphaLevel,
    DatasetId,
    DateWindow,
    FocalSpec,
    PostWindow,
    PreWindow,
    Segment,
    SegmentSpec,
    StrictInt,
)

P = ParamSpec("P")
R = TypeVar("R")

READ_ONLY = ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=False)

#: The tool names, in the order the design lists them, then the pipeline entry points that sit between them.
TOOL_NAMES: tuple[str, ...] = (
    "list_datasets",
    "check_data_quality",
    "confirm_change",
    "detect_change_point",
    "match_event_candidates",
    "decompose_change",
    "test_candidates",
    "test_event_scope",
    "estimate_did",
    "check_pre_trends",
    "run_placebo",
    "project_impact",
    "power_analysis",
    "design_experiment",
)


def _guarded(fn: Callable[P, R]) -> Callable[P, R]:
    """Report invalid input as a clear tool error. Anything else is a crash and keeps the SDK's generic message."""

    @functools.wraps(fn)
    def run(*args: P.args, **kwargs: P.kwargs) -> R:
        try:
            return fn(*args, **kwargs)
        except ToolError:
            raise
        except DatasetError as exc:
            raise ToolError(str(exc)) from exc
        except ValueError as exc:
            raise ToolError(f"Invalid input: {exc}") from exc

    return run


# --------------------------------------------------------------------------- consistency checks


def _require_same_population(did: DidResult, panel) -> None:
    """The DiD result must have been computed on the population the caller describes."""
    onset = pd.Timestamp(did.event_date)
    use = panel.daily[panel.daily["usable"].to_numpy(dtype=bool)]
    pre, post = use[use.index < onset], use[use.index >= onset]
    found = {
        "excluded_dates": list(panel.excluded_dates),
        "n_days_pre": len(pre),
        "n_days_post": len(post),
        "focal_sessions_post": int(post["s_f"].sum()),
        "comparison_sessions_post": int(post["s_c"].sum()),
    }
    given = {k: getattr(did, k) for k in found}
    differs = [f"{k}: result has {given[k]!r}, this dataset and population give {found[k]!r}" for k in found if given[k] != found[k]]
    if differs:
        raise ToolError(
            "The DiD result was not computed on this dataset and population (" + "; ".join(differs) + "). Pass the same "
            "dataset_id, focal, comparison, quality and apply_exclusions that produced it."
        )


def _require_same_windows(decomposition: DecompositionResult | None, pre: DateWindow, post: DateWindow) -> None:
    if decomposition is None:
        return
    got = ((decomposition.pre.start, decomposition.pre.end), (decomposition.post.start, decomposition.post.end))
    if got != (pre.as_tuple(), post.as_tuple()):
        raise ToolError(
            f"The decomposition covers pre {got[0][0]} to {got[0][1]} and post {got[1][0]} to {got[1][1]}, which differs from the "
            f"windows given ({pre.start} to {pre.end}; {post.start} to {post.end}). Use the same windows for both calls."
        )


def _require_same_exclusions(result: CandidateTestingResult, data, quality, apply_exclusions: bool) -> None:
    """Rebuild the focal panel and require the same removed days as the candidate test used."""
    if result.focal is None or result.exclusions is None:
        return
    panel = _build_panel(data.metrics, result.focal.segments, events=data.events, quality=quality, apply_exclusions=apply_exclusions)
    if panel.disclosure != result.exclusions:
        raise ToolError(
            "The candidate-testing result was produced with different exclusions than this call would apply (a different "
            "dataset_id, quality report or apply_exclusions). Pass the same dataset_id, quality and apply_exclusions that "
            "produced it, so the experiment is sized from the same population."
        )


# --------------------------------------------------------------------------- registration


def register_tools(server: MCPServer, registry: DatasetRegistry) -> None:
    """Register every tool on ``server``. The tools read only the datasets in ``registry``."""

    def tool(fn: Callable[P, R]) -> Callable[P, R]:
        server.tool(name=fn.__name__, annotations=READ_ONLY)(fn)
        return fn

    @tool
    @_guarded
    def list_datasets() -> DatasetCatalog:
        """List the configured datasets: id, files, SHA-256 fingerprints, row count, date range and segment values.

        Use the ``id`` as ``dataset_id`` in every data-dependent tool. The fingerprint ties a result to the exact data
        that produced it. Read-only.
        """
        return registry.catalog()

    @tool
    @_guarded
    def check_data_quality(dataset_id: DatasetId, detector_params: DetectorParams | None = None) -> DataQualityReport:
        """Flag days whose conversion or traffic is implausible (tracking breaks, outages), with linked incident events.

        Run this before blaming behaviour. The report's ``excluded_dates`` are what the other tools drop by default;
        pass the whole report as ``quality`` to those tools to reuse exactly these exclusions. Deterministic.
        """
        data = registry.load(dataset_id)
        return _check_data_quality(data.metrics, data.events, detector_params)

    @tool
    @_guarded
    def confirm_change(
        dataset_id: DatasetId,
        pre_window: PreWindow,
        post_window: PostWindow,
        segment: Segment | None = None,
        quality: DataQualityReport | None = None,
        apply_exclusions: bool = True,
        detector_params: DetectorParams | None = None,
        n_bootstrap: StrictInt = 2000,
        seed: StrictInt = 0,
        level: AlphaLevel = 0.95,
        alpha: AlphaLevel = 0.05,
        min_days_per_window: StrictInt = 7,
    ) -> ConfirmChangeResult:
        """Is the change in conversion between two windows real or noise? Change in percentage points, with an interval.

        Insufficient data is a normal result (``status = "insufficient_data"``), not an error. The bootstrap uses
        ``seed``, so the same input gives the same interval. Deterministic.
        """
        data = registry.load(dataset_id)
        return _confirm_change(
            data.metrics, pre_window.as_tuple(), post_window.as_tuple(), data.events, segment=segment, quality=quality,
            apply_exclusions=apply_exclusions, detector_params=detector_params, n_bootstrap=n_bootstrap, seed=seed, level=level,
            alpha=alpha, min_days_per_window=min_days_per_window,
        )

    @tool
    @_guarded
    def detect_change_point(
        dataset_id: DatasetId,
        segment: Segment | None = None,
        search_window: DateWindow | None = None,
        quality: DataQualityReport | None = None,
        apply_exclusions: bool = True,
        detector_params: DetectorParams | None = None,
        adjust_weekdays: bool = True,
        min_segment_days: StrictInt = 7,
        plausible_level: AlphaLevel = 0.95,
        alpha: AlphaLevel = 0.05,
        min_days_for_weekday_adjustment: StrictInt = 28,
        max_iterations: StrictInt = 50,
    ) -> ChangePointResult:
        """When did the conversion level shift? The best date and the set of plausible onset dates.

        The plausible set is approximate and can undercover for small effects. Pass the result to
        ``match_event_candidates``. Deterministic.
        """
        data = registry.load(dataset_id)
        window = None if search_window is None else search_window.as_tuple()
        return _detect_change_point(
            data.metrics, data.events, segment=segment, search_window=window, quality=quality, apply_exclusions=apply_exclusions,
            detector_params=detector_params, adjust_weekdays=adjust_weekdays, min_segment_days=min_segment_days,
            plausible_level=plausible_level, alpha=alpha, min_days_for_weekday_adjustment=min_days_for_weekday_adjustment,
            max_iterations=max_iterations,
        )

    @tool
    @_guarded
    def match_event_candidates(dataset_id: DatasetId, change_point: ChangePointResult, lead_days: StrictInt = 3) -> CandidateMatchResult:
        """Which logged events fall in, or up to ``lead_days`` before, the plausible onset range? Date coincidences only.

        A match is not evidence of cause; test it with ``test_candidates`` or ``test_event_scope``. Needs the dataset's
        event log. Deterministic.
        """
        data = registry.load(dataset_id, events="required")
        assert data.events is not None
        return _match_event_candidates(change_point, data.events, lead_days=lead_days)

    @tool
    @_guarded
    def decompose_change(
        dataset_id: DatasetId,
        pre_window: PreWindow,
        post_window: PostWindow,
        dimensions: tuple[str, ...] = DIMENSIONS,
        include_pairs: bool = True,
        quality: DataQualityReport | None = None,
        apply_exclusions: bool = True,
        detector_params: DetectorParams | None = None,
        alpha: AlphaLevel = 0.05,
        level: AlphaLevel = 0.95,
        explain_threshold: float = 0.8,
        max_localized_traffic_share: float = 0.5,
        fdr_family: Literal["scan", "partition"] = "scan",
        inference: Literal["day_level", "binomial"] = "day_level",
        min_days_per_window: StrictInt = 7,
    ) -> DecompositionResult:
        """Where did the change happen? Rate and mix effects per segment, FDR-adjusted tests, and a localization.

        Describes where the change is concentrated, not why. Insufficient data is a normal result. Deterministic.
        """
        data = registry.load(dataset_id)
        return _decompose_change(
            data.metrics, pre_window.as_tuple(), post_window.as_tuple(), data.events, dimensions=dimensions,
            include_pairs=include_pairs, quality=quality, apply_exclusions=apply_exclusions, detector_params=detector_params,
            alpha=alpha, level=level, explain_threshold=explain_threshold, max_localized_traffic_share=max_localized_traffic_share,
            fdr_family=fdr_family, inference=inference, min_days_per_window=min_days_per_window,
        )

    @tool
    @_guarded
    def test_candidates(
        dataset_id: DatasetId,
        pre_window: PreWindow,
        post_window: PostWindow,
        focal: SegmentSpec | None = None,
        decomposition: DecompositionResult | None = None,
        candidates: list[str] | None = None,
        expected_patterns: dict[str, ExpectedPattern] | None = None,
        lag_days: dict[str, StrictInt] | None = None,
        sensitivity_lags: tuple[StrictInt, ...] = (0, 1),
        alpha: AlphaLevel = 0.05,
        level: AlphaLevel = 0.95,
        quality: DataQualityReport | None = None,
        apply_exclusions: bool = True,
        detector_params: DetectorParams | None = None,
        lead_days: StrictInt = 3,
        did_cov: Literal["cluster", "hc1"] = "hc1",
    ) -> CandidateTestingResult:
        """Verdicts (supported, rejected, inconclusive) for each candidate event, with the evidence and its strength.

        The focal segment is the localization in ``decomposition`` (or computed) unless ``focal`` is given. A supported
        verdict is conditional on the stated lag (default 0: the effect begins on the event date); lags 0 and 1 are always
        evaluated. Needs the dataset's event log. Deterministic.
        """
        data = registry.load(dataset_id, events="required")
        assert data.events is not None
        _require_same_windows(decomposition, pre_window, post_window)
        return _test_candidates(
            data.metrics, data.events, pre_window.as_tuple(), post_window.as_tuple(), focal=focal, decomposition=decomposition,
            candidates=candidates, expected_patterns=expected_patterns, lag_days=lag_days, sensitivity_lags=sensitivity_lags,
            alpha=alpha, level=level, quality=quality, apply_exclusions=apply_exclusions, detector_params=detector_params,
            lead_days=lead_days, did_cov=did_cov,
        )

    @tool
    @_guarded
    def test_event_scope(
        dataset_id: DatasetId,
        event: str,
        pre_window: PreWindow,
        post_window: PostWindow,
        focal: SegmentSpec | None = None,
        decomposition: DecompositionResult | None = None,
        candidates: list[str] | None = None,
        expected_patterns: dict[str, ExpectedPattern] | None = None,
        lag_days: dict[str, StrictInt] | None = None,
        sensitivity_lags: tuple[StrictInt, ...] = (0, 1),
        alpha: AlphaLevel = 0.05,
        level: AlphaLevel = 0.95,
        quality: DataQualityReport | None = None,
        apply_exclusions: bool = True,
        detector_params: DetectorParams | None = None,
        lead_days: StrictInt = 3,
        did_cov: Literal["cluster", "hc1"] = "hc1",
    ) -> CandidateVerdict:
        """The verdict for one named event. The other candidates still enter the joint timing model as rivals.

        Same inputs and conventions as ``test_candidates``; returns only the named event's verdict. Deterministic.
        """
        data = registry.load(dataset_id, events="required")
        assert data.events is not None
        _require_same_windows(decomposition, pre_window, post_window)
        return _test_event_scope(
            event, data.metrics, data.events, pre_window.as_tuple(), post_window.as_tuple(), focal=focal,
            decomposition=decomposition, candidates=candidates, expected_patterns=expected_patterns, lag_days=lag_days,
            sensitivity_lags=sensitivity_lags, alpha=alpha, level=level, quality=quality, apply_exclusions=apply_exclusions,
            detector_params=detector_params, lead_days=lead_days, did_cov=did_cov,
        )

    @tool
    @_guarded
    def estimate_did(
        dataset_id: DatasetId,
        focal: FocalSpec,
        event_date: date,
        comparison: SegmentSpec | None = None,
        quality: DataQualityReport | None = None,
        apply_exclusions: bool = True,
        cov: Literal["cluster", "hc1"] = "hc1",
        weights: Literal["sessions", "none"] = "sessions",
        level: AlphaLevel = 0.95,
    ) -> DidResult:
        """Differential effect of the focal population from ``event_date`` on, in percentage points, with an interval.

        ``event_date`` is the first treated day (the event date plus any lag). The comparison defaults to all other
        traffic. The estimate is a differential effect and assumes the comparison was unaffected; the assumptions are
        in the result. Insufficient data or no valid comparison is a normal result (``status = "insufficient"``).
        """
        data = registry.load(dataset_id)
        return _estimate_did(
            data.metrics, focal, event_date, comparison=comparison, events=data.events, quality=quality,
            apply_exclusions=apply_exclusions, cov=cov, weights=weights, level=level,
        )

    @tool
    @_guarded
    def check_pre_trends(
        dataset_id: DatasetId,
        focal: FocalSpec,
        event_date: date,
        comparison: SegmentSpec | None = None,
        quality: DataQualityReport | None = None,
        apply_exclusions: bool = True,
        cov: Literal["cluster", "hc1"] = "hc1",
        weights: Literal["sessions", "none"] = "sessions",
    ) -> PreTrendResult:
        """Was the focal population already trending away from the comparison before ``event_date``? Slope per day, in pp."""
        data = registry.load(dataset_id)
        return _check_pre_trends(
            data.metrics, focal, event_date, comparison=comparison, events=data.events, quality=quality,
            apply_exclusions=apply_exclusions, cov=cov, weights=weights,
        )

    @tool
    @_guarded
    def run_placebo(
        dataset_id: DatasetId,
        focal: FocalSpec,
        event_date: date,
        comparison: SegmentSpec | None = None,
        quality: DataQualityReport | None = None,
        apply_exclusions: bool = True,
        cov: Literal["cluster", "hc1"] = "hc1",
        weights: Literal["sessions", "none"] = "sessions",
        dates: list[date] | None = None,
    ) -> PlaceboResult:
        """DiD at fake event dates inside the pre-period. A real-looking effect there is evidence against the design.

        ``dates`` defaults to a fixed grid of three dates. A requested date that cannot be evaluated leaves the check
        ``not_evaluable``; it is never counted as passed.
        """
        data = registry.load(dataset_id)
        return _run_placebo(
            data.metrics, focal, event_date, comparison=comparison, events=data.events, quality=quality,
            apply_exclusions=apply_exclusions, cov=cov, weights=weights, dates=dates,
        )

    @tool
    @_guarded
    def project_impact(
        dataset_id: DatasetId,
        did: DidResult,
        focal: FocalSpec,
        comparison: SegmentSpec | None = None,
        quality: DataQualityReport | None = None,
        apply_exclusions: bool = True,
    ) -> ImpactResult:
        """Scale a DiD result to the overall conversion rate (pp) and to orders over the post period, with intervals.

        The population is rebuilt from ``dataset_id``, ``focal``, ``comparison``, ``quality`` and ``apply_exclusions``,
        which must be the ones that produced ``did``. The call fails, naming the difference, if the rebuilt population
        does not match the DiD result's days, exclusions and session counts.
        """
        data = registry.load(dataset_id)
        panel = _build_panel(
            data.metrics, focal, comparison=comparison, events=data.events, quality=quality, apply_exclusions=apply_exclusions
        )
        _require_same_population(did, panel)
        return _project_impact(did, panel)

    @tool
    @_guarded
    def power_analysis(
        baseline: float,
        mde_pp: float,
        alpha: AlphaLevel = 0.05,
        power: AlphaLevel = 0.8,
        allocation_ratio: float = 1.0,
        n_comparisons: StrictInt = 1,
    ) -> PowerResult:
        """Sessions per arm for a two-proportion test. Needs no dataset.

        ``baseline`` is a fraction in (0, 1) (0.05 is 5%). ``mde_pp`` is the signed change to detect in percentage points
        (+0.5 is half a point), so ``target_rate = baseline + mde_pp / 100``. Power is "achieved power under the normal
        approximation", per comparison; several comparisons divide alpha. Deterministic.
        """
        return _power_analysis(
            baseline, mde_pp, alpha=alpha, power=power, allocation_ratio=allocation_ratio, n_comparisons=n_comparisons
        )

    @tool
    @_guarded
    def design_experiment(
        dataset_id: DatasetId,
        candidate_testing: CandidateTestingResult,
        post_window: PostWindow,
        quality: DataQualityReport | None = None,
        apply_exclusions: bool = True,
        target_candidate: str | None = None,
        arms: Literal["leading", "each_candidate"] = "leading",
        mde_pp: float | None = None,
        alpha: AlphaLevel = 0.05,
        power: AlphaLevel = 0.8,
        allocation_ratio: float = 1.0,
    ) -> ExperimentDesign:
        """A randomized rollback experiment sized from the observed focal traffic. A planning aid, not a causal claim.

        The population is the focal segment in ``candidate_testing``; the baseline and daily traffic come from
        ``dataset_id`` over ``post_window``. Missing inputs give ``status = "needs_input"`` and name what is missing.
        Pass the ``dataset_id``, ``quality`` and ``apply_exclusions`` that produced ``candidate_testing``; the call fails
        if the rebuilt population removes different days.
        """
        data = registry.load(dataset_id)
        _require_same_exclusions(candidate_testing, data, quality, apply_exclusions)
        return _design_experiment(
            data.metrics, candidate_testing, post_window.as_tuple(), events=data.events, quality=quality,
            apply_exclusions=apply_exclusions, target_candidate=target_candidate, arms=arms, mde_pp=mde_pp, alpha=alpha,
            power=power, allocation_ratio=allocation_ratio,
        )
