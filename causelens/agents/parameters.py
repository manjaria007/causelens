"""Which tool parameters the reasoner may choose, and the checks on its choice (design: the LLM layer "chooses parameters").

The list is short on purpose. Windows, the dataset, prior results, exclusions, declared patterns and lags, seeds, significance
levels, power and the minimum detectable effect are the user's or the pipeline's, and a reasoner cannot change them. What is left
are settings that change how much is searched or how many days are required, not what is claimed. A choice is validated strictly
(a JSON integer is not ``true``, ``"7"`` or ``7.0``; ranges are inclusive), and its rationale follows the same prose rules as a
report sentence. A refused choice is recorded and the tool's own default is used.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from causelens.agents.trace import TraceError, check_prose


class ParameterError(ValueError):
    """A parameter choice that is not permitted. The message names the parameter and the rule, never a value."""


@dataclass(frozen=True)
class ParamSpec:
    kind: Literal["int", "float", "bool"]
    default: Any
    low: float | None = None
    high: float | None = None
    description: str = ""


TUNABLE: dict[str, dict[str, ParamSpec]] = {
    "confirm_change": {
        "n_bootstrap": ParamSpec("int", 2000, 500, 20000, "Bootstrap resamples of days for the interval."),
        "min_days_per_window": ParamSpec("int", 7, 3, 60, "Fewest usable days a window needs before the change can be confirmed."),
    },
    "match_event_candidates": {
        "lead_days": ParamSpec("int", 3, 0, 14, "How many days before the plausible onset range an event may fall and still be listed."),
    },
    "decompose_change": {
        "explain_threshold": ParamSpec("float", 0.8, 0.5, 0.95, "Share of the change the explaining set of segments must reach."),
        "include_pairs": ParamSpec("bool", True, None, None, "Also decompose by pairs of dimensions."),
    },
    "test_candidates": {
        "lead_days": ParamSpec("int", 3, 0, 14, "How many days before the plausible onset range an event may fall and still be tested."),
    },
}


class ParameterChoice(BaseModel):
    """What a reasoner returns: values for tunable parameters of one tool, and why. No digits in the rationale."""

    model_config = ConfigDict(frozen=True)

    arguments: dict[str, Any]
    rationale: str


def validate_choice(tool: str, choice: ParameterChoice) -> dict[str, Any]:
    """The choice as tool arguments, or ``ParameterError``."""
    spec = TUNABLE.get(tool)
    if spec is None:
        raise ParameterError(f"{tool} has no tunable parameters")
    for name, value in choice.arguments.items():
        if name not in spec:
            raise ParameterError(f"{name} is not a tunable parameter of {tool}; the tunable ones are {sorted(spec)}")
        item = spec[name]
        if item.kind == "bool":
            if type(value) is not bool:
                raise ParameterError(f"{name} must be a JSON boolean")
            continue
        ok = type(value) is int if item.kind == "int" else type(value) in (int, float)
        if not ok:
            raise ParameterError(f"{name} must be a JSON {'integer' if item.kind == 'int' else 'number'}")
        if (item.low is not None and value < item.low) or (item.high is not None and value > item.high):
            raise ParameterError(f"{name} is outside the permitted range")
    try:
        check_prose(choice.rationale)
    except TraceError as exc:
        raise ParameterError(f"the rationale is refused: {exc}") from None
    if not choice.rationale.strip():
        raise ParameterError("a parameter choice needs a rationale")
    return dict(choice.arguments)


def describe(tool: str) -> list[dict[str, Any]]:
    """The tunable parameters of a tool as plain data, for a model's prompt."""
    return [
        {"name": n, "type": s.kind, "default": s.default, "min": s.low, "max": s.high, "meaning": s.description}
        for n, s in TUNABLE.get(tool, {}).items()
    ]
