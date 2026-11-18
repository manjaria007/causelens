"""``test_candidates`` / ``test_event_scope``: verdicts for candidate explanations (Task 5.1, 5.4).

Reads as the design's "Candidate testing" section. In short:

* An event's logged ``scope`` is its **exposure** (who it could have touched), never a prediction of where
  conversion moves. A declared ``expected_pattern`` (default ``unspecified``) is tested directly.
* The estimand is the **differential effect** on the focal-minus-comparison conversion difference. Naming a
  release as its source is a separate claim and carries the lag it assumes (default 0: the effect begins on
  the event date) and the lags it was *not* tested at.
* ``rejected`` needs positive evidence against a named hypothesis: the localized change is outside the
  exposure scope, a declared pattern is contradicted, or a calibrated timing contrast rejects the event as
  the sole source. Onset-set exclusion alone blocks ``supported`` and gives ``inconclusive``.
* ``supported`` needs all of: onset date inside the onset set; a significant step in the direction of the
  observed change after Holm adjustment; timing diagnostics run, evaluable and not failed; pre-trend and
  placebo checks run, evaluable and not failed; no contradicted pattern; and the sole-source hypothesis not
  rejected. Rejection of the sole-source hypothesis takes precedence over step evidence. Unrun checks never
  count as passed. One surviving candidate is not enough.
* Release attribution is **ambiguous** when two candidates can give the same onset date at independently
  allowed lags (their step columns then coincide); strength is then capped at ``moderate``.

Pure and deterministic: no network, LLM or randomness.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date as Date
from datetime import timedelta
from typing import Literal

import numpy as np
import pandas as pd
from pydantic import BaseModel, ConfigDict, Field

from causelens.analytics.changepoint import (
    ChangePointResult,
    detect_change_point,
    match_event_candidates,
)
from causelens.analytics.confirm import Interval
from causelens.analytics.data_quality import (
    DataQualityReport,
    DetectorParams,
    check_data_quality,
)
from causelens.analytics.decompose import (
    DecompositionResult,
    day_level_test,
    decompose_change,
)
from causelens.analytics.did import (
    DidResult,
    ImpactResult,
    Panel,
    PlaceboResult,
    PreTrendResult,
    build_panel,
    did_on_panel,
    placebo_on_panel,
    pre_trends_on_panel,
    project_impact,
    segment_mask,
)
from causelens.analytics.evidence import EvidenceAssessment, assess_evidence
from causelens.analytics.loaders import parse_scope, validate_events, validate_metrics
from causelens.analytics.patterns import (
    NOT_DECLARED,
    ExpectedPattern,
    PatternCheck,
    check_pattern,
)
from causelens.analytics.series import ExclusionDisclosure
from causelens.analytics.timing import (
    AmbiguousPair,
    CandidateTiming,
    TimingAnalysis,
    analyze_timing,
    lag_ambiguity,
)

Verdict = Literal["supported", "rejected", "inconclusive"]
Window = tuple[Date | str, Date | str]


# --------------------------------------------------------------------------- result models


class FocalInfo(BaseModel):
    model_config = ConfigDict(frozen=True)

    segments: list[dict[str, str]]
    source: Literal["provided", "localization"]
    label: str
    change_pp: Interval | None = Field(None, description="Focal pooled change between the windows, day-level interval.")
    change_significant: bool
    comparison: str = "all other traffic"


class ExposureCheck(BaseModel):
    model_config = ConfigDict(frozen=True)

    scope: str
    status: Literal["all_visitors", "within", "partially_within", "disjoint"]
    rejects: bool
    detail: str


class LocationCheck(BaseModel):
    model_config = ConfigDict(frozen=True)

    onset_date: Date
    onset_set: list[Date]
    onset_set_available: bool
    in_onset_set: bool
    note: str


class DiDChecks(BaseModel):
    model_config = ConfigDict(frozen=True)

    pre_trends: PreTrendResult
    placebo: PlaceboResult


class LagOutcome(BaseModel):
    model_config = ConfigDict(frozen=True)

    lag_days: int
    onset_date: Date
    verdict: Verdict
    headline: str
    declared: bool


class Attribution(BaseModel):
    model_config = ConfigDict(frozen=True)

    ambiguous: bool
    pairs: list[AmbiguousPair]
    note: str


class CandidateVerdict(BaseModel):
    model_config = ConfigDict(frozen=True)

    event: str
    event_type: str
    event_date: Date
    lag_days: int
    onset_date: Date
    verdict: Verdict
    headline: str
    hypothesis: str
    reasons: list[str]
    exposure: ExposureCheck
    pattern: PatternCheck
    location: LocationCheck
    timing: CandidateTiming | None
    timing_status: str
    multiplicity: str
    did_checks: DiDChecks | None
    lag_sensitivity: list[LagOutcome]
    attribution: Attribution
    differential_effect: DidResult | None = None
    impact: ImpactResult | None = None
    evidence: EvidenceAssessment | None = None


class CandidateTestingResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    status: Literal["ok", "not_localized", "no_candidates"]
    focal: FocalInfo | None
    onset_set: list[Date]
    onset_set_note: str
    candidates: list[CandidateVerdict]
    timing: TimingAnalysis | None
    ambiguity: list[AmbiguousPair]
    leading: str | None = Field(None, description="The single supported candidate, if exactly one.")
    alternatives: list[str]
    exclusions: ExclusionDisclosure | None
    assumptions: list[str]
    summary: str


# --------------------------------------------------------------------------- helpers


def _label(seg: Mapping[str, str]) -> str:
    return ", ".join(f"{k}={v}" for k, v in seg.items())


def _parse_label(label: str) -> dict[str, str]:
    return dict(term.split("=", 1) for term in label.split(", "))


def _headline(name: str, verdict: Verdict, lag: int, ground: str | None = None) -> str:
    if verdict == "supported":
        if lag == 0:
            return f"{name}: supported under the immediate-effect assumption (lag 0)"
        return f"{name}: supported assuming the effect begins {lag} day(s) after the event (lag {lag})"
    if verdict == "rejected":
        if ground in (None, "timing"):
            if lag == 0:
                return f"{name}: rejected as the sole immediate explanation (lag 0)"
            return f"{name}: rejected as the sole explanation if the effect begins {lag} day(s) after the event (lag {lag})"
        return f"{name}: rejected, {ground} (lag {lag})"
    return f"{name}: inconclusive at lag {lag}" + (f" ({ground})" if ground else "")


def _hypothesis(name: str, event_date: Date, lag: int, onset: Date) -> str:
    when = f"on {onset}" if lag == 0 else f"on {onset} ({lag} day(s) after the event date {event_date})"
    return f"The whole localized change begins {when}, caused by {name} alone (no other logged event contributes)."


def _exposure(scope_text: str, focal: Sequence[Mapping[str, str]], data: pd.DataFrame, focal_significant: bool) -> ExposureCheck:
    scope = parse_scope(scope_text)
    if not scope:
        return ExposureCheck(scope=scope_text, status="all_visitors", rejects=False,
                             detail="the event's exposure scope is all visitors, which never rules it out by location")
    live = data[data["sessions"] > 0]
    shares: list[bool] = []
    for seg in focal:
        conflict = any(dim in seg and seg[dim] != val for dim, val in scope.items())
        merged = {**seg, **scope}
        shares.append((not conflict) and bool(segment_mask(live, [merged]).any()))
    if all(shares):
        return ExposureCheck(scope=scope_text, status="within", rejects=False, detail="the localized change lies within the event's exposure scope")
    if any(shares):
        return ExposureCheck(scope=scope_text, status="partially_within", rejects=False,
                             detail="part of the localized change lies within the event's exposure scope")
    if not focal_significant:
        return ExposureCheck(scope=scope_text, status="disjoint", rejects=False,
                             detail="the localized segment shares no visitors with the event, but its change is not itself distinguishable from noise, so this is not used to reject")
    return ExposureCheck(scope=scope_text, status="disjoint", rejects=True,
                         detail="the localized change is in visitors the event could not have touched (no shared traffic with its exposure scope)")


def _focal_change(panel: Panel, pre: tuple[Date, Date], post: tuple[Date, Date], level: float) -> Interval | None:
    d = panel.daily[panel.daily["usable"].to_numpy(dtype=bool)]

    def arrays(window: tuple[Date, Date]) -> tuple[np.ndarray, np.ndarray]:
        w = d[(d.index >= pd.Timestamp(window[0])) & (d.index <= pd.Timestamp(window[1]))]
        return w["s_f"].to_numpy(float), w["o_f"].to_numpy(float)

    (s0, o0), (s1, o1) = arrays(pre), arrays(post)
    if len(s0) < 3 or len(s1) < 3:
        return None
    return day_level_test(s0, o0, s1, o1, level=level).change_pp


def _onset_set(
    data: pd.DataFrame, events: pd.DataFrame, focal: Sequence[Mapping[str, str]], quality: DataQualityReport,
    apply_exclusions: bool, detector_params: DetectorParams | None,
) -> ChangePointResult:
    """Task 3's change point on the focal series. A union of segments is relabelled to a single segment."""
    if len(focal) == 1:
        return detect_change_point(data, events, segment=dict(focal[0]), quality=quality,
                                   apply_exclusions=apply_exclusions, detector_params=detector_params)
    frame = data.copy()
    mask = segment_mask(frame, focal)
    ids = pd.Series(frame.index.astype(str), index=frame.index)
    frame.loc[mask, ["device", "browser", "customer_type"]] = "focal"
    frame.loc[mask, "channel"] = "focal-" + ids[mask]
    return detect_change_point(frame, events, segment={"device": "focal"}, quality=quality,
                               apply_exclusions=apply_exclusions, detector_params=detector_params)


def _window(name: str, w: Window) -> tuple[Date, Date]:
    try:
        a, b = (pd.Timestamp(x).date() for x in w)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} window must be (start, end) dates: {exc}") from None
    if a > b:
        raise ValueError(f"{name} window starts ({a}) after it ends ({b})")
    return a, b


# --------------------------------------------------------------------------- main


def test_candidates(
    metrics: pd.DataFrame,
    events: pd.DataFrame,
    pre_window: Window,
    post_window: Window,
    *,
    focal: Mapping[str, str] | Sequence[Mapping[str, str]] | None = None,
    decomposition: DecompositionResult | None = None,
    candidates: Sequence[str] | None = None,
    expected_patterns: Mapping[str, ExpectedPattern] | None = None,
    lag_days: Mapping[str, int] | None = None,
    sensitivity_lags: Sequence[int] = (0, 1),
    alpha: float = 0.05,
    level: float = 0.95,
    quality: DataQualityReport | None = None,
    apply_exclusions: bool = True,
    detector_params: DetectorParams | None = None,
    lead_days: int = 3,
    did_cov: Literal["cluster", "hc1"] = "hc1",
) -> CandidateTestingResult:
    """Verdicts (supported / rejected / inconclusive) for every candidate event, with their evidence.

    The focal segment is the Task 4 localization unless ``focal`` is given. Candidates are Task 3's matches to
    the focal series' onset set unless ``candidates`` (event names) is given. ``lag_days`` sets each
    candidate's declared lag (default 0); every lag in ``sensitivity_lags`` is also evaluated, independently
    per candidate. Raises ``ValueError`` on invalid arguments; thin data gives ``inconclusive`` verdicts with
    the reason, never a partial result.
    """
    if not 1e-12 <= alpha <= 0.5 or not 0 < level < 1:
        raise ValueError("alpha must be in [1e-12, 0.5] and level in (0, 1)")
    sens = sorted({int(x) for x in sensitivity_lags})
    if not sens or sens[0] < 0:
        raise ValueError("sensitivity_lags must be non-negative integers")
    pre, post = _window("pre", pre_window), _window("post", post_window)
    if pre[1] >= post[0]:
        raise ValueError("the pre window must end before the post window starts")

    data = validate_metrics(metrics)
    ev = validate_events(events)
    if ev["name"].duplicated().any():
        raise ValueError("event names must be unique to be tested as candidates")
    quality = quality or check_data_quality(data, ev, detector_params)

    # ---- the focal segment ------------------------------------------------------------
    if focal is not None:
        segs = [dict(s) for s in ([focal] if isinstance(focal, Mapping) else focal)]
        source: Literal["provided", "localization"] = "provided"
        localized_significant: bool | None = None
    else:
        dec = decomposition or decompose_change(data, pre, post, ev, quality=quality, apply_exclusions=apply_exclusions)
        if dec.status != "ok" or dec.localization is None:
            why = (dec.localization_note if dec.status == "ok" else dec.summary) or "no localized segment"
            return CandidateTestingResult(
                status="not_localized", focal=None, onset_set=[], onset_set_note="not computed", candidates=[], timing=None,
                ambiguity=[], alternatives=[], exclusions=None,
                assumptions=["Without a localized segment there is no unaffected comparison, so a differential effect cannot be estimated."],
                summary=f"No candidate can be tested: {why} Consider a randomized experiment on the broad change.",
            )
        segs = [_parse_label(label) for label in dec.localization.segments]
        source, localized_significant = "localization", dec.localization.all_selected_significant

    panel = build_panel(data, segs, events=ev, quality=quality, apply_exclusions=apply_exclusions)
    change = _focal_change(panel, pre, post, level)
    significant = bool(change is not None and (change.lower > 0 or change.upper < 0))
    if localized_significant is not None:
        significant = significant and localized_significant
    focal_info = FocalInfo(segments=segs, source=source, label=" + ".join(_label(s) for s in segs), change_pp=change, change_significant=significant)

    # ---- onset set and candidates -------------------------------------------------------
    cp = _onset_set(data, ev, segs, quality, apply_exclusions, detector_params)
    onset_set = sorted(cp.plausible_dates) if cp.change_detected else []
    onset_note = (
        f"plausible onset dates {onset_set[0]} to {onset_set[-1]} from Task 3 on the focal series (an approximate "
        f"likelihood-ratio set that undercovers for small effects)"
        if onset_set else "no change point is supported on the focal series, so no onset date can be confirmed"
    )
    rows = ev.set_index("name")
    if candidates is None:
        matched = match_event_candidates(cp, ev, lead_days=lead_days)
        names = [c.name for c in matched.candidates]
    else:
        names = list(dict.fromkeys(candidates))
        missing = [n for n in names if n not in rows.index]
        if missing:
            raise ValueError(f"unknown candidate event(s): {missing}")
    names = sorted(names, key=lambda n: (rows.loc[n, "date"], n))
    if not names:
        return CandidateTestingResult(
            status="no_candidates", focal=focal_info, onset_set=onset_set, onset_set_note=onset_note, candidates=[], timing=None,
            ambiguity=[], alternatives=["an unlogged change", "a data-collection or tracking change (see the Task 2 flags)"],
            exclusions=panel.disclosure,
            assumptions=["The event log may be incomplete: a change with no logged event is not explained by this tool."],
            summary=f"No logged event falls in or shortly before the onset range ({onset_note}); the change is not attributed to any event.",
        )

    patterns = dict(expected_patterns or {})
    lags = {n: int((lag_days or {}).get(n, 0)) for n in names}
    for mapping_name, mp in (("expected_patterns", patterns), ("lag_days", dict(lag_days or {}))):
        unknown = [k for k in mp if k not in names]
        if unknown:
            raise ValueError(f"{mapping_name} names events that are not candidates: {unknown}")
    if any(v < 0 for v in lags.values()):
        raise ValueError("lag_days must be non-negative")
    edates: dict[str, Date] = {n: rows.loc[n, "date"].date() for n in names}

    usable = panel.usable_days
    y = panel.daily.loc[usable, "diff_pp"].to_numpy(float)
    exposure = {n: _exposure(str(rows.loc[n, "scope"]), segs, data, significant) for n in names}
    pattern = {
        n: check_pattern(patterns.get(n, ExpectedPattern()), data, usable, pre, post, alpha=alpha, level=level) if n in patterns else NOT_DECLARED
        for n in names
    }

    timing_cache: dict[tuple, TimingAnalysis] = {}
    checks_cache: dict[Date, DiDChecks] = {}
    did_cache: dict[Date, DidResult] = {}

    def timing_for(lagvec: Mapping[str, int]) -> TimingAnalysis:
        key = tuple(sorted(lagvec.items()))
        if key not in timing_cache:
            onsets = {n: edates[n] + timedelta(days=lagvec[n]) for n in names}
            timing_cache[key] = analyze_timing(usable, y, onsets, alpha=alpha, level=level)
        return timing_cache[key]

    def checks_for(onset: Date) -> DiDChecks:
        if onset not in checks_cache:
            checks_cache[onset] = DiDChecks(
                pre_trends=pre_trends_on_panel(panel, onset, cov=did_cov, level=level),
                placebo=placebo_on_panel(panel, onset, cov=did_cov, level=level),
            )
        return checks_cache[onset]

    def decide(name: str, lagvec: Mapping[str, int]) -> tuple[Verdict, list[str], str | None, TimingAnalysis, CandidateTiming, LocationCheck, DiDChecks]:
        lag = lagvec[name]
        onset = edates[name] + timedelta(days=lag)
        ta = timing_for(lagvec)
        ct = ta.candidates[name]
        in_set = onset in onset_set
        loc = LocationCheck(
            onset_date=onset, onset_set=onset_set, onset_set_available=bool(onset_set), in_onset_set=in_set,
            note=(f"onset {onset} is {'inside' if in_set else 'outside'} the plausible onset set" if onset_set else onset_note),
        )
        dc = checks_for(onset)
        rejections: list[tuple[str, str]] = []  # (ground, text)
        if exposure[name].rejects:
            rejections.append(("exposure", exposure[name].detail))
        if pattern[name].status == "contradicted":
            rejections.append(("pattern", pattern[name].detail))
        if ta.status == "ok" and ct.status == "ok" and ct.sole_source is not None and ct.sole_source.rejected:
            ss = ct.sole_source
            dof = f"{ss.df1}, {ss.df2}" if ss.statistic == "F" else f"{ss.df2}"
            rejections.append((
                "timing",
                (
                    f"the timing contrast rejects {name} as the sole source of the change ({ss.statistic} = {ss.value:.2f} "
                    f"on {dof} df, Holm-adjusted p = {ss.p_value_holm:.3g}); this does not mean it contributed nothing"
                ),
            ))
        if rejections:
            ground = rejections[0][0]
            label = {"exposure": "the localized change is outside its exposure scope", "pattern": "its declared pattern is contradicted", "timing": None}[ground]
            return "rejected", [t for _, t in rejections], label, ta, ct, loc, dc
        blockers: list[str] = []
        if not in_set:
            blockers.append(
                f"its onset date {onset} is outside the plausible onset set; this blocks 'supported' but does not reject it"
                if onset_set else f"no onset set is available ({onset_note})"
            )
        if ta.status != "ok":
            blockers.append(f"the timing contrast cannot be used: {ta.reason}")
        elif ct.status != "ok":
            blockers.append(f"its step is unidentifiable: {ct.unidentifiable_reason}")
        else:
            if not ct.step_significant:
                blockers.append(f"no significant step at its onset (Holm-adjusted p = {ct.step_p_holm:.3g}, alpha = {alpha:g})")
            elif ct.direction_matches is not True:
                blockers.append("its step is not in the direction of the observed change")
        if dc.pre_trends.status in ("fail", "not_evaluable"):
            blockers.append(f"the pre-trend check {'failed' if dc.pre_trends.status == 'fail' else 'could not be evaluated'}"
                            + (f" ({dc.pre_trends.reason})" if dc.pre_trends.reason else ""))
        if dc.placebo.status in ("fail", "not_evaluable"):
            blockers.append(f"the placebo check {'failed' if dc.placebo.status == 'fail' else 'could not be evaluated'}"
                            + (f" ({dc.placebo.reason})" if dc.placebo.reason else ""))
        if blockers:
            return "inconclusive", blockers, blockers[0].split(";")[0][:70], ta, ct, loc, dc
        notes = []
        if pattern[name].status == "untested":
            notes.append(pattern[name].detail)
        return "supported", [
            f"onset {onset} is inside the plausible onset set",
            f"significant step in the direction of the change (Holm-adjusted p = {ct.step_p_holm:.3g} over {len([c for c in ta.candidates.values() if c.status == 'ok'])} step test(s), alpha = {alpha:g})",
            "timing diagnostics ran and did not fail; pre-trend and placebo checks ran and did not fail",
            "the sole-source hypothesis was not rejected",
            *notes,
        ], None, ta, ct, loc, dc

    # ---- evaluate: declared lags, and each candidate's lag independently -------------------
    declared = dict(lags)
    main = {n: decide(n, declared) for n in names}
    sensitivity: dict[str, list[LagOutcome]] = {}
    for n in names:
        outs: list[LagOutcome] = []
        for lag in sorted({*sens, declared[n]}):
            vec = {**declared, n: lag}
            v, _, ground, *_ = decide(n, vec) if lag != declared[n] else main[n]
            outs.append(LagOutcome(lag_days=lag, onset_date=edates[n] + timedelta(days=lag), verdict=v,
                                   headline=_headline(n, v, lag, ground), declared=lag == declared[n]))
        sensitivity[n] = outs
    lag_sets = {n: sorted({*sens, declared[n]}) for n in names}
    pairs = lag_ambiguity(edates, onset_set, lag_sets)

    # ---- assemble ---------------------------------------------------------------------------
    verdicts: list[CandidateVerdict] = []
    for n in names:
        v, reasons, ground, ta, ct, loc, dc = main[n]
        mine = [p for p in pairs if n in (p.first, p.second)]
        attribution = Attribution(
            ambiguous=bool(mine), pairs=mine,
            note=(
                "release attribution is ambiguous: " + "; ".join(
                    f"{p.first} at lag {p.first_lag} and {p.second} at lag {p.second_lag} both begin on {p.onset_date}" for p in mine
                ) + "; their steps coincide, so the data cannot separate them"
                if mine else "no other candidate reaches the same onset date at an allowed lag"
            ),
        )
        did = impact = evidence = None
        if v == "supported":
            onset = loc.onset_date
            if onset not in did_cache:
                did_cache[onset] = did_on_panel(panel, onset, cov=did_cov, level=level)
            did = did_cache[onset]
            impact = project_impact(did, panel)
            evidence = assess_evidence(
                did=did, pre_trends=dc.pre_trends, placebo=dc.placebo,
                timing_assumptions="pass" if ta.status == "ok" else ("fail" if ta.status == "assumptions_failed" else "not_run"),
                exposure_consistent=None if exposure[n].status == "all_visitors" else (not exposure[n].rejects),
                pattern_consistent=(False if pattern[n].status == "contradicted" else (True if pattern[n].status == "not_contradicted" else None)),
                attribution_ambiguous=attribution.ambiguous,
            )
        verdicts.append(CandidateVerdict(
            event=n, event_type=str(rows.loc[n, "type"]), event_date=edates[n], lag_days=declared[n], onset_date=loc.onset_date,
            verdict=v, headline=_headline(n, v, declared[n], ground), hypothesis=_hypothesis(n, edates[n], declared[n], loc.onset_date),
            reasons=reasons, exposure=exposure[n], pattern=pattern[n], location=loc, timing=ct,
            timing_status=ta.status if ta.status != "ok" else "ok", multiplicity=ta.adjustment, did_checks=dc,
            lag_sensitivity=sensitivity[n], attribution=attribution, differential_effect=did, impact=impact, evidence=evidence,
        ))

    supported = [c for c in verdicts if c.verdict == "supported"]
    leading = supported[0].event if len(supported) == 1 else None
    alt: list[str] = []
    for c in verdicts:
        if leading is None or c.event != leading:
            alt.append(f"{c.event}: {c.verdict} (lag {c.lag_days})" + (f"; {c.reasons[0]}" if c.reasons else ""))
    alt += ["an unlogged concurrent change (the event log may be incomplete)",
            "a traffic-mix or data-collection change (see the Task 2 data-quality flags and exclusions)"]
    ta0 = timing_for(declared)
    assumptions = [
        "The estimand is the focal segment's differential effect; it equals the segment's own effect only if the comparison traffic was unaffected.",
        "Naming a release as the source assumes the effect begins at the evaluated lag; other lags were evaluated separately and are reported per candidate.",
        "The timing contrast assumes independent, homoskedastic day-level noise; the diagnostics screen for gross violations and do not validate it.",
        f"Multiplicity: Holm across the candidate tests at alpha = {alpha:g}.",
    ]
    if pairs:
        assumptions.append("Candidates that can reach the same onset date at allowed lags cannot be separated by these data; only an experiment can settle which one acted.")
    summary = _summary(verdicts, leading, focal_info, onset_note, ta0)
    return CandidateTestingResult(
        status="ok", focal=focal_info, onset_set=onset_set, onset_set_note=onset_note, candidates=verdicts, timing=ta0,
        ambiguity=pairs, leading=leading, alternatives=alt, exclusions=panel.disclosure, assumptions=assumptions, summary=summary,
    )


def _summary(verdicts: Sequence[CandidateVerdict], leading: str | None, focal: FocalInfo, onset_note: str, ta: TimingAnalysis) -> str:
    head = f"Localized change in {focal.label} ({onset_note}). "
    body = " ".join(c.headline + "." for c in verdicts)
    tail = ""
    if leading:
        c = next(v for v in verdicts if v.event == leading)
        if c.differential_effect and c.differential_effect.effect_pp and c.evidence:
            e = c.differential_effect.effect_pp
            tail = (
                f" Differential effect {e.estimate:+.3f} pp ({100 * e.level:g}% CI {e.lower:+.3f} to {e.upper:+.3f}, "
                f"{c.differential_effect.n_days_pre} days before and {c.differential_effect.n_days_post} after); evidence strength "
                f"{c.evidence.strength}: {c.evidence.reasons[-1]}. This estimate is a differential effect and assumes the comparison traffic was unaffected."
            )
        if c.attribution.ambiguous:
            tail += " Release attribution is ambiguous; a randomized rollback would settle it."
    elif sum(c.verdict == "supported" for c in verdicts) > 1:
        tail = " More than one candidate is supported, so no single explanation leads."
    elif all(c.verdict != "supported" for c in verdicts):
        tail = " No candidate is supported; the evidence cannot attribute the change to a logged event."
    return head + body + tail


def test_event_scope(
    event: str,
    metrics: pd.DataFrame,
    events: pd.DataFrame,
    pre_window: Window,
    post_window: Window,
    **kwargs,
) -> CandidateVerdict:
    """Verdict for one named event (the other candidates still enter the joint timing model as rivals)."""
    cands = kwargs.pop("candidates", None)
    result = test_candidates(metrics, events, pre_window, post_window, candidates=cands, **kwargs)
    for c in result.candidates:
        if c.event == event:
            return c
    raise ValueError(f"{event!r} is not a candidate for this change: {result.summary}")


# These public tools start with "test_" but are not pytest tests; keep test modules that import them from collecting them.
test_candidates.__test__ = False  # type: ignore[attr-defined]
test_event_scope.__test__ = False  # type: ignore[attr-defined]
