"""The LLM layer (design, overview): choose parameters, interpret tool results, write the report text.

A ``Reasoner`` has two methods. ``choose_parameters`` may set the few tunable parameters of a tool (``parameters.TUNABLE``);
``interpret`` returns ``Statement`` objects for a role. Neither can compute: the only route from a tool result to a sentence is a
``Ref`` into the log, and the orchestrator validates every statement (no digits, every reference resolves, no cause-asserting words
outside a claim bound to an eligible difference-in-differences result) and every parameter choice before using it. What fails is
dropped and recorded; it is never repaired.

The orchestrator calls ``choose_parameters`` before each tunable tool call and ``interpret`` right after each role's stages,
so a model sees the results of earlier roles before the next one runs.

* ``TemplateReasoner`` is deterministic and offline (the demo needs no model): fixed sentence templates, and the tools' defaults.
* ``ModelReasoner`` drives any text model through the ``TextModel`` protocol. It builds the prompt from ``Role.system_prompt()`` (which
  embeds ``scientist-rules.md`` verbatim) and a digest of the logged results, asks for JSON, and parses it. Its output is untrusted
  and passes through the same validation. No provider adapter ships, so no hosted model was run in this repository's tests.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from causelens.agents.claims import ClaimEligibility, required_refs
from causelens.agents.parameters import TUNABLE, ParameterChoice, describe
from causelens.agents.roles import Role
from causelens.agents.state import InvestigationState, LoggedCall
from causelens.agents.trace import FORMATS, Ref, Statement


@dataclass(frozen=True)
class Context:
    """What a reasoner may read: the log so far and the causal claims the checks allow (``allowed`` is each one's verdict)."""

    state: InvestigationState
    claims: tuple[ClaimEligibility, ...]


class Reasoner(Protocol):
    def choose_parameters(self, role: Role, tool: str, context: Context) -> ParameterChoice | None: ...

    def interpret(self, role: Role, context: Context) -> list[Statement]: ...


def _ref(call: LoggedCall, path: str, fmt: str = "text") -> Ref:
    return Ref(call=call.seq, path=path, fmt=fmt)  # type: ignore[arg-type]


class TemplateReasoner:
    """Fixed sentence templates over the logged results, and the tools' own parameter defaults. Deterministic; no model."""

    def choose_parameters(self, role: Role, tool: str, context: Context) -> ParameterChoice | None:
        return None

    def interpret(self, role: Role, context: Context) -> list[Statement]:
        build = {"Detective": self._detective, "Scientist": self._scientist, "Experiment designer": self._designer}[role.name]
        return build(role, context)

    # ------------------------------------------------------------------ Detective

    def _detective(self, role: Role, context: Context) -> list[Statement]:
        state, out = context.state, []
        quality = state.last("check_data_quality")
        if quality and quality.result is not None:
            dates = quality.result["excluded_dates"]
            if dates:
                refs = {f"d{i}": _ref(quality, f"/excluded_dates/{i}", "date") for i in range(len(dates))}
                names = ", ".join("{" + k + "}" for k in refs)
                out.append(Statement(role=role.name, kind="caveat", refs=refs,
                                     text=f"Possible tracking issues were flagged and these days are excluded from the estimates: {names}."))
            else:
                out.append(Statement(role=role.name, kind="finding", refs={}, text="No day was flagged as a possible tracking issue."))
        confirm = state.last("confirm_change")
        if confirm and confirm.result and confirm.result.get("abs_change_pp"):
            out.append(Statement(
                role=role.name, kind="finding",
                text="Conversion moved by {change} between the windows (interval {lower} to {upper} at level {level}); "
                     "the confirmation verdict is {verdict}.",
                refs={"change": _ref(confirm, "/abs_change_pp/estimate", "pp"), "lower": _ref(confirm, "/abs_change_pp/lower", "pp"),
                      "upper": _ref(confirm, "/abs_change_pp/upper", "pp"), "level": _ref(confirm, "/abs_change_pp/level", "level"),
                      "verdict": _ref(confirm, "/verdict")},
            ))
        point = state.last("detect_change_point")
        if point and point.result and point.result.get("best_date"):
            out.append(Statement(
                role=role.name, kind="finding",
                text="The shift in the conversion level most plausibly began on {best}; the plausible onset range is {first} to {last}.",
                refs={"best": _ref(point, "/best_date", "date"), "first": _ref(point, "/plausible_range/0", "date"),
                      "last": _ref(point, "/plausible_range/1", "date")},
            ))
        match = state.last("match_event_candidates")
        if match and match.result and match.result["candidates"]:
            refs = {f"e{i}": _ref(match, f"/candidates/{i}/name") for i in range(len(match.result["candidates"]))}
            names = ", ".join("{" + k + "}" for k in refs)
            out.append(Statement(role=role.name, kind="finding", refs=refs,
                                 text=f"Logged events that coincide in time with the plausible range: {names}. A match is a date coincidence, "
                                      "not evidence about why conversion moved."))
        decomposition = state.last("decompose_change")
        if decomposition and decomposition.result and decomposition.result.get("localization"):
            segments = decomposition.result["localization"]["segments"]
            segment_refs = {f"seg{i}": _ref(decomposition, f"/localization/segments/{i}") for i in range(len(segments))}
            refs = {
                **segment_refs,
                "share": _ref(decomposition, "/localization/selected_share_of_change", "pct"),
                "traffic": _ref(decomposition, "/localization/traffic_share_post", "pct"),
            }
            listed = ", ".join("{" + k + "}" for k in segment_refs)
            if len(segments) == 1:
                text = (f"The change is concentrated in {listed}, which accounts for {{share}} of the overall change while carrying "
                        "{traffic} of post-window sessions. This says where the change is, not why.")
            else:
                text = (f"The change is concentrated in the set of segments {listed}, which together account for {{share}} of the overall "
                        "change while together carrying {traffic} of post-window sessions. The figures are for the whole set, not for "
                        "any one segment in it. This says where the change is, not why.")
            out.append(Statement(role=role.name, kind="finding", text=text, refs=refs))
        return out

    # ------------------------------------------------------------------ Scientist

    def _scientist(self, role: Role, context: Context) -> list[Statement]:
        state, out = context.state, []
        testing = state.last("test_candidates")
        if testing is None or testing.result is None:
            return out
        for i, _ in enumerate(testing.result["candidates"]):
            out.append(Statement(role=role.name, kind="finding", text="{headline}.", refs={"headline": _ref(testing, f"/candidates/{i}/headline")}))
        for i, _ in enumerate(testing.result["alternatives"]):
            out.append(Statement(role=role.name, kind="finding", text="Alternative considered: {alternative}.",
                                 refs={"alternative": _ref(testing, f"/alternatives/{i}")}))
        for claim in context.claims:
            event = Ref(call=claim.verdict_call, path=claim.verdict_path.rsplit("/", 1)[0] + "/event", fmt="text")
            if claim.allowed:
                required = required_refs(state, claim)
                refs = {name: Ref(call=c, path=p, fmt=f) for name, (c, p, f) in required.items()}  # type: ignore[arg-type]
                refs["focal"] = _ref(testing, "/focal/label")
                listed = " ".join("{" + k + "}" for k, (_, path, _) in required.items() if path.startswith("/assumptions/"))
                out.append(Statement(
                    role=role.name, kind="causal_claim", refs=refs,
                    text="By difference-in-differences, the change in conversion of {focal} relative to its comparison that is attributed "
                         "to {event} (evaluated lag {lag}, onset {onset}) is about {effect} (interval {lower} to {upper} at level "
                         "{level}, {n} observations), if these assumptions hold: " + listed,
                ))
            else:
                out.append(Statement(
                    role=role.name, kind="caveat", refs={"event": event},
                    text="No causal claim is made for {event}: the design's checks did not all pass, so the estimate is reported as "
                         "consistent with, not as a cause.",
                ))
        return out

    # ------------------------------------------------------------------ Experiment designer

    def _designer(self, role: Role, context: Context) -> list[Statement]:
        design = context.state.last("design_experiment")
        if design is None or design.result is None:
            return []
        if design.result["status"] != "ok":
            refs = {f"n{i}": _ref(design, f"/needs/{i}") for i in range(len(design.result["needs"]))}
            listed = "; ".join("{" + k + "}" for k in refs)
            return [Statement(role=role.name, kind="recommendation", refs=refs,
                              text="An experiment cannot be specified yet. What is missing: " + (listed or "see the design status") + ".")]
        plan = design.result.get("plan")
        refs = {"hypothesis": _ref(design, "/hypothesis")}
        text = "Recommended experiment. Hypothesis: {hypothesis}"
        if plan:
            refs.update({"n": _ref(design, "/plan/arms/0/n_sessions", "count"), "days": _ref(design, "/plan/duration_days", "count")})
            text += " It needs {n} sessions in the control arm and about {days} days of observed traffic."
        out = [Statement(role=role.name, kind="recommendation", text=text, refs=refs)]
        out += [
            Statement(role=role.name, kind="caveat", text="Design warning: {warning}", refs={"warning": _ref(design, f"/warnings/{i}")})
            for i in range(len(design.result["warnings"]))
        ]
        return out


# --------------------------------------------------------------------------- a model behind the same protocol


class TextModel(Protocol):
    """Any text model: a system message and a user message in, text out. Adapters for a provider implement this."""

    def complete(self, system: str, user: str) -> str: ...


class ModelOutputError(ValueError):
    """The model's output could not be read as the requested JSON."""


class _ModelRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    call: int
    path: str
    fmt: str = "text"


class _ModelStatement(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: str
    text: str
    refs: dict[str, _ModelRef] = Field(default_factory=dict)


class _ModelStatements(BaseModel):
    model_config = ConfigDict(extra="forbid")

    statements: list[_ModelStatement]


class _ModelParameters(BaseModel):
    model_config = ConfigDict(extra="forbid")

    arguments: dict[str, Any] = Field(default_factory=dict)
    rationale: str = ""


def digest(state: InvestigationState, *, max_leaves: int = 250, max_items: int = 12, max_text: int = 240) -> list[dict[str, Any]]:
    """The logged results a model reads: per call, JSON pointers to scalar values. Pointers are what it cites back."""
    out = []
    for call in state.log:
        if call.is_error or call.result is None:
            out.append({"call": call.seq, "tool": call.tool, "error": call.error})
            continue
        leaves: dict[str, Any] = {}

        def walk(node: Any, pointer: str, leaves: dict[str, Any] = leaves) -> None:
            if len(leaves) >= max_leaves:
                return
            if isinstance(node, dict):
                for key, value in node.items():
                    walk(value, f"{pointer}/{str(key).replace('~', '~0').replace('/', '~1')}")
            elif isinstance(node, list):
                for i, value in enumerate(node[:max_items]):
                    walk(value, f"{pointer}/{i}")
            elif node is not None:
                leaves[pointer] = node[:max_text] if isinstance(node, str) else node

        walk(call.result, "")
        out.append({"call": call.seq, "tool": call.tool, "values": leaves})
    return out


STATEMENT_INSTRUCTIONS = """\
Reply with one JSON object and nothing else:
{"statements": [{"kind": "finding" | "caveat" | "recommendation" | "causal_claim",
                 "text": "a sentence with {placeholders} and no digits",
                 "refs": {"placeholder": {"call": <call number>, "path": "<json pointer from the evidence>", "fmt": "<format>"}}}]}
Formats: text and date for strings; pp for percentage-point values; pct for fractions shown as percent; level for a probability
level; count for whole numbers; p for other numbers. Every placeholder needs a reference and every reference a placeholder.
A causal_claim is allowed only for an entry of "allowed_claims", and must quote every reference listed there as required."""

PARAMETER_INSTRUCTIONS = """\
Reply with one JSON object and nothing else: {"arguments": {"<parameter>": <value>}, "rationale": "<why, with no digits>"}.
Use only the parameters listed, within their ranges, or reply {"arguments": {}, "rationale": "default"} to keep the defaults."""


def _json_object(text: str) -> dict[str, Any]:
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise ModelOutputError("the model did not return a JSON object")
    try:
        value = json.loads(text[start : end + 1])
    except json.JSONDecodeError as exc:
        raise ModelOutputError(f"the model's JSON is invalid: {exc.msg}") from None
    if not isinstance(value, dict):
        raise ModelOutputError("the model did not return a JSON object")
    return value


class ModelReasoner:
    """A ``Reasoner`` backed by a text model. The model's output is parsed here and validated by the orchestrator.

    A model failure is not hidden: a ``ModelOutputError`` (or whatever the model raises) reaches the orchestrator, which records it
    and carries on without that text or parameter choice.
    """

    def __init__(self, model: TextModel):
        self.model = model

    def choose_parameters(self, role: Role, tool: str, context: Context) -> ParameterChoice | None:
        if tool not in TUNABLE:
            return None
        user = json.dumps({"task": "choose_parameters", "tool": tool, "parameters": describe(tool),
                           "evidence_so_far": digest(context.state)}, default=str)
        raw = _json_object(self.model.complete(role.system_prompt(), user + "\n\n" + PARAMETER_INSTRUCTIONS))
        try:
            parsed = _ModelParameters.model_validate(raw)
        except ValidationError as exc:
            raise ModelOutputError(f"parameter choice is malformed: {exc.errors()[0]['msg']}") from None
        return ParameterChoice(arguments=parsed.arguments, rationale=parsed.rationale) if parsed.arguments else None

    def interpret(self, role: Role, context: Context) -> list[Statement]:
        claims = [
            {"event_call": c.verdict_call, "did_call": c.did_call, "allowed": c.allowed, "reason": c.reason,
             "required_refs": {k: {"call": call, "path": path, "fmt": fmt} for k, (call, path, fmt) in required_refs(context.state, c).items()}
             if c.allowed else None}
            for c in context.claims
        ]
        user = json.dumps({"task": "interpret", "role": role.name, "formats": sorted(FORMATS), "allowed_claims": claims,
                           "evidence": digest(context.state)}, default=str)
        raw = _json_object(self.model.complete(role.system_prompt(), user + "\n\n" + STATEMENT_INSTRUCTIONS))
        try:
            parsed = _ModelStatements.model_validate(raw)
        except ValidationError as exc:
            raise ModelOutputError(f"the statements are malformed: {exc.errors()[0]['msg']}") from None
        out = []
        for item in parsed.statements:
            if item.kind not in ("finding", "caveat", "recommendation", "causal_claim"):
                raise ModelOutputError(f"unknown statement kind {item.kind!r}")
            if not all(re.fullmatch(r"text|date|count|pp|pct|level|p", r.fmt) for r in item.refs.values()):
                raise ModelOutputError("unknown reference format")
            out.append(Statement(role=role.name, kind=item.kind, text=item.text,  # type: ignore[arg-type]
                                 refs={k: Ref(call=r.call, path=r.path, fmt=r.fmt) for k, r in item.refs.items()}))  # type: ignore[arg-type]
        return out
