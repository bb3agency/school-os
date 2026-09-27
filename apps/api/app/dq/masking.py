"""Masked forms of compared values (FR-DQ-006, SEC-012, invariant 5).

Findings never store a compared value in clear: ``sis.dq_findings.details`` holds the value id
(so the student record can show it, or reveal a C3 value with its own audit) and one of these
masked forms. Pure functions, no I/O.

- names: first character of every word, then three dots (``"K••• V••• S•••"``); an initial
  keeps its dot (``"K."``). The length of a word is not revealed.
- dates: ``"••/••/••••"`` (which parts differ is reported separately, as codes).
- everything else: ``"••••"``.
"""

from __future__ import annotations

import unicodedata
from typing import Final

DOT: Final = "•"
FULL_MASK: Final = DOT * 4
DATE_MASK: Final = f"{DOT * 2}/{DOT * 2}/{DOT * 4}"


def _grapheme_head(word: str) -> str:
    """The first character with its combining marks (Telugu vowel signs, virama)."""
    head = word[0]
    for ch in word[1:]:
        if unicodedata.combining(ch) or unicodedata.category(ch) in ("Mn", "Mc"):
            head += ch
        else:
            break
    return head


def mask_name(value: str | None) -> str | None:
    if value is None:
        return None
    words = unicodedata.normalize("NFC", value).split()
    if not words:
        return FULL_MASK
    out = []
    for word in words:
        head = _grapheme_head(word)
        rest = word[len(head) :]
        if rest in ("", "."):
            out.append(head + rest)
        else:
            out.append(head + DOT * 3)
    return " ".join(out)


def mask_value(value: str | None, *, kind: str) -> str | None:
    """Masked form of ``value``; ``kind`` is ``name``, ``date`` or anything else."""
    if value is None:
        return None
    if kind == "name":
        return mask_name(value)
    if kind == "date":
        return DATE_MASK
    return FULL_MASK
