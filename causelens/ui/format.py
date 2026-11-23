"""How a traced value is shown. Display precision is fixed here; no value is changed, and the exact value is kept for a tooltip."""
from __future__ import annotations

from causelens.agents.trace import Traced, TracedInterval


def fmt_number(value: float, unit: str) -> str:
    v = float(value)
    match unit:
        case "pp":
            return f"{v:+.3f} pp"
        case "pp_per_day":
            return f"{v:+.4f} pp/day"
        case "fraction":
            return f"{v * 100:.2f}%"
        case "share":
            return f"{v * 100:.1f}%"
        case "level":
            return f"{v * 100:.0f}%"
        case "count" | "sessions" | "days" | "orders":
            return f"{round(v):,}"
        case "p_value":
            return f"{v:.4g}"
        case "sd":
            return f"{v:.3g}"
    return f"{v:.4g}"


def fmt_traced(t: Traced) -> str:
    """The text a traced value is displayed as."""
    value = t.value
    if t.unit == "flag":
        return "yes" if value is True else "no"
    if t.unit in ("date", "text") or isinstance(value, str):
        return str(value)
    if isinstance(value, bool):
        return "yes" if value else "no"
    return fmt_number(value, t.unit)


def fmt_bounds(i: TracedInterval) -> tuple[str, str, str, str]:
    """(estimate, lower, upper, level) as displayed."""
    return (fmt_number(i.estimate, i.unit), fmt_number(i.lower, i.unit), fmt_number(i.upper, i.unit), fmt_number(i.level, "level"))


def exact(value: object) -> str:
    """The unrounded value, for a tooltip."""
    return repr(value) if isinstance(value, float) else str(value)
