"""The LLM layer: the part that interprets tool results and writes the report text (design, overview).

A ``Reasoner`` receives a role and the investigation so far and returns ``Statement`` objects. It does not receive a way to
compute: the only route from a tool result to a sentence is a ``Ref`` into the log, and the orchestrator validates each
statement (no digits, no cause-asserting words outside a permitted ``causal_claim``, every reference resolves) before it
enters the report. A statement that fails is dropped and recorded in ``rejected_statements``.

``TemplateReasoner`` is the deterministic, offline implementation, so the demo runs without a model. A model-backed reasoner
implements the same protocol: it builds its system message with ``Role.system_prompt()``, asks the model for statements in
the ``Statement`` schema, and returns them. Nothing else changes, because the validation does not trust the author.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from causelens.agents.claims import ClaimEligibility
from causelens.agents.roles import Role
from causelens.agents.state import InvestigationState, LoggedCall
from causelens.agents.trace import Ref, Statement


@dataclass(frozen=True)
class Context:
    """What a reasoner may read: the log and which causal claims the checks allow."""

    state: InvestigationState
    claims: tuple[ClaimEligibility, ...]


class Reasoner(Protocol):
    def interpret(self, role: Role, context: Context) -> list[Statement]: ...


def _ref(call: LoggedCall, path: str, fmt: str = "text") -> Ref:
    return Ref(call=call.seq, path=path, fmt=fmt)  # type: ignore[arg-type]


class TemplateReasoner:
    """Fixed sentence templates over the logged results. Deterministic; no model."""

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
            out.append(Statement(
                role=role.name, kind="finding",
                text="The change is concentrated in {segment}, which accounts for {share} of the overall change while carrying "
                     "{traffic} of post-window sessions. This says where the change is, not why.",
                refs={"segment": _ref(decomposition, "/localization/segments/0"), "share": _ref(decomposition, "/localization/selected_share_of_change", "pct"),
                      "traffic": _ref(decomposition, "/localization/traffic_share_post", "pct")},
            ))
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
            did = state.call(claim.did_call)
            if claim.allowed:
                assumptions = (did.result or {}).get("assumptions", [])
                refs = {
                    "event": _ref(state.call(claim.verdict_call), claim.verdict_path.rsplit("/", 1)[0] + "/event"),
                    "effect": _ref(did, "/effect_pp/estimate", "pp"), "lower": _ref(did, "/effect_pp/lower", "pp"),
                    "upper": _ref(did, "/effect_pp/upper", "pp"), "level": _ref(did, "/effect_pp/level", "level"),
                    "n": _ref(did, "/n_obs", "count"), "focal": _ref(testing, "/focal/label"),
                }
                refs.update({f"a{i}": _ref(did, f"/assumptions/{i}") for i in range(len(assumptions))})
                listed = " ".join("{" + f"a{i}" + "}" for i in range(len(assumptions)))
                out.append(Statement(
                    role=role.name, kind="causal_claim", refs=refs,
                    text="By difference-in-differences, the change in conversion of {focal} relative to its comparison that is attributed to {event} "
                         "is about {effect} (interval {lower} to {upper} at level {level}, {n} observations), if these assumptions hold: " + listed,
                ))
            else:
                out.append(Statement(
                    role=role.name, kind="caveat", refs={"event": _ref(state.call(claim.verdict_call), claim.verdict_path.rsplit("/", 1)[0] + "/event")},
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
