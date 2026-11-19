"""Typed JSON parameters shared by the MCP tools.

These are the only types the adapter defines for tool *inputs*. Everything a tool returns, and every prior result a
tool accepts, is an existing ``causelens.analytics`` model, so the published schemas are generated from the analytics
code and cannot drift from it.
"""
import json
from datetime import date
from typing import Annotated, TypeVar

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StrictBool,
    StrictFloat,
    StrictInt,
    ValidationError,
)

__all__ = [
    "AlphaLevel",
    "DatasetId",
    "DateWindow",
    "FocalSpec",
    "PostWindow",
    "PreWindow",
    "Segment",
    "SegmentSpec",
    "StrictBool",
    "StrictFloat",
    "StrictInt",
    "strictly",
]

M = TypeVar("M", bound=BaseModel)


class _StrictJson:
    """Validate a model-typed argument as strict JSON: ``true`` is not a number, ``"0.5"`` is not 0.5, ``5.0`` is not 5.

    The SDK validates arguments leniently, so a bare ``float`` field quietly turns ``true`` into ``1.0`` and a numeric
    string into a number. Prior results and settings are typed models whose fields would be coerced the same way, so each
    is re-validated here, in JSON mode with ``strict=True`` (dates are still ISO strings, and JSON integers are still
    accepted for float fields).
    """

    def __init__(self, model: type[BaseModel]):
        self.model = model

    def __call__(self, value: object) -> object:
        if isinstance(value, self.model):
            return value
        try:
            return self.model.model_validate_json(json.dumps(value), strict=True)
        except ValidationError as exc:
            detail = "; ".join(f"{'.'.join(str(p) for p in e['loc']) or self.model.__name__}: {e['msg']}" for e in exc.errors()[:6])
            raise ValueError(detail) from None
        except TypeError as exc:
            raise ValueError(f"not a JSON value: {exc}") from None


def strictly(model: type[M]) -> type[M]:
    """``model`` for use as a tool argument type, validated as strict JSON (see ``_StrictJson``)."""
    return Annotated[model, BeforeValidator(_StrictJson(model))]  # type: ignore[return-value]


class DateWindow(BaseModel):
    """A calendar window. Both ends are inclusive and are ISO dates (``YYYY-MM-DD``)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    start: date = Field(description="First day of the window (inclusive).")
    end: date = Field(description="Last day of the window (inclusive).")

    def as_tuple(self) -> tuple[date, date]:
        return (self.start, self.end)


Segment = Annotated[
    dict[str, str],
    Field(
        description=(
            "One segment as a conjunction of dimension values, for example "
            '{"device": "mobile", "browser": "safari"}. Dimensions: device, browser, channel, customer_type.'
        )
    ),
]
SegmentSpec = Segment | list[Segment]
FocalSpec = Annotated[
    SegmentSpec,
    Field(
        description=(
            "The focal (treated) population: one segment, or a list of segments that are pooled together. "
            "Pass the same population to every tool that analyses it."
        )
    ),
]

DatasetId = Annotated[str, Field(description="Id of a configured dataset. Call list_datasets to see the ids.")]
PreWindow = Annotated[DateWindow, Field(description="The baseline window (before the change), inclusive dates.")]
PostWindow = Annotated[DateWindow, Field(description="The window after the change, inclusive dates.")]
AlphaLevel = Annotated[StrictFloat, Field(description="Probability in (0, 1).")]
