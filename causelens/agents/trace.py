"""Numbers that trace to a logged tool result, and the checks on the sentences a reasoner writes (Requirement 8.2, 8.4).

Two mechanisms.

* ``Traced`` and ``TracedInterval`` hold a value together with the log entry and JSON pointer it was read from. They are
  built only by a ``Tracer``, which reads the value out of the log; ``verify_traces`` reads it again and compares.
* A ``Statement`` is a sentence template whose placeholders are bound to ``Ref`` entries (call, pointer, display format).
  The template text may not contain a digit and may not assert a cause, so a number can only reach a sentence through a
  reference into the log. Formatting is fixed code (``FORMATS``): it chooses decimals, never a value.
"""
from __future__ import annotations

import re
import string
from collections.abc import Iterator, Sequence
from datetime import date
from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictFloat,
    StrictInt,
    StrictStr,
)

from causelens.agents.claims import (
    ClaimEligibility,
    ref_belongs_to_claim,
    required_refs,
)
from causelens.agents.state import InvestigationState

Scalar = StrictBool | StrictInt | StrictFloat | StrictStr
Unit = Literal["pp", "pp_per_day", "fraction", "share", "count", "sessions", "days", "orders", "level", "p_value", "sd", "date", "text", "flag"]


class TraceError(ValueError):
    """A reference does not resolve in the log, or a sentence breaks a numeric or wording rule."""


def pointer_get(document: Any, pointer: str) -> Any:
    """RFC 6901 JSON pointer lookup. ``""`` is the whole document."""
    if pointer == "":
        return document
    if not pointer.startswith("/"):
        raise TraceError(f"a pointer starts with '/', got {pointer!r}")
    node = document
    for raw in pointer[1:].split("/"):
        part = raw.replace("~1", "/").replace("~0", "~")
        try:
            node = node[int(part)] if isinstance(node, list) else node[part]
        except (KeyError, IndexError, ValueError, TypeError):
            raise TraceError(f"{pointer!r} does not resolve (stopped at {part!r})") from None
    return node


def _resolve(state: InvestigationState, call: int, pointer: str) -> Any:
    try:
        entry = state.call(call)
    except KeyError as exc:
        raise TraceError(str(exc)) from None
    if entry.is_error or entry.result is None:
        raise TraceError(f"call {call} ({entry.tool}) has no result")
    return pointer_get(entry.result, pointer)


class Traced(BaseModel):
    """One value read from ``call`` (a log position) at ``path``."""

    model_config = ConfigDict(frozen=True)

    label: str
    value: Scalar
    unit: Unit
    call: int
    path: str


class TracedInterval(BaseModel):
    """An estimate with its interval, read from one object ``{estimate, lower, upper, level}``, and the sample sizes behind it."""

    model_config = ConfigDict(frozen=True)

    label: str
    estimate: StrictFloat
    lower: StrictFloat
    upper: StrictFloat
    level: StrictFloat
    unit: Unit
    call: int
    path: str
    n: list[Traced] = Field(min_length=1, description="The sample sizes the estimate rests on (days, sessions, observations).")


class TracedBand(TracedInterval):
    """An interval whose sample size the tool does not report (for example each placebo date). ``n`` may be empty."""

    n: list[Traced] = Field(default_factory=list)


class Tracer:
    """Builds ``Traced`` values by reading the log. The only way the report obtains a number."""

    def __init__(self, state: InvestigationState):
        self.state = state

    def value(self, call: int, path: str, label: str, unit: Unit) -> Traced:
        found = _resolve(self.state, call, path)
        if isinstance(found, date):
            found = found.isoformat()
        if not isinstance(found, bool | int | float | str):
            raise TraceError(f"call {call} {path!r} is {type(found).__name__}, not a scalar")
        return Traced(label=label, value=found, unit=unit, call=call, path=path)

    def maybe(self, call: int, path: str, label: str, unit: Unit) -> Traced | None:
        """``None`` when the tool left the field null (an absent value is not a number)."""
        return None if _resolve(self.state, call, path) is None else self.value(call, path, label, unit)

    def interval(self, call: int, path: str, label: str, unit: Unit, n: list[Traced]) -> TracedInterval:
        found = _resolve(self.state, call, path)
        if not isinstance(found, dict) or not {"estimate", "lower", "upper", "level"} <= found.keys():
            raise TraceError(f"call {call} {path!r} is not an interval")
        return TracedInterval(label=label, call=call, path=path, unit=unit, n=n, **{k: float(found[k]) for k in ("estimate", "lower", "upper", "level")})

    def band(self, call: int, path: str, label: str, unit: Unit) -> TracedBand:
        """Like ``interval`` for an estimate the tool reports without a sample size. Say so wherever it is shown."""
        found = _resolve(self.state, call, path)
        if not isinstance(found, dict) or not {"estimate", "lower", "upper", "level"} <= found.keys():
            raise TraceError(f"call {call} {path!r} is not an interval")
        return TracedBand(label=label, call=call, path=path, unit=unit, **{k: float(found[k]) for k in ("estimate", "lower", "upper", "level")})

    def maybe_interval(self, call: int, path: str, label: str, unit: Unit, n: list[Traced]) -> TracedInterval | None:
        return None if _resolve(self.state, call, path) is None else self.interval(call, path, label, unit, n)

    def texts(self, call: int, path: str, label: str) -> list[Traced]:
        """Every string in the list at ``path`` (assumptions, reasons, warnings), each with its own pointer."""
        found = _resolve(self.state, call, path)
        if not isinstance(found, list):
            raise TraceError(f"call {call} {path!r} is not a list")
        return [self.value(call, f"{path}/{i}", label, "text") for i in range(len(found))]


def iter_traced(model: Any) -> Iterator[Traced | TracedInterval]:
    """Every ``Traced`` and ``TracedInterval`` inside a pydantic model, list or dict, depth first."""
    if isinstance(model, Traced | TracedInterval):
        yield model
        if isinstance(model, TracedInterval):
            yield from model.n
    elif isinstance(model, BaseModel):
        for name in type(model).model_fields:
            yield from iter_traced(getattr(model, name))
    elif isinstance(model, list | tuple):
        for item in model:
            yield from iter_traced(item)
    elif isinstance(model, dict):
        for item in model.values():
            yield from iter_traced(item)


def verify_traces(model: Any, state: InvestigationState) -> int:
    """Re-read every traced value from the log and require equality. Returns how many were checked."""
    checked = 0
    for item in iter_traced(model):
        found = _resolve(state, item.call, item.path)
        if isinstance(item, Traced):
            if isinstance(found, date):
                found = found.isoformat()
            if type(found) is not type(item.value) or found != item.value:
                raise TraceError(f"{item.label}: the report has {item.value!r} but call {item.call} {item.path} is {found!r}")
        else:
            for key in ("estimate", "lower", "upper", "level"):
                if float(found[key]) != getattr(item, key):
                    raise TraceError(f"{item.label}: {key} is {getattr(item, key)!r} in the report but {found[key]!r} in call {item.call}")
        checked += 1
    return checked


# --------------------------------------------------------------------------- sentences

Format = Literal["text", "date", "count", "pp", "pct", "level", "p"]

#: Display formats. They choose decimals only; the stored value is never altered.
FORMATS: dict[str, Any] = {
    "text": lambda v: str(v),
    "date": lambda v: str(v),
    "count": lambda v: f"{int(v):,}",
    "pp": lambda v: f"{float(v):+.3f} pp",
    "pct": lambda v: f"{float(v) * 100:.2f}%",
    "level": lambda v: f"{float(v) * 100:.0f}%",
    "p": lambda v: f"{float(v):.4g}",
}

#: Words that assert a cause. Allowed only in a ``causal_claim`` statement that passes ``validate_statement``.
CAUSAL_WORDS = re.compile(
    r"\b(cause|causes|caused|causing|because|due to|led to|leads to|lead to|resulted in|results in|result in|responsible|drove|driven by|triggered|"
    r"is why|are why|was why|explains why)\b",
    re.IGNORECASE,
)


class Ref(BaseModel):
    model_config = ConfigDict(frozen=True)

    call: int
    path: str
    fmt: Format = "text"


class Statement(BaseModel):
    """A sentence template with every number bound to a log reference."""

    model_config = ConfigDict(frozen=True)

    role: str
    kind: Literal["finding", "caveat", "recommendation", "causal_claim"]
    text: str = Field(description="Template. Placeholders are {name}; the text has no digits.")
    refs: dict[str, Ref]


class RenderedStatement(BaseModel):
    model_config = ConfigDict(frozen=True)

    role: str
    kind: str
    text: str
    statement: Statement


def _parse(text: str) -> list[tuple[str, str | None, str | None, str | None]]:
    try:
        return list(string.Formatter().parse(text))
    except ValueError as exc:  # an unmatched brace
        raise TraceError(f"the template is malformed: {exc}") from None


def _placeholders(text: str) -> list[str]:
    names: list[str] = []
    for _, field, spec, conversion in _parse(text):
        if field is None:
            continue
        if not field.isidentifier() or spec or conversion:
            raise TraceError(f"placeholder {{{field}}} must be a plain name")
        names.append(field)
    return names


def check_prose(text: str) -> None:
    """The rules for text a reasoner writes outside a statement (a parameter rationale): no digits, no cause-asserting words."""
    if re.search(r"\d", text):
        raise TraceError("prose may not contain digits; numbers come only from references into the log")
    match = CAUSAL_WORDS.search(text)
    if match:
        raise TraceError(f"wording asserts a cause: {match.group(0)!r}")


def event_names(state: InvestigationState) -> set[str]:
    """Every event name the log mentions. A causal sentence must take its event from a reference, never type it."""
    names: set[str] = set()
    for call in state.log:
        if call.is_error or call.result is None:
            continue
        for key in ("candidates",):
            for item in call.result.get(key, []) if isinstance(call.result.get(key), list) else []:
                if isinstance(item, dict):
                    names.update(str(item[k]) for k in ("event", "name") if k in item)
    return {n for n in names if n}


def validate_statement(statement: Statement, state: InvestigationState, *, allowed_claims: Sequence[ClaimEligibility] = ()) -> None:
    """Raise ``TraceError`` unless the statement obeys the numeric and wording rules.

    ``allowed_claims`` are the claims ``assess_claim`` allowed (see ``causelens.agents.claims``). A ``causal_claim`` sentence is
    bound to exactly one of them: it must quote that claim's event, lag, onset, estimate, interval, level, sample size and every
    assumption, name the design, quote nothing about another candidate, and not type an event name.
    """
    names = _placeholders(statement.text)
    if set(names) != set(statement.refs):
        raise TraceError(f"placeholders {sorted(set(names))} do not match references {sorted(statement.refs)}")
    literal = "".join(piece for piece, *_ in _parse(statement.text))
    if re.search(r"\d", literal):
        raise TraceError("a sentence template may not contain digits; bind the number to a reference into the log")
    for name, ref in statement.refs.items():
        value = _resolve(state, ref.call, ref.path)
        if isinstance(value, dict | list) or value is None:
            raise TraceError(f"{{{name}}} resolves to {type(value).__name__}, not a value")
        if ref.fmt not in ("text", "date") and not (isinstance(value, int | float) and not isinstance(value, bool)):
            raise TraceError(f"{{{name}}} uses format {ref.fmt!r} but call {ref.call} {ref.path} is not a number")
    asserts_cause = CAUSAL_WORDS.search(literal)
    if statement.kind != "causal_claim":
        if asserts_cause:
            raise TraceError(f"wording asserts a cause outside a causal_claim: {asserts_cause.group(0)!r}")
        return
    _validate_claim(statement, state, literal, allowed_claims)


def _validate_claim(statement: Statement, state: InvestigationState, literal: str, allowed: Sequence[ClaimEligibility]) -> None:
    cited = {ref.call for ref in statement.refs.values()}
    claims = [c for c in allowed if c.did_call in cited]
    if len(claims) != 1:
        raise TraceError("a causal_claim must cite exactly one difference-in-differences result whose checks passed")
    claim = claims[0]
    if "difference-in-differences" not in literal.lower():
        raise TraceError("a causal_claim must name its design (difference-in-differences)")
    typed = [n for n in event_names(state) if n.lower() in literal.lower()]
    if typed:
        raise TraceError(f"a causal_claim may not type an event name ({typed[0]!r}); the event comes from a reference")
    quoted = {(r.call, r.path, r.fmt) for r in statement.refs.values()}
    missing = [name for name, want in required_refs(state, claim).items() if want not in quoted]
    if missing:
        raise TraceError(f"the statement is not bound to the claim for {claim.event!r}: it does not quote {sorted(missing)}")
    foreign = [r.path for r in statement.refs.values() if not ref_belongs_to_claim(claim, r.call, r.path)]
    if foreign:
        raise TraceError(f"the statement is not bound to the event of its estimate ({claim.event!r}); it quotes {foreign[0]!r}")


def render(statement: Statement, state: InvestigationState) -> str:
    """The sentence with each placeholder replaced by its formatted logged value."""
    values: dict[str, str] = {}
    for name, ref in statement.refs.items():
        value = _resolve(state, ref.call, ref.path)
        values[name] = FORMATS[ref.fmt](value)
    try:
        return statement.text.format_map(values)
    except (ValueError, KeyError, IndexError) as exc:
        raise TraceError(f"the template cannot be rendered: {exc}") from None
