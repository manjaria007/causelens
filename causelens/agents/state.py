"""The shared investigation state and the log of every MCP call (Requirement 8.2).

``InvestigationState`` is the one object the stages pass along. Its ``log`` is append-only: each entry stores the tool,
the arguments as sent and the structured result exactly as the server returned it. Large prior results that were passed
on to a later call (the quality report, the decomposition) are stored in the arguments as ``{"$prior": seq}``, a pointer to
the call that produced them, so the log stays readable and the data are not duplicated.

Nothing here computes a number. Results are validated into the analytics models when they are read.
"""
from __future__ import annotations

import copy
from typing import Any, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr

from causelens.analytics import (
    CandidateMatchResult,
    CandidateTestingResult,
    CandidateVerdict,
    ChangePointResult,
    ConfirmChangeResult,
    DataQualityReport,
    DecompositionResult,
    DidResult,
    ExpectedPattern,
    ExperimentDesign,
    ImpactResult,
    PlaceboResult,
    PowerResult,
    PreTrendResult,
)
from causelens.mcp.datasets import DatasetCatalog
from causelens.mcp.models import IsoDate, strictly

M = TypeVar("M", bound=BaseModel)

#: The stages, in order (design: validate -> confirm -> change point -> localize -> test -> estimate -> recommend).
Stage = Literal["validate", "confirm", "change_point", "localize", "test", "estimate", "recommend"]
STAGES: tuple[Stage, ...] = ("validate", "confirm", "change_point", "localize", "test", "estimate", "recommend")

#: The model each tool's structured result is validated into.
TOOL_MODELS: dict[str, type[BaseModel]] = {
    "list_datasets": DatasetCatalog,
    "check_data_quality": DataQualityReport,
    "confirm_change": ConfirmChangeResult,
    "detect_change_point": ChangePointResult,
    "match_event_candidates": CandidateMatchResult,
    "decompose_change": DecompositionResult,
    "test_candidates": CandidateTestingResult,
    "test_event_scope": CandidateVerdict,
    "estimate_did": DidResult,
    "check_pre_trends": PreTrendResult,
    "run_placebo": PlaceboResult,
    "project_impact": ImpactResult,
    "power_analysis": PowerResult,
    "design_experiment": ExperimentDesign,
}

#: Which tool produces the prior result a tool argument carries.
PRIOR_ARGUMENT_SOURCE: dict[str, str] = {
    "quality": "check_data_quality",
    "change_point": "detect_change_point",
    "decomposition": "decompose_change",
    "candidate_testing": "test_candidates",
    "did": "estimate_did",
}


class Window(BaseModel):
    """An inclusive calendar window. Dates are ISO ``YYYY-MM-DD`` only (the MCP boundary's rule: no timestamps, no numbers)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    start: IsoDate
    end: IsoDate

    def as_arguments(self) -> dict[str, str]:
        return {"start": self.start.isoformat(), "end": self.end.isoformat()}


class InvestigationRequest(BaseModel):
    """What the user asks. Windows, declared patterns and lags are the user's; nothing is inferred from event names.

    Values are not coerced, as at the MCP boundary: a date is an ISO string, a lag is a JSON integer (``true``, ``"1"`` and
    ``1.0`` are refused), and a declared pattern is validated as strict JSON.

    ``question`` is recorded and echoed in the report. The orchestrator does not interpret it: the metric is the dataset's
    conversion rate (``orders / sessions``).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    question: StrictStr
    dataset_id: StrictStr
    pre_window: Window
    post_window: Window
    candidates: list[StrictStr] | None = None
    expected_patterns: dict[StrictStr, strictly(ExpectedPattern)] | None = None  # type: ignore[valid-type]
    lag_days: dict[StrictStr, StrictInt] | None = None


class Prior:
    """A marker for an argument whose value is the logged result of call ``seq``."""

    __slots__ = ("seq",)

    def __init__(self, seq: int):
        self.seq = seq

    def __repr__(self) -> str:
        return f"Prior({self.seq})"


class FrozenDict(dict):
    """A dict that cannot be edited. Logged payloads are snapshots, so nothing downstream can rewrite history."""

    def _blocked(self, *args: Any, **kwargs: Any) -> Any:
        raise TypeError("a logged payload is read-only")

    __setitem__ = __delitem__ = __ior__ = clear = pop = popitem = setdefault = update = _blocked

    def __deepcopy__(self, memo: dict) -> FrozenDict:
        return FrozenDict({k: copy.deepcopy(v, memo) for k, v in self.items()})

    def __copy__(self) -> FrozenDict:
        return FrozenDict(self)

    def __reduce__(self) -> tuple:
        return (FrozenDict, (dict(self),))


class FrozenList(list):
    """A list that cannot be edited (see ``FrozenDict``)."""

    def _blocked(self, *args: Any, **kwargs: Any) -> Any:
        raise TypeError("a logged payload is read-only")

    __setitem__ = __delitem__ = __iadd__ = __imul__ = append = extend = insert = remove = pop = clear = sort = reverse = _blocked

    def __deepcopy__(self, memo: dict) -> FrozenList:
        return FrozenList(copy.deepcopy(v, memo) for v in self)

    def __copy__(self) -> FrozenList:
        return FrozenList(self)

    def __reduce__(self) -> tuple:
        return (FrozenList, (list(self),))


def freeze(value: Any) -> Any:
    """A read-only deep copy of JSON data. Nothing in the result aliases the input."""
    if isinstance(value, dict):
        return FrozenDict({str(k): freeze(v) for k, v in value.items()})
    if isinstance(value, list | tuple):
        return FrozenList(freeze(v) for v in value)
    return value


def thaw(value: Any) -> Any:
    """An ordinary, editable deep copy of JSON data (what is sent to a server)."""
    if isinstance(value, dict):
        return {str(k): thaw(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [thaw(v) for v in value]
    return value


class LoggedCall(BaseModel):
    """One MCP tool call. ``result`` is the server's structured content, untouched, held as a read-only snapshot."""

    model_config = ConfigDict(frozen=True)

    seq: int = Field(description="1-based position in the log.")
    role: str
    stage: str
    tool: str
    arguments: dict[str, Any] = Field(description="As sent. Prior results appear as {'$prior': seq}.")
    is_error: bool = False
    result: dict[str, Any] | None = None
    error: str | None = None

    def model_post_init(self, context: Any, /) -> None:
        object.__setattr__(self, "arguments", freeze(self.arguments))
        object.__setattr__(self, "result", None if self.result is None else freeze(self.result))


class StageRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    stage: Stage
    role: str
    outcome: Literal["done", "stopped", "skipped"]
    reason: str | None = None
    calls: list[int] = Field(default_factory=list)


class ParameterDecision(BaseModel):
    """A parameter choice the reasoner made for one call (the values are in that call's logged arguments)."""

    model_config = ConfigDict(frozen=True)

    tool: str
    call: int | None = Field(description="The call that used the choice; None when the choice was refused.")
    names: list[str]
    rationale: str
    accepted: bool
    problem: str | None = None


class InvestigationState(BaseModel):
    """The request, the call log and the stage records. The only thing the stages share."""

    request: InvestigationRequest
    log: list[LoggedCall] = Field(default_factory=list)
    stages: list[StageRecord] = Field(default_factory=list)
    decisions: list[ParameterDecision] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list, description="Rejected statements and cross-check failures; never hidden.")

    def call(self, seq: int) -> LoggedCall:
        if not 1 <= seq <= len(self.log):
            raise KeyError(f"no call {seq} in a log of {len(self.log)}")
        return self.log[seq - 1]

    def calls_to(self, tool: str) -> list[LoggedCall]:
        return [c for c in self.log if c.tool == tool and not c.is_error]

    def last(self, tool: str) -> LoggedCall | None:
        found = self.calls_to(tool)
        return found[-1] if found else None

    def model(self, call: LoggedCall | int, expected: type[M] | None = None) -> Any:
        """The call's result validated into its analytics model."""
        entry = self.call(call) if isinstance(call, int) else call
        if entry.is_error or entry.result is None:
            raise ValueError(f"call {entry.seq} ({entry.tool}) has no result: {entry.error}")
        model = TOOL_MODELS[entry.tool]
        if expected is not None and model is not expected:
            raise TypeError(f"call {entry.seq} is {entry.tool}, which returns {model.__name__}, not {expected.__name__}")
        return model.model_validate(entry.result)

    def stage(self, stage: Stage, role: str, outcome: Literal["done", "stopped", "skipped"], reason: str | None = None) -> None:
        """Close a stage; ``calls`` are the log entries made since the previous stage record."""
        first = 1 + sum(len(s.calls) for s in self.stages)
        self.stages.append(StageRecord(stage=stage, role=role, outcome=outcome, reason=reason,
                                       calls=list(range(first, len(self.log) + 1))))
