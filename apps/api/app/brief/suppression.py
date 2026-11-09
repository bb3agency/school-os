"""Small-cell suppression of student counts in the weekly brief (FR-BRF-009). Pure.

The rule of the Ask count tools (docs/06 §7, ``app/knowledge/tools/counts.py``): a count of
students between 1 and ``minimum - 1`` is hidden; when exactly one count of a group is hidden,
the next smallest non-zero count is hidden too, so it cannot be worked out from the total.
Zero is shown (nobody is described by it).
"""

from __future__ import annotations

from collections.abc import Mapping


def small(value: int, minimum: int) -> bool:
    return 0 < value < minimum


def suppress_one(value: int, minimum: int) -> int | None:
    """A lone student count: hidden (None) when it is a small cell."""
    return None if small(value, minimum) else value


def suppress_group(counts: Mapping[str, int], minimum: int) -> dict[str, int | None]:
    """A group of student counts that add up to a total shown alongside (primary and secondary
    suppression)."""
    hidden = {k for k, v in counts.items() if small(v, minimum)}
    if len(hidden) == 1:
        rest = sorted((v, k) for k, v in counts.items() if k not in hidden and v > 0)
        if rest:
            hidden.add(rest[0][1])
    return {k: (None if k in hidden else v) for k, v in counts.items()}


__all__ = ["small", "suppress_group", "suppress_one"]
