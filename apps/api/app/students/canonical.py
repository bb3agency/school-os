"""Canonical value resolution (docs/05 §10, FR-STU-004, BR-01). Pure, table-driven tested.

Given the *current* value of each source for one attribute, pick the canonical one:

1. Rejected values never count.
2. Candidates are the sources listed in the policy's ``precedence``, in that order. Sources
   outside ``precedence`` (e.g. Aadhaar-as-printed, UDISE+, board) never become canonical; they
   can only produce conflicts.
3. With ``require_verified``: the first *verified* candidate wins. The result is provisional
   unless it comes from the policy ``anchor`` (identity attributes: the admission register,
   BR-01). With no verified candidate the first candidate is used and marked provisional; the
   data-quality engine raises a DQ-005-style finding for provisional identity values.
4. Without ``require_verified`` the first candidate wins (verified or not) and is not provisional.
5. ``conflicts`` lists every other current, non-rejected source whose comparison value differs.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

from app.students.definitions import CanonicalPolicy


class SourceValue(Protocol):
    @property
    def source(self) -> str: ...

    @property
    def verification_status(self) -> str: ...


@dataclass(frozen=True, slots=True)
class Resolution[V: SourceValue]:
    value: V | None
    provisional: bool
    conflicts: tuple[str, ...]


def resolve[V: SourceValue](
    policy: CanonicalPolicy,
    current: Mapping[str, V],
    *,
    compare: Mapping[str, str | None] | None = None,
) -> Resolution[V]:
    """Resolve the canonical value from ``current`` (source -> current value row).

    ``compare`` maps source -> comparison string (e.g. normalised name); when omitted no
    conflicts are reported.
    """
    usable = {s: v for s, v in current.items() if v.verification_status != "rejected"}
    candidates = [usable[s] for s in policy.precedence if s in usable]
    chosen: V | None = None
    provisional = False
    if candidates:
        if policy.require_verified:
            verified = [c for c in candidates if c.verification_status == "verified"]
            if verified:
                chosen = verified[0]
                provisional = policy.anchor is not None and chosen.source != policy.anchor
            else:
                chosen = candidates[0]
                provisional = True
        else:
            chosen = candidates[0]
    elif policy.require_verified and policy.anchor is not None:
        provisional = True  # nothing recorded yet for an identity attribute
    conflicts: tuple[str, ...] = ()
    if chosen is not None and compare is not None:
        mine = compare.get(chosen.source)
        conflicts = tuple(
            sorted(
                s
                for s in usable
                if s != chosen.source and compare.get(s) is not None and compare.get(s) != mine
            )
        )
    return Resolution(value=chosen, provisional=provisional, conflicts=conflicts)
