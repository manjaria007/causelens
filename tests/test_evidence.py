"""Evidence-strength rules (Task 5.4): estimate and required checks first; ambiguity can only cap."""
from __future__ import annotations

from datetime import date
from itertools import product

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from causelens.analytics.confirm import Interval
from causelens.analytics.did import DidResult, PlaceboResult, PreTrendResult
from causelens.analytics.evidence import ORDER, assess_evidence

D = date(2026, 10, 14)


def did(lower=-0.9, upper=-0.4, valid=True, status="ok"):
    return DidResult(
        status=status, reason=None if status == "ok" else "too few days", event_date=D,
        effect_pp=None if status != "ok" else Interval(estimate=(lower + upper) / 2, lower=lower, upper=upper),
        std_error_pp=0.1, p_value=0.001, degrees_of_freedom=40.0, cov="hc1", weights="sessions", control_valid=valid,
        n_days_pre=30, n_days_post=14, n_obs=88, focal_sessions_post=1000, comparison_sessions_post=9000,
        excluded_dates=[], assumptions=[],
    )


def pre(status="pass"):
    return PreTrendResult(status=status, p_value=None if status == "not_evaluable" else 0.5, n_days=30, onset=D)


def plc(status="pass"):
    return PlaceboResult(status=status, placebos=[], n_attempted=3, n_evaluated=0 if status == "not_evaluable" else 3, onset=D)


def run(d=None, p="pass", b="pass", timing="pass", exposure=True, pattern=None, ambiguous=False):
    return assess_evidence(
        did=d if d is not None else did(), pre_trends=pre(p) if p else None, placebo=plc(b) if b else None,
        timing_assumptions=timing, exposure_consistent=exposure, pattern_consistent=pattern, attribution_ambiguous=ambiguous,
    )


def test_all_checks_pass_is_strong():
    r = run()
    assert r.strength == r.base_strength == "strong" and not r.capped_by_ambiguity


def test_ci_including_zero_is_insufficient_whatever_the_checks():
    r = run(d=did(-0.5, 0.2))
    assert r.strength == "insufficient" and "includes 0" in r.reasons[0]


def test_no_valid_control_is_insufficient():
    assert run(d=did(valid=False)).strength == "insufficient"
    assert run(d=did(status="insufficient")).strength == "insufficient"
    assert assess_evidence(did=None, pre_trends=pre(), placebo=plc(), timing_assumptions="pass", exposure_consistent=True,
                           pattern_consistent=None, attribution_ambiguous=False).strength == "insufficient"


def test_a_borderline_check_is_moderate():
    assert run(p="borderline").strength == "moderate"
    assert run(b="borderline").strength == "moderate"


@pytest.mark.parametrize("kwargs", [{"p": "fail"}, {"b": "fail"}, {"timing": "fail"}, {"exposure": False}, {"pattern": False},
                                    {"p": "not_evaluable"}, {"b": "not_evaluable"}, {"p": None}, {"b": None}, {"timing": "not_run"}])
def test_a_failed_or_unrun_required_check_is_weak(kwargs):
    assert run(**kwargs).strength == "weak"


def test_unrun_checks_never_count_as_passed():
    r = run(p="not_evaluable", b=None)
    assert r.strength == "weak"
    assert {c.name: c.outcome for c in r.checks}["pre-trends"] == "not_run"
    assert {c.name: c.outcome for c in r.checks}["placebo"] == "not_run"


def test_an_undeclared_pattern_is_not_a_required_check():
    r = run(pattern=None)
    assert "declared pattern" not in {c.name for c in r.checks}
    assert r.strength == "strong"


def test_ambiguity_caps_strong_at_moderate():
    r = run(ambiguous=True)
    assert (r.base_strength, r.strength, r.capped_by_ambiguity) == ("strong", "moderate", True)


@pytest.mark.parametrize("kwargs, expected", [({"p": "fail"}, "weak"), ({"d": did(-0.5, 0.2)}, "insufficient"), ({"p": "borderline"}, "moderate")])
def test_ambiguity_never_raises_a_lower_strength(kwargs, expected):
    r = run(ambiguous=True, **kwargs)
    assert r.strength == expected and not r.capped_by_ambiguity


def test_assessment_is_judged_from_the_estimate_and_checks_before_ambiguity():
    """Corrected rule 1: the base strength is identical with or without ambiguity."""
    for p, b in product(["pass", "borderline", "fail", "not_evaluable"], repeat=2):
        assert run(p=p, b=b).base_strength == run(p=p, b=b, ambiguous=True).base_strength


@settings(max_examples=150, deadline=None)
@given(
    lo=st.floats(-2, 1), width=st.floats(0.01, 2), valid=st.booleans(),
    p=st.sampled_from(["pass", "borderline", "fail", "not_evaluable", None]),
    b=st.sampled_from(["pass", "borderline", "fail", "not_evaluable", None]),
    timing=st.sampled_from(["pass", "fail", "not_run"]), exposure=st.sampled_from([True, False, None]),
    pattern=st.sampled_from([True, False, None]),
)
def test_property_ambiguity_only_ever_lowers_and_never_above_moderate(lo, width, valid, p, b, timing, exposure, pattern):
    d = did(lo, lo + width, valid=valid)
    kw = {"d": d, "p": p, "b": b, "timing": timing, "exposure": exposure, "pattern": pattern}
    plain, amb = run(**kw), run(ambiguous=True, **kw)
    assert ORDER[amb.strength] <= ORDER[plain.strength]
    assert ORDER[amb.strength] <= max(ORDER["moderate"], 0) or plain.strength in ("weak", "insufficient")
    if plain.strength in ("weak", "insufficient"):
        assert amb.strength == plain.strength
    assert amb.base_strength == plain.base_strength
    assert amb.strength != "strong"
