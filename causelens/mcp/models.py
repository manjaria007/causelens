"""Typed JSON parameters shared by the MCP tools.

These are the only types the adapter defines for tool *inputs*. Everything a tool returns, and every prior result a
tool accepts, is an existing ``causelens.analytics`` model, so the published schemas are generated from the analytics
code and cannot drift from it.
"""
from datetime import date
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StrictInt

__all__ = [
    "AlphaLevel",
    "DatasetId",
    "DateWindow",
    "FocalSpec",
    "PostWindow",
    "PreWindow",
    "Segment",
    "SegmentSpec",
    "StrictInt",
]


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
AlphaLevel = Annotated[float, Field(description="Probability in (0, 1).")]
