"""Parent-notice drafting: request text, schema and validation (FR-NOTICE-001..004).

A notice is drafted from exactly one of:

- a circular: its indexed (Aadhaar-masked) passages and the deadlines staff confirmed from it;
- staff text: what a staff member typed, refused if it holds a phone number, an email address or
  an Aadhaar-like number (:func:`has_personal_numbers`; FR-NOTICE-002).

Never from student records: the caller has no way to pass them (invariant 8, CLAUDE.md §11 "send
a field, not a record"; here no student field at all). The draft comes back as four strings; the
Telugu ones must use Telugu script, every value is cut to the configured length and passed
through ``core.redaction.redact`` (masks phones, emails, Aadhaar numbers) before it is stored.
"""

from __future__ import annotations

import datetime as dt
import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Final, Literal

from app.core.redaction import redact
from app.knowledge.circulars.reading import Passage
from app.knowledge.config.circulars import NoticeConfig

SCHEMA_TAG: Final = "sos:parent_notice.v1"
_TELUGU: Final = re.compile(r"[ఀ-౿]")
_SPACES: Final = re.compile(r"[ \t\u00a0]+")
_BLANK_LINES: Final = re.compile(r"\n{3,}")

SCHEMA: Final[dict[str, Any]] = {
    "type": "object",
    "description": SCHEMA_TAG,
    "additionalProperties": False,
    "required": ["title_en", "body_en", "title_te", "body_te"],
    "properties": {
        "title_en": {"type": "string", "description": "Short English title"},
        "body_en": {"type": "string", "description": "English notice for parents"},
        "title_te": {"type": "string", "description": "Short Telugu title"},
        "body_te": {"type": "string", "description": "The same notice in Telugu"},
    },
}


@dataclass(frozen=True, slots=True)
class ConfirmedDeadline:
    due_on: dt.date
    title: str


@dataclass(frozen=True, slots=True)
class NoticeSource:
    kind: Literal["circular", "staff_text"]
    title: str = ""
    passages: tuple[Passage, ...] = ()
    deadlines: tuple[ConfirmedDeadline, ...] = ()
    staff_text: str = ""


@dataclass(frozen=True, slots=True)
class NoticeDraft:
    title_en: str
    body_en: str
    title_te: str
    body_te: str


def has_personal_numbers(text: str) -> bool:
    """True when ``text`` holds a mobile number, an email address or an Aadhaar-like number."""
    normalised = unicodedata.normalize("NFC", text)
    return redact(normalised) != normalised


def _tidy(text: str) -> str:
    lines = [
        _SPACES.sub(" ", line).strip() for line in unicodedata.normalize("NFC", text).split("\n")
    ]
    return _BLANK_LINES.sub("\n\n", "\n".join(lines)).strip()


def _cut(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    head = text[:limit].rsplit(" ", 1)[0]
    return (head or text[:limit]).rstrip(" ,;:") + "…"


def build_request(source: NoticeSource, config: NoticeConfig) -> str:
    """The user message for the ``parent_notice`` prompt."""
    if source.kind == "staff_text":
        text = _tidy(source.staff_text)[: config.max_staff_text_chars]
        return f"Source: text written by school staff.\n\n{text}\n"
    head = f"Source: a circular received by the school.\nTitle: {source.title or '(none)'}\n"
    if source.deadlines:
        head += "Dates the school confirmed from this circular:\n"
        head += "".join(
            f"- {d.due_on.strftime('%d/%m/%Y')}: {d.title}\n"
            for d in sorted(source.deadlines, key=lambda d: d.due_on)
        )
    head += "\nPassages of the circular:\n"
    budget = config.max_input_chars - len(head)
    lines: list[str] = []
    for passage in source.passages:
        line = f"[{passage.number}] {' '.join(passage.text.split())}\n"
        if len(line) > budget:
            break
        budget -= len(line)
        lines.append(line)
    return head + "".join(lines)


def _field(raw: Mapping[str, object], key: str, limit: int, *, telugu: bool) -> str:
    value = raw.get(key)
    if not isinstance(value, str):
        return ""
    text = _tidy(redact(value))
    if telugu and not _TELUGU.search(text):
        return ""
    return _cut(text, limit)


def validate_notice(raw: Mapping[str, object], config: NoticeConfig) -> NoticeDraft:
    return NoticeDraft(
        title_en=_field(raw, "title_en", config.max_title_chars, telugu=False),
        body_en=_field(raw, "body_en", config.max_body_chars, telugu=False),
        title_te=_field(raw, "title_te", config.max_title_chars, telugu=True),
        body_te=_field(raw, "body_te", config.max_body_chars, telugu=True),
    )


__all__ = [
    "SCHEMA",
    "SCHEMA_TAG",
    "ConfirmedDeadline",
    "NoticeDraft",
    "NoticeSource",
    "build_request",
    "has_personal_numbers",
    "validate_notice",
]
