"""Offline stand-in for the ``contextualize`` role (docs/06 §4.11; SOS_KB_PROVIDER_MODE=fake).

Deterministic and built only from what the request carries, so its contexts always pass the
server-side checks (no new facts) and CI, local runs and the ``app-fake`` evals can exercise the
whole path. For each passage it writes what a careful reader would add to situate it:

    "<title>: <subject>. Part: <nearest heading before the passage>."

- *subject*: the document's ``Sub:`` / ``Subject:`` / ``విషయం:`` line, else its first line;
- *heading*: the last ``#`` heading of the document that comes before the passage's text (only
  when the passage does not already start with it).

It never reads anything but the request. It is a stand-in for measuring the application's
controls and the effect of adding document-level context, **not** a language model: it cannot
resolve references ("the said fee") the way a model can, nor summarise.
"""

from __future__ import annotations

import re
from typing import Any, Final

from app.knowledge.contextual.rules import parse_document, parse_passages

_SUBJECT: Final = re.compile(r"^(?:sub|subject|విషయం)\s*[:.\-]\s*(.+)$", re.IGNORECASE)
_HEADING: Final = re.compile(r"^#+\s+(.+)$")
_MAX_SUBJECT: Final = 160
_PROBE: Final = 40


def _subject(lines: list[str]) -> str:
    for line in lines:
        m = _SUBJECT.match(line.strip())
        if m:
            return m.group(1).strip()
    for line in lines:
        text = _HEADING.sub(r"\1", line).strip()
        if text:
            return text
    return ""


def _heading_before(lines: list[str], passage: str) -> str:
    probe = passage.strip()[:_PROBE]
    heading = ""
    for line in lines:
        m = _HEADING.match(line)
        if m:
            heading = m.group(1).strip()
        elif probe and probe[:20] in line:
            return heading
    return ""


def _context(title: str, subject: str, heading: str, passage: str) -> str:
    parts = [p for p in (title.strip(), subject[:_MAX_SUBJECT].strip()) if p]
    parts = list(dict.fromkeys(parts))
    text = ": ".join(parts)
    if heading and not passage.strip().startswith(heading):
        text = f"{text}. Part: {heading}" if text else heading
    return (text.rstrip(".") + ".") if text else "Part of this document."


def contextual_reply(system: str, request_text: str) -> dict[str, Any]:
    """The stand-in's ``{"contexts": [...]}`` for one call."""
    document = parse_document(system)
    title, body = (document[0], document[2]) if document else ("", "")
    lines = [line for line in body.splitlines() if line.strip()]
    subject = _subject(lines)
    contexts = [
        {"passage": number, "context": _context(title, subject, _heading_before(lines, text), text)}
        for number, text in parse_passages(request_text)
    ]
    return {"contexts": contexts}


__all__ = ["contextual_reply"]
