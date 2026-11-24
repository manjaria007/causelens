"""Typed JSON parameters shared by the MCP tools.

These are the only types the adapter defines for tool *inputs*. Everything a tool returns, and every prior result a
tool accepts, is an existing ``causelens.analytics`` model, so the published schemas are generated from the analytics
code and cannot drift from it.
"""
import json
import re
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
    "IsoDate",
    "PostWindow",
    "PreWindow",
    "Segment",
    "SegmentSpec",
    "StrictBool",
    "StrictFloat",
    "StrictInt",
    "strictly",
    "strictly_restoring",
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


_ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


def _iso_date_only(value: object) -> date:
    """A calendar date is ``YYYY-MM-DD``. Pydantic's lax parsing also reads ``0`` and ``"86400"`` as Unix timestamps
    (1970-01-01 and 1970-01-02) and ``"2026-10-14T00:00:00"`` as a date; those change what a call means, so they are refused."""
    if type(value) is date:
        return value
    if isinstance(value, str) and _ISO_DATE.fullmatch(value):
        try:
            return date.fromisoformat(value)
        except ValueError:
            raise ValueError(f"{value!r} is not a real calendar date") from None
    raise ValueError(f"a date must be an ISO date string YYYY-MM-DD, got {value!r}")


IsoDate = Annotated[date, BeforeValidator(_iso_date_only), Field(description="An ISO calendar date, YYYY-MM-DD.")]


class DateWindow(BaseModel):
    """A calendar window. Both ends are inclusive and are ISO dates (``YYYY-MM-DD``)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    start: IsoDate = Field(description="First day of the window (inclusive), YYYY-MM-DD.")
    end: IsoDate = Field(description="Last day of the window (inclusive), YYYY-MM-DD.")

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


# --------------------------------------------------------------------------- match_event_candidates: known omissions in a copied change point

#: The empty containers of a ``ChangePointResult`` that a native Crew agent's tool call was observed to drop when it copied the result into
#: ``match_event_candidates`` (PR #13; ``docs/crew-evidence/slot-causelens-detective.json``). Each is restored ONLY when its key is absent.
CHANGE_POINT_RESTORABLE: tuple[tuple[tuple[str, ...], object], ...] = (
    (("segment",), {}),
    (("exclusions", "missing_dates"), []),
    (("exclusions", "truncated_baseline_dates"), []),
)


def restore_known_change_point_omissions(value: object) -> object:
    """Put back the three known empty containers when (and only when) their keys are absent; change nothing else.

    A supplied value is kept as it is, so a wrong type or an explicit ``null`` is still refused by the strict validation that follows, and a
    missing core field (``status``, ``plausible_dates``, ...) is still an error. ``exclusions`` itself is not invented: the two list fields are
    restored only inside an ``exclusions`` object that is present. This is not provenance: a change point is not recomputed by
    ``match_event_candidates``, so a copied result that was edited (rather than shortened) is used as given.
    """
    if not isinstance(value, dict):
        return value
    out = dict(value)
    for path, empty in CHANGE_POINT_RESTORABLE:
        parent = out
        for key in path[:-1]:
            child = parent.get(key)
            if not isinstance(child, dict):
                parent = None
                break
            child = dict(child)
            parent[key] = child
            parent = child
        if parent is not None and path[-1] not in parent:
            parent[path[-1]] = type(empty)()
    return out


def strictly_restoring(model: type[M]) -> type[M]:
    """``strictly(model)`` for a change point copied by an agent: the known omissions are restored first (see above)."""
    strict = _StrictJson(model)
    return Annotated[model, BeforeValidator(lambda v: strict(restore_known_change_point_omissions(v)))]  # type: ignore[return-value]
