"""Contextual chunk header (docs/06 §4.5): prepended for embedding and FTS, never shown.

Example: ``[Circular] Exam timings · DEO Guntur · Ref Rc.No.123/B/2026 · 12 Aug 2026 ·
Subject: Exam timings · § 3. Timings``. Only document metadata the office entered (or that
metadata extraction confirmed) and the chunk's section headings appear; nothing is guessed. Dates
are rendered without the process locale, so the header is the same on every machine.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from typing import Final

from app.knowledge.chunking.text import graphemes, normalize_space
from app.knowledge.domain import DocumentContext

SEPARATOR: Final = " · "
PATH_SEPARATOR: Final = " > "
MAX_PART_GRAPHEMES: Final = 120
"""A heading or metadata value longer than this is shortened (a mis-styled paragraph must not
turn the header into a second copy of the chunk)."""
_MONTHS: Final = (
    *("Jan", "Feb", "Mar", "Apr", "May", "Jun"),
    *("Jul", "Aug", "Sep", "Oct", "Nov", "Dec"),
)


def _part(value: str | None) -> str:
    if value is None:
        return ""
    clean = normalize_space(value)
    clusters = graphemes(clean)
    if len(clusters) > MAX_PART_GRAPHEMES:
        return "".join(clusters[:MAX_PART_GRAPHEMES]).rstrip() + "…"
    return clean


def format_date(value: date) -> str:
    return f"{value.day} {_MONTHS[value.month - 1]} {value.year}"


def doc_type_label(doc_type: str) -> str:
    """``verified_answer`` -> ``Verified answer`` (sentence case, docs/13)."""
    words = normalize_space(doc_type.replace("_", " "))
    return words[:1].upper() + words[1:]


def context_header(context: DocumentContext, heading_path: Sequence[str]) -> str:
    title = _part(context.title)
    first = f"[{doc_type_label(context.doc_type)}]" + (f" {title}" if title else "")
    parts = [first]
    if issuer := _part(context.issuer):
        parts.append(issuer)
    if reference := _part(context.reference_no):
        parts.append(f"Ref {reference}")
    if context.issued_on is not None:
        parts.append(format_date(context.issued_on))
    if subject := _part(context.subject):
        parts.append(f"Subject: {subject}")
    path = [p for p in (_part(h) for h in heading_path) if p]
    if path:
        parts.append("§ " + PATH_SEPARATOR.join(path))
    return SEPARATOR.join(parts)


__all__ = ["context_header", "doc_type_label", "format_date"]
