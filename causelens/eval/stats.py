"""Interval for a proportion of passes (Wilson score). Pure and deterministic."""
from __future__ import annotations

from math import sqrt

Z_95 = 1.959963984540054  # the 97.5th percentile of the standard normal


def wilson_interval(passes: int, n: int, z: float = Z_95) -> tuple[float, float]:
    """The Wilson score interval for ``passes`` successes in ``n`` trials, within [0, 1]. ``n`` must be positive."""
    if n <= 0:
        raise ValueError("n must be positive")
    if not 0 <= passes <= n:
        raise ValueError("passes must be between 0 and n")
    p = passes / n
    denominator = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denominator
    half = z * sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denominator
    # The score interval contains p exactly; rounding can leave an end a hair inside it (3e-18 for 0 of 63), so clamp to p.
    return max(0.0, min(centre - half, p)), min(1.0, max(centre + half, p))
