"""Tracing and wording rules (Requirement 8.2, 8.4), on a hand-built log. No server needed."""
from __future__ import annotations

import re

import pytest
from agents_support import request
from hypothesis import given
from hypothesis import strategies as st

from causelens.agents import wording
from causelens.agents.claims import ClaimEligibility, required_refs
from causelens.agents.state import InvestigationState, LoggedCall
from causelens.agents.trace import (
    CAUSAL_WORDS,
    Ref,
    Statement,
    Traced,
    TracedInterval,
    TraceError,
    Tracer,
    pointer_get,
    render,
    validate_statement,
    verify_traces,
)


def state_with(result: dict, tool: str = "confirm_change") -> InvestigationState:
    state = InvestigationState(request=request())
    state.log.append(LoggedCall(seq=1, role="Detective", stage="confirm", tool=tool, arguments={}, result=result))
    return state


RESULT = {
    "verdict": "real_change",
    "abs_change_pp": {"estimate": -0.25, "lower": -0.5, "upper": -0.1, "level": 0.95},
    "pre": {"n_days_used": 70, "sessions": 1000},
    "assumptions": ["Days are independent.", "The comparison was unaffected."],
    "nothing": None,
}


def stmt(text: str, kind: str = "finding", **refs: Ref) -> Statement:
    return Statement(role="Detective", kind=kind, text=text, refs=refs)  # type: ignore[arg-type]


# --------------------------------------------------------------------------- pointers

json_values = st.recursive(
    st.one_of(st.integers(-5, 5), st.floats(-5, 5, allow_nan=False), st.text(max_size=4), st.booleans()),
    lambda inner: st.one_of(st.lists(inner, min_size=1, max_size=3), st.dictionaries(st.text("ab/~", min_size=1, max_size=3), inner, min_size=1, max_size=3)),
    max_leaves=12,
)


def paths(doc, prefix=()):
    yield prefix, doc
    if isinstance(doc, dict):
        for k, v in doc.items():
            yield from paths(v, (*prefix, k))
    elif isinstance(doc, list):
        for i, v in enumerate(doc):
            yield from paths(v, (*prefix, i))


@given(json_values)
def test_every_path_in_a_document_resolves_to_its_value(doc):
    for path, value in paths(doc):
        pointer = "".join("/" + str(p).replace("~", "~0").replace("/", "~1") for p in path)
        assert pointer_get(doc, pointer) == value


def test_pointer_errors_are_trace_errors():
    for bad in ("x", "/missing", "/abs_change_pp/nope", "/assumptions/9", "/assumptions/x"):
        with pytest.raises(TraceError):
            pointer_get(RESULT, bad)


# --------------------------------------------------------------------------- traced values

def test_tracer_reads_values_and_intervals_from_the_log():
    state = state_with(RESULT)
    t = Tracer(state)
    n = [t.value(1, "/pre/n_days_used", "days", "days")]
    interval = t.interval(1, "/abs_change_pp", "change", "pp", n)
    assert (interval.estimate, interval.lower, interval.upper, interval.level) == (-0.25, -0.5, -0.1, 0.95)
    assert t.maybe(1, "/nothing", "x", "text") is None
    assert [x.value for x in t.texts(1, "/assumptions", "assumption")] == RESULT["assumptions"]
    assert verify_traces([interval, n], state) == 3  # the interval, its sample size, and the sample size listed again


def test_tracer_refuses_what_is_not_in_the_log():
    t = Tracer(state_with(RESULT))
    for call, path in ((1, "/pre"), (1, "/absent"), (2, "/verdict")):
        with pytest.raises(TraceError):
            t.value(call, path, "x", "text")
    with pytest.raises(TraceError):
        t.interval(1, "/pre", "x", "pp", [])


def test_a_changed_number_fails_verification():
    state = state_with(RESULT)
    t = Tracer(state)
    good = t.value(1, "/pre/sessions", "sessions", "sessions")
    verify_traces(good, state)
    forged = Traced(label="sessions", value=1001, unit="sessions", call=1, path="/pre/sessions")
    with pytest.raises(TraceError, match="1001"):
        verify_traces(forged, state)
    wrong_type = Traced(label="sessions", value=1000.0, unit="sessions", call=1, path="/pre/sessions")
    with pytest.raises(TraceError):
        verify_traces(wrong_type, state)
    interval = TracedInterval(label="x", estimate=-0.25, lower=-0.6, upper=-0.1, level=0.95, unit="pp", call=1, path="/abs_change_pp", n=[good])
    with pytest.raises(TraceError, match="lower"):
        verify_traces(interval, state)


@given(st.floats(-1e6, 1e6, allow_nan=False, allow_infinity=False), st.integers(0, 10**9))
def test_values_round_trip_exactly(x, n):
    state = state_with({"x": x, "n": n})
    t = Tracer(state)
    assert t.value(1, "/x", "x", "pp").value == x
    assert t.value(1, "/n", "n", "count").value == n
    assert verify_traces([t.value(1, "/x", "x", "pp"), t.value(1, "/n", "n", "count")], state) == 2


# --------------------------------------------------------------------------- statements

def test_a_statement_renders_logged_values_only():
    state = state_with(RESULT)
    s = stmt("Conversion moved by {c} ({v}).", c=Ref(call=1, path="/abs_change_pp/estimate", fmt="pp"), v=Ref(call=1, path="/verdict"))
    validate_statement(s, state)
    assert render(s, state) == "Conversion moved by -0.250 pp (real_change)."


@given(st.text(alphabet=st.characters(blacklist_characters="{}", blacklist_categories=("Cs",)), max_size=30), st.sampled_from("0123456789"))
def test_any_digit_in_the_template_text_is_rejected(prefix, digit):
    state = state_with(RESULT)
    with pytest.raises(TraceError, match="digit"):
        validate_statement(stmt(f"{prefix}{digit} rest"), state)


@given(st.text(alphabet=st.characters(blacklist_characters="{}0123456789", blacklist_categories=("Cs", "Nd", "Nl", "No")), max_size=40))
def test_digit_free_text_without_cause_words_is_accepted(text):
    if CAUSAL_WORDS.search(text):
        return
    validate_statement(stmt(text), state_with(RESULT))


@pytest.mark.parametrize("text", [
    "The release caused the drop.", "Conversion fell because of the release.", "The drop is due to the hero refresh.",
    "This led to fewer orders.", "It resulted in a loss.", "The release is responsible.", "The outage drove the fall.",
])
def test_cause_asserting_words_are_rejected_outside_a_causal_claim(text):
    with pytest.raises(TraceError, match="asserts a cause"):
        validate_statement(stmt(text), state_with(RESULT))


@pytest.mark.parametrize("text", [
    "The drop coincides with the release.", "The change is concentrated in mobile Safari.", "This is consistent with a release effect.",
])
def test_the_permitted_vocabulary_passes(text):
    validate_statement(stmt(text), state_with(RESULT))


def test_placeholders_and_references_must_match_and_resolve():
    state = state_with(RESULT)
    ref = Ref(call=1, path="/verdict")
    with pytest.raises(TraceError, match="do not match"):
        validate_statement(stmt("Says {a}.", b=ref), state)
    with pytest.raises(TraceError, match="do not match"):
        validate_statement(stmt("Says {a}.", a=ref, b=ref), state)
    with pytest.raises(TraceError, match="plain name"):
        validate_statement(stmt("Says {a:.2f}.", a=ref), state)
    with pytest.raises(TraceError):
        validate_statement(stmt("Says {a}.", a=Ref(call=1, path="/absent")), state)
    with pytest.raises(TraceError):
        validate_statement(stmt("Says {a}.", a=Ref(call=7, path="/verdict")), state)
    with pytest.raises(TraceError, match="not a value"):
        validate_statement(stmt("Says {a}.", a=Ref(call=1, path="/pre")), state)
    with pytest.raises(TraceError, match="not a number"):
        validate_statement(stmt("Says {a}.", a=Ref(call=1, path="/verdict", fmt="pp")), state)


def claim_state() -> tuple[InvestigationState, ClaimEligibility]:
    """A log with two candidates in call 1, a DiD in call 2 and its two checks in calls 3 and 4. Candidate 1 is the supported one."""
    state = InvestigationState(request=request())
    candidates = [
        {"event": "Hero", "onset_date": "2026-10-13", "lag_days": 0, "verdict": "rejected"},
        {"event": "Checkout", "onset_date": "2026-10-14", "lag_days": 0, "verdict": "supported"},
    ]
    state.log.append(LoggedCall(seq=1, role="Scientist", stage="test", tool="test_candidates", arguments={},
                                result={"candidates": candidates, "focal": {"label": "mobile safari"}}))
    did = {"effect_pp": {"estimate": -0.68, "lower": -0.77, "upper": -0.58, "level": 0.95}, "n_obs": 182,
           "assumptions": ["Parallel trends hold.", "The comparison was unaffected."]}
    state.log.append(LoggedCall(seq=2, role="Scientist", stage="estimate", tool="estimate_did", arguments={}, result=did))
    for seq, tool in ((3, "check_pre_trends"), (4, "run_placebo")):
        state.log.append(LoggedCall(seq=seq, role="Scientist", stage="estimate", tool=tool, arguments={}, result={"status": "pass"}))
    claim = ClaimEligibility(event="Checkout", allowed=True, reason="ok", did_call=2, pre_trend_call=3, placebo_call=4,
                             verdict_call=1, verdict_path="/candidates/1/verdict")
    return state, claim


def claim_statement(state, claim, *, index: int = 1, text: str | None = None, drop: str | None = None, **override) -> Statement:
    refs = {name: Ref(call=c, path=p, fmt=f) for name, (c, p, f) in required_refs(state, claim).items()}
    refs["event"] = Ref(call=1, path=f"/candidates/{index}/event", fmt="text")
    refs.update(override)
    body = text or ("By difference-in-differences, the change attributed to {event} (lag {lag}, onset {onset}) is {effect} "
                    "({lower} to {upper} at {level}, {n} observations), if: {a0} {a1}")
    if drop:
        refs.pop(drop)
        body = body.replace("{" + drop + "}", "something")
    return Statement(role="Scientist", kind="causal_claim", text=body, refs=refs)


def test_a_causal_claim_is_bound_to_one_eligible_claim():
    state, claim = claim_state()
    validate_statement(claim_statement(state, claim), state, allowed_claims=[claim])
    with pytest.raises(TraceError, match="exactly one"):
        validate_statement(claim_statement(state, claim), state)  # nothing is eligible
    blocked = claim.model_copy(update={"allowed": False})
    assert not blocked.allowed


@pytest.mark.parametrize("drop", ["event", "onset", "lag", "effect", "lower", "upper", "level", "n", "a0", "a1"])
def test_a_causal_claim_must_quote_every_part_of_its_claim(drop):
    state, claim = claim_state()
    with pytest.raises(TraceError, match="not bound to the claim"):
        validate_statement(claim_statement(state, claim, drop=drop), state, allowed_claims=[claim])


def test_a_causal_claim_cannot_name_another_candidate_even_through_a_reference():
    state, claim = claim_state()
    with pytest.raises(TraceError, match="not bound"):
        validate_statement(claim_statement(state, claim, index=0), state, allowed_claims=[claim])
    extra = {"other": Ref(call=1, path="/candidates/0/verdict")}
    wrapped = claim_statement(state, claim, text="By difference-in-differences {event} {lag} {onset} {effect} {lower} {upper} {level} {n} {a0} {a1} {other}", **extra)
    with pytest.raises(TraceError, match="not bound"):
        validate_statement(wrapped, state, allowed_claims=[claim])


def test_a_causal_claim_cannot_type_an_event_name_or_skip_the_design():
    state, claim = claim_state()
    typed = claim_statement(state, claim, text="By difference-in-differences, Hero is {effect} ({lower} {upper} {level} {n}) {lag} {onset} {a0} {a1} {event}")
    with pytest.raises(TraceError, match="type an event name"):
        validate_statement(typed, state, allowed_claims=[claim])
    unnamed = claim_statement(state, claim, text="The change attributed to {event} (lag {lag}, onset {onset}) is {effect} ({lower} {upper} {level} {n}) {a0} {a1}")
    with pytest.raises(TraceError, match="name its design"):
        validate_statement(unnamed, state, allowed_claims=[claim])


def test_a_causal_claim_cannot_reformat_a_figure():
    state, claim = claim_state()
    wrong = claim_statement(state, claim, effect=Ref(call=2, path="/effect_pp/estimate", fmt="count"))  # would show the estimate as a whole number
    with pytest.raises(TraceError, match="not bound to the claim"):
        validate_statement(wrong, state, allowed_claims=[claim])


@pytest.mark.parametrize("text", ["The effect is {effect", "The effect is effect}", "The effect is {}", "The effect is {0}", "{{effect}"])
def test_a_malformed_template_is_a_trace_error_not_a_crash(text):
    state = state_with(RESULT)
    s = stmt(text, effect=Ref(call=1, path="/verdict"))
    with pytest.raises(TraceError):
        validate_statement(s, state)
    with pytest.raises(TraceError):
        render(s, state)


def test_the_orchestrators_own_phrases_contain_no_digits():
    phrases = {k: v for k, v in vars(wording).items() if k.isupper() and isinstance(v, str)}
    assert phrases
    for name, text in phrases.items():
        assert not re.search(r"\d", text), name
        assert not CAUSAL_WORDS.search(text), name
