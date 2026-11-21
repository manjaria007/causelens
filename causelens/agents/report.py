"""Assemble the structured result from the log. Every number is read through a ``Tracer``; nothing is computed here.

The functions below only choose *which* logged value goes where. Where the tools left a field null, the result field is
``None`` (an absent value is never replaced by a number).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from causelens.agents import wording
from causelens.agents.claims import DESIGN, ClaimEligibility
from causelens.agents.result import (
    CandidateFinding,
    CausalEstimate,
    ChangePointFinding,
    ConfirmedChange,
    DataQualityFlagNote,
    DataQualityNotes,
    DatasetFacts,
    EvidenceSummary,
    ExperimentArmPlan,
    ExperimentRecommendation,
    InvestigationResult,
    LagView,
    LocalizationFinding,
    Status,
    Strength,
    Unexplained,
)
from causelens.agents.state import InvestigationState
from causelens.agents.trace import RenderedStatement, Traced, Tracer

STRENGTH_ORDER: tuple[Strength, ...] = ("insufficient", "weak", "moderate", "strong")


@dataclass(frozen=True)
class Stop:
    status: Status
    reason: str
    call: int | None = None


@dataclass(frozen=True)
class EstimateCalls:
    """The standalone difference-in-differences calls for one supported candidate (``index`` is its place in the test result)."""

    index: int
    did: int
    pre_trend: int
    placebo: int
    impact: int
    matches_embedded: bool


@dataclass
class Facts:
    """Which log entry holds each stage's result. Filled by the orchestrator as it goes."""

    catalog: int | None = None
    quality: int | None = None
    confirm: int | None = None
    change_point: int | None = None
    match: int | None = None
    decomposition: int | None = None
    testing: int | None = None
    design: int | None = None
    estimates: list[EstimateCalls] = field(default_factory=list)
    events_available: bool = True


def _ref(state: InvestigationState, seq: int) -> dict[str, Any]:
    result = state.call(seq).result
    assert result is not None
    return result


def build_dataset(t: Tracer, f: Facts) -> DatasetFacts | None:
    if f.catalog is None:
        return None
    catalog = _ref(t.state, f.catalog)
    wanted = t.state.request.dataset_id
    index = next((i for i, d in enumerate(catalog["datasets"]) if d["id"] == wanted), None)
    if index is None:
        return None
    base = f"/datasets/{index}"
    return DatasetFacts(
        id=t.value(f.catalog, f"{base}/id", "dataset id", "text"),
        n_rows=t.value(f.catalog, f"{base}/n_rows", "metrics rows", "count"),
        first_date=t.value(f.catalog, f"{base}/date_range/0", "first date", "date"),
        last_date=t.value(f.catalog, f"{base}/date_range/1", "last date", "date"),
        metrics_sha256=t.value(f.catalog, f"{base}/metrics_sha256", "metrics fingerprint", "text"),
        events_sha256=t.maybe(f.catalog, f"{base}/events_sha256", "events fingerprint", "text"),
    )


def build_quality(t: Tracer, f: Facts) -> DataQualityNotes | None:
    if f.quality is None:
        return None
    q, doc = f.quality, _ref(t.state, f.quality)
    flags = [
        DataQualityFlagNote(
            date=t.value(q, f"/flags/{i}/date", "flagged date", "date"),
            segment=t.value(q, f"/flags/{i}/segment", "flagged segment", "text"),
            kind=t.value(q, f"/flags/{i}/kind", "flag kind", "text"),
            reason=t.value(q, f"/flags/{i}/reason", "flag reason", "text"),
            linked_event=t.maybe(q, f"/flags/{i}/linked_event/name", "linked incident", "text") if flag.get("linked_event") else None,
        )
        for i, flag in enumerate(doc["flags"])
    ]
    return DataQualityNotes(
        n_days_checked=t.value(q, "/n_days_checked", "days checked", "days"),
        excluded_dates=[t.value(q, f"/excluded_dates/{i}", "excluded date", "date") for i in range(len(doc["excluded_dates"]))],
        missing_dates=[t.value(q, f"/missing_dates/{i}", "missing date", "date") for i in range(len(doc["missing_dates"]))],
        exclusion_statement=t.value(q, "/exclusion_statement", "exclusion statement", "text"),
        flags=flags, note=wording.DATA_QUALITY_NOTE,
    )


def build_confirm(t: Tracer, f: Facts) -> ConfirmedChange | None:
    if f.confirm is None:
        return None
    c, doc = f.confirm, _ref(t.state, f.confirm)
    n = [t.value(c, "/pre/n_days_used", "days used before", "days"), t.value(c, "/post/n_days_used", "days used after", "days"),
         t.value(c, "/pre/sessions", "sessions before", "sessions"), t.value(c, "/post/sessions", "sessions after", "sessions")]
    needed: list[Traced] = []
    if doc.get("data_needed"):
        for key, unit in (("minimum_detectable_change_pp", "pp"), ("days_per_window_for_observed_change", "days"), ("additional_days_per_window", "days")):
            item = t.maybe(c, f"/data_needed/{key}", key.replace("_", " "), unit)  # type: ignore[arg-type]
            if item is not None:
                needed.append(item)
        needed.append(t.value(c, "/data_needed/note", "data needed", "text"))
    noise = doc.get("noise_test") or {}
    return ConfirmedChange(
        status=t.value(c, "/status", "confirmation status", "text"), verdict=t.value(c, "/verdict", "confirmation verdict", "text"),
        summary=t.value(c, "/summary", "confirmation summary", "text"),
        pre_conversion=t.maybe_interval(c, "/pre/conversion", "conversion before", "fraction", n[:1] + n[2:3]),
        post_conversion=t.maybe_interval(c, "/post/conversion", "conversion after", "fraction", n[1:2] + n[3:4]),
        abs_change_pp=t.maybe_interval(c, "/abs_change_pp", "absolute change", "pp", n),
        rel_change=t.maybe_interval(c, "/rel_change", "relative change", "fraction", n),
        noise_p_value=t.maybe(c, "/noise_test/p_value", "noise-test p-value", "p_value") if noise else None,
        effect_size_pre_sd=t.maybe(c, "/noise_test/effect_size_pre_sd", "effect size in pre-period standard deviations", "sd") if noise else None,
        data_needed=needed,
    )


def build_change_point(t: Tracer, f: Facts) -> ChangePointFinding | None:
    if f.change_point is None:
        return None
    p, doc = f.change_point, _ref(t.state, f.change_point)
    matched: list[Traced] = []
    statement = None
    if f.match is not None:
        matched = [t.value(f.match, f"/candidates/{i}/name", "event near the onset", "text") for i in range(len(_ref(t.state, f.match)["candidates"]))]
        statement = t.value(f.match, "/statement", "match statement", "text")
    return ChangePointFinding(
        status=t.value(p, "/status", "change point status", "text"), best_date=t.maybe(p, "/best_date", "best onset date", "date"),
        plausible_dates=[t.value(p, f"/plausible_dates/{i}", "plausible onset date", "date") for i in range(len(doc["plausible_dates"]))],
        plausible_level=t.value(p, "/plausible_level", "plausible set level", "level"), direction=t.maybe(p, "/direction", "direction", "text"),
        summary=t.value(p, "/summary", "change point summary", "text"), matched_events=matched, match_statement=statement,
    )


def build_localization(t: Tracer, f: Facts) -> LocalizationFinding | None:
    if f.decomposition is None:
        return None
    d, doc = f.decomposition, _ref(t.state, f.decomposition)
    loc = doc.get("localization")
    common = {
        "status": t.value(d, "/status", "decomposition status", "text"),
        "n_tests_scanned": t.value(d, "/n_tests_scanned", "segments tested", "count"),
        "fdr_family": t.value(d, "/fdr_family", "FDR family", "text"), "alpha": t.value(d, "/alpha", "alpha", "level"),
        "overall_change_pp": t.maybe(d, "/change_pp", "overall change", "pp"), "multiple_comparisons": wording.MULTIPLE_COMPARISONS,
    }
    if not loc:
        return LocalizationFinding(
            localized=False, partition=None, segments=[], share_of_change=None, traffic_share_post=None, all_segments_significant=None,
            statement=None, reason_if_not_localized=t.maybe(d, "/localization_note", "why not localized", "text"), **common,
        )
    return LocalizationFinding(
        localized=True, partition=t.value(d, "/localization/partition", "partition", "text"),
        segments=[t.value(d, f"/localization/segments/{i}", "localized segment", "text") for i in range(len(loc["segments"]))],
        share_of_change=t.value(d, "/localization/selected_share_of_change", "share of the overall change", "share"),
        traffic_share_post=t.value(d, "/localization/traffic_share_post", "share of post-window sessions", "share"),
        all_segments_significant=t.value(d, "/localization/all_selected_significant", "all localized segments significant after FDR", "flag"),
        statement=t.value(d, "/localization/statement", "localization statement", "text"), reason_if_not_localized=None, **common,
    )


_WHY = {"supported": wording.WHY_LEADING, "rejected": wording.WHY_REJECTED, "inconclusive": wording.WHY_ALTERNATIVE}


def build_candidates(t: Tracer, f: Facts) -> list[CandidateFinding]:
    if f.testing is None:
        return []
    c, doc = f.testing, _ref(t.state, f.testing)
    out = []
    for i, cand in enumerate(doc["candidates"]):
        b = f"/candidates/{i}"
        out.append(CandidateFinding(
            event=t.value(c, f"{b}/event", "event", "text"), event_type=t.value(c, f"{b}/event_type", "event type", "text"),
            event_date=t.value(c, f"{b}/event_date", "event date", "date"), lag_days=t.value(c, f"{b}/lag_days", "evaluated lag", "days"),
            onset_date=t.value(c, f"{b}/onset_date", "onset date", "date"), verdict=t.value(c, f"{b}/verdict", "verdict", "text"),
            headline=t.value(c, f"{b}/headline", "headline", "text"), hypothesis=t.value(c, f"{b}/hypothesis", "hypothesis", "text"),
            reasons=t.texts(c, f"{b}/reasons", "reason"),
            attribution_ambiguous=t.value(c, f"{b}/attribution/ambiguous", "release attribution ambiguous", "flag"),
            attribution_note=t.value(c, f"{b}/attribution/note", "attribution note", "text"),
            evidence_strength=t.maybe(c, f"{b}/evidence/strength", "evidence strength", "text") if cand.get("evidence") else None,
            lag_sensitivity=[
                LagView(lag_days=t.value(c, f"{b}/lag_sensitivity/{j}/lag_days", "lag", "days"),
                        onset_date=t.value(c, f"{b}/lag_sensitivity/{j}/onset_date", "onset at that lag", "date"),
                        verdict=t.value(c, f"{b}/lag_sensitivity/{j}/verdict", "verdict at that lag", "text"),
                        headline=t.value(c, f"{b}/lag_sensitivity/{j}/headline", "headline at that lag", "text"))
                for j in range(len(cand["lag_sensitivity"]))
            ],
            why=_WHY[cand["verdict"]],
        ))
    return out


def build_estimates(t: Tracer, f: Facts, claims: list[ClaimEligibility]) -> list[CausalEstimate]:
    assert f.testing is not None
    out = []
    for calls, claim in zip(f.estimates, claims, strict=True):
        did, pre, plc, imp, tested = calls.did, calls.pre_trend, calls.placebo, calls.impact, f.testing
        b = f"/candidates/{calls.index}"
        n = [t.value(did, "/n_obs", "observations", "count"), t.value(did, "/n_days_pre", "days before", "days"),
             t.value(did, "/n_days_post", "days after", "days"), t.value(did, "/focal_sessions_post", "focal sessions after", "sessions"),
             t.value(did, "/comparison_sessions_post", "comparison sessions after", "sessions")]
        n_imp = [t.value(imp, "/focal_sessions_post", "focal sessions after", "sessions"), t.value(imp, "/total_sessions_post", "all sessions after", "sessions"),
                 t.value(imp, "/n_days_post", "days after", "days")]
        pre_doc = _ref(t.state, pre)
        out.append(CausalEstimate(
            event=t.value(tested, f"{b}/event", "event", "text"), onset_date=t.value(tested, f"{b}/onset_date", "onset date", "date"),
            focal=t.value(tested, "/focal/label", "focal segment", "text"), comparison=t.value(tested, "/focal/comparison", "comparison", "text"),
            design=DESIGN, effect_pp=t.maybe_interval(did, "/effect_pp", "differential effect", "pp", n), status=t.value(did, "/status", "estimate status", "text"),
            overall_pp=t.maybe_interval(imp, "/overall_pp", "effect on the overall rate", "pp", n_imp),
            lost_orders=t.maybe_interval(imp, "/lost_orders", "lost orders in the post-period", "orders", n_imp),
            pre_trend_status=t.value(pre, "/status", "pre-trend check", "text"),
            pre_trend_slope_pp_per_day=t.maybe_interval(pre, "/slope_difference_pp_per_day", "pre-trend slope difference", "pp_per_day",
                                                         [t.value(pre, "/n_days", "pre-period days", "days")]) if pre_doc.get("slope_difference_pp_per_day") else None,
            pre_trend_p_value=t.maybe(pre, "/p_value", "pre-trend p-value", "p_value"),
            placebo_status=t.value(plc, "/status", "placebo check", "text"), placebo_n_evaluated=t.value(plc, "/n_evaluated", "placebo dates evaluated", "count"),
            assumptions=t.texts(did, "/assumptions", "assumption") + t.texts(imp, "/assumptions", "assumption"),
            attribution_label=wording.ATTRIBUTION_LABEL, claim=claim, matches_candidate_test=calls.matches_embedded,
        ))
    return out


def build_unexplained(t: Tracer, f: Facts, any_supported: bool) -> Unexplained | None:
    if f.decomposition is None:
        return None
    d, doc = f.decomposition, _ref(t.state, f.decomposition)
    loc = doc.get("localization")
    remaining = distinguishable = None
    if loc:
        for p, part in enumerate(doc["partitions"]):
            if part["name"] != loc["partition"]:
                continue
            for key in ("local_set", "explaining_set"):
                chosen = part.get(key)
                if chosen and chosen["segments"] == loc["segments"]:
                    remaining = t.value(d, f"/partitions/{p}/{key}/remaining_share_of_change", "remaining share of the change", "share")
                    break
            distinguishable = t.value(d, f"/partitions/{p}/rate_effect_not_distinguishable_pp", "rate effect not distinguishable from noise", "pp")
            break
    note = wording.UNEXPLAINED_NOTE if any_supported else wording.UNEXPLAINED_NOTE + " " + wording.UNEXPLAINED_NO_EVENT
    return Unexplained(remaining_share_of_change=remaining, rate_effect_not_distinguishable_pp=distinguishable,
                       explained_by_a_supported_event=any_supported, note=note)


def build_evidence(t: Tracer, f: Facts, stop: Stop | None) -> EvidenceSummary:
    def rule(reason: str) -> EvidenceSummary:
        return EvidenceSummary(strength="insufficient", base_strength=None, source_call=None, reason=reason, reasons=[], weakest_event=None)

    if stop is not None:
        return rule({"stopped_change_not_confirmed": wording.STRENGTH_STOPPED_NOT_CONFIRMED,
                     "stopped_insufficient_data": wording.STRENGTH_STOPPED_DATA}.get(stop.status, wording.STRENGTH_TOOL_ERROR))
    if f.testing is None:
        return rule(wording.STRENGTH_NO_EVENT_LOG)
    doc = _ref(t.state, f.testing)
    if doc["status"] == "not_localized":
        return rule(wording.STRENGTH_NOT_LOCALIZED)
    supported = [(i, c) for i, c in enumerate(doc["candidates"]) if c["verdict"] == "supported"]
    if not supported:
        return rule(wording.STRENGTH_NO_SUPPORTED)
    ranked = sorted(supported, key=lambda ic: STRENGTH_ORDER.index((ic[1].get("evidence") or {"strength": "insufficient"})["strength"]))
    i, cand = ranked[0]
    if not cand.get("evidence"):
        return rule(wording.STRENGTH_NO_SUPPORTED)
    b = f"/candidates/{i}"
    return EvidenceSummary(
        strength=cand["evidence"]["strength"], base_strength=cand["evidence"]["base_strength"], source_call=f.testing,
        reason=wording.STRENGTH_WEAKEST_SUPPORTED, reasons=t.texts(f.testing, f"{b}/evidence/reasons", "evidence reason"),
        weakest_event=t.value(f.testing, f"{b}/event", "event", "text"),
    )


def build_experiment(t: Tracer, f: Facts, stop: Stop | None) -> ExperimentRecommendation | None:
    if f.design is None:
        return None
    g, doc = f.design, _ref(t.state, f.design)
    plan = doc.get("plan")
    arms = []
    if plan:
        for i in range(len(plan["arms"])):
            arms.append(ExperimentArmPlan(
                name=t.value(g, f"/plan/arms/{i}/name", "arm", "text"), role=t.value(g, f"/plan/arms/{i}/role", "arm role", "text"),
                n_sessions=t.value(g, f"/plan/arms/{i}/n_sessions", "sessions in the arm", "sessions"),
                allocation_share=t.value(g, f"/plan/arms/{i}/allocation_share", "allocation share", "share"),
            ))
    guardrails: list[Traced] = []
    for i in range(len(doc["guardrails"])):
        guardrails += [t.value(g, f"/guardrails/{i}/name", "guardrail", "text"), t.value(g, f"/guardrails/{i}/detail", "guardrail detail", "text")]
    return ExperimentRecommendation(
        status=t.value(g, "/status", "design status", "text"), purpose=t.maybe(g, "/purpose", "purpose", "text"),
        hypothesis=t.maybe(g, "/hypothesis", "hypothesis", "text"), intervention=t.maybe(g, "/intervention", "intervention", "text"),
        randomization_unit=t.maybe(g, "/randomization_unit", "randomization unit", "text"), primary_metric=t.maybe(g, "/primary_metric", "primary metric", "text"),
        guardrails=guardrails, mde_pp=t.value(g, "/mde/mde_pp", "minimum detectable effect", "pp") if doc.get("mde") else None, arms=arms,
        n_per_arm_control=t.value(g, "/plan/power/n_control", "sessions per arm (control)", "sessions") if plan else None,
        duration_days=t.value(g, "/plan/duration_days", "expected duration", "days") if plan else None,
        achieved_power=t.value(g, "/plan/power/achieved_power", "achieved power under the normal approximation", "level") if plan else None,
        needs=t.texts(g, "/needs", "what is missing"), warnings=t.texts(g, "/warnings", "warning"),
        summary=t.value(g, "/summary", "design summary", "text"),
    )


def aggregate_assumptions(t: Tracer, f: Facts) -> list[Traced]:
    seen: set[str] = set()
    out: list[Traced] = []
    for call in (f.confirm, f.change_point, f.decomposition, f.testing, f.design):
        if call is None:
            continue
        for item in t.texts(call, "/assumptions", "assumption"):
            if item.value not in seen:
                seen.add(str(item.value))
                out.append(item)
    return out


def build_report(
    state: InvestigationState, f: Facts, stop: Stop | None, claims: list[ClaimEligibility],
    statements: list[RenderedStatement], rejected: list[str],
) -> InvestigationResult:
    t = Tracer(state)
    candidates = build_candidates(t, f)
    any_supported = any(c.verdict.value == "supported" for c in candidates)
    if stop is not None or f.testing is None:
        note = wording.NO_ESTIMATE_NOT_RUN
    else:
        note = None if any_supported else wording.NO_ESTIMATE_NO_SUPPORTED
    experiment = build_experiment(t, f, stop)
    if experiment is not None:
        experiment_note = None
    else:
        experiment_note = wording.NOT_RUN_STOPPED if stop is not None else wording.NOT_RUN_NO_CANDIDATE_TESTING
    return InvestigationResult(
        question=state.request.question, request=state.request, status=stop.status if stop else "completed",
        stopped_reason=stop.reason if stop else None, stopped_call=stop.call if stop else None,
        dataset=build_dataset(t, f), data_quality=build_quality(t, f), confirmed_change=build_confirm(t, f),
        change_point=build_change_point(t, f), localization=build_localization(t, f), candidates=candidates,
        alternatives_considered=t.texts(f.testing, "/alternatives", "alternative explanation") if f.testing else [],
        causal_estimates=build_estimates(t, f, claims) if f.testing else [], causal_estimate_note=note,
        unexplained=build_unexplained(t, f, any_supported), evidence=build_evidence(t, f, stop), recommended_experiment=experiment, experiment_note=experiment_note,
        assumptions=aggregate_assumptions(t, f), statements=statements, rejected_statements=rejected, stages=list(state.stages),
        n_calls=len(state.log),
    )
