"""What the ``contextualize`` role sees, and the checks on what it writes (docs/06 §4.11).

Request (Anthropic's contextual retrieval technique, arranged for prompt caching):

- **system** = the versioned prompt (``prompts/contextualize.v<n>.txt``) rendered with the
  document's title, type and its whole text (:func:`document_text`, already Aadhaar-masked by
  ingestion; cut to ``max_document_chars`` at a line boundary by :func:`clip_document`). It is
  identical for every call of one document version, so the provider caches it once (the
  gateway marks the system prompt cacheable) and every further call reads it from the cache.
- **user text** = only the passages of this call, numbered (:func:`passages_message`).
- **output** (structured, :data:`SCHEMA`): ``contexts[]`` of ``{passage, context}``.

Checks (:class:`ContextChecker`), per context: NFC and whitespace collapsed; at least
``min_chars``; cut at a word boundary to ``max_chars``; Telugu script for a Telugu passage and
none for an English one (code-mixed: either); no link or e-mail address; **no new facts**: every
number must be written in the document (digits compared as numbers, any script), and every
capitalised Latin word must occur in the document or be a configured generic word (Telugu has no
capitals, so for Telugu text only the number rule applies); finally ``core.redaction.redact``
(Aadhaar, phone numbers, e-mails). A context that fails is dropped with a reason code, never
repaired: the chunk is indexed without one (``rejected``).
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final

from app.core.redaction import redact
from app.knowledge.config.contextual import ContextualConfig
from app.knowledge.domain import ExtractedDocument, Locale

SCHEMA_TAG: Final = "sos:chunk_contexts.v1"
"""The schema's ``description``: names the request for the offline fake provider."""

SCHEMA: Final[dict[str, Any]] = {
    "type": "object",
    "description": SCHEMA_TAG,
    "additionalProperties": False,
    "required": ["contexts"],
    "properties": {
        "contexts": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["passage", "context"],
                "properties": {
                    "passage": {"type": "integer", "description": "The passage number"},
                    "context": {
                        "type": "string",
                        "description": "One or two sentences situating the passage in the document",
                    },
                },
            },
        }
    },
}

TOO_SHORT: Final = "too_short"
WRONG_SCRIPT: Final = "wrong_script"
NEW_NUMBER: Final = "new_number"
NEW_NAME: Final = "new_name"
LINK: Final = "link"
MISSING: Final = "missing"
"""No context came back for the passage."""

_TELUGU: Final = re.compile(r"[ఀ-౿]")
_WS: Final = re.compile(r"\s+")
_DIGITS: Final = re.compile(r"\d+")
_WORD: Final = re.compile(r"\w+")
_CAPITALISED: Final = re.compile(r"(?<![A-Za-z])[A-Z][A-Za-z]*")
_LINK: Final = re.compile(r"(?i)(?:https?://|ftp://|www\.)|[\w.+-]+@[\w-]+\.[\w.]+")
_PASSAGE: Final = re.compile(r'<passage n="(\d+)">\n(.*?)\n</passage>', re.DOTALL)
_DOCUMENT: Final = re.compile(
    r"<document>\nTitle: (?P<title>[^\n]*)\nType: (?P<type>[^\n]*)\n(?P<body>.*?)\n</document>",
    re.DOTALL,
)


_BLOCK_TAG: Final = re.compile(r"<(\s*/?\s*(?:document|passage)\b)", re.IGNORECASE)
"""A ``<document>`` / ``<passage>`` tag (opening or closing, any case or spacing) in text."""


def escape_tags(text: str) -> str:
    """Text placed inside the prompt's ``<document>`` or ``<passage>`` blocks, with any such tag
    it writes escaped (``<`` becomes ``&lt;``), so the text cannot close its block and add words
    that read as instructions outside it (audit 2026-10-04 hardening)."""
    return _BLOCK_TAG.sub(r"&lt;\1", text)


@dataclass(frozen=True, slots=True)
class Passage:
    number: int
    """1-based within one call."""
    text: str
    language: Locale | None = None


@dataclass(frozen=True, slots=True)
class Checked:
    context: str | None
    """The cleaned, redacted context; None when rejected."""
    reason: str | None = None
    """Why it was rejected (a code, never text)."""


def _one_line(text: str) -> str:
    return _WS.sub(" ", unicodedata.normalize("NFC", text)).strip()


def document_text(document: ExtractedDocument) -> str:
    """The document as the model reads it: one block per line, headings marked with ``#``."""
    lines: list[str] = []
    for page in document.pages:
        for block in page.blocks:
            if block.kind == "page_break":
                continue
            text = _one_line(block.text)
            if not text:
                continue
            if block.kind == "heading":
                text = "#" * max(1, min(block.level or 1, 6)) + " " + text
            elif block.kind == "list_item":
                text = "- " + text
            lines.append(text)
    return "\n".join(lines)


def clip_document(text: str, max_chars: int) -> str:
    """At most ``max_chars``, cut at the last line break before the limit (office documents put
    the subject and headings first; the passages themselves are always sent whole)."""
    if len(text) <= max_chars:
        return text
    cut = text.rfind("\n", 0, max_chars)
    return text[: cut if cut > max_chars // 2 else max_chars].rstrip() + "\n[...]"


def passages_message(passages: Sequence[Passage]) -> str:
    """The user text of one call: the passages only, numbered, one line each."""
    body = "\n\n".join(
        f'<passage n="{p.number}">\n{escape_tags(_one_line(p.text))}\n</passage>'
        for p in passages
    )
    return f"Write one context for each passage below.\n\n{body}"


def parse_passages(message: str) -> list[tuple[int, str]]:
    """``(number, text)`` of each passage in :func:`passages_message` output (offline fake)."""
    return [(int(m.group(1)), m.group(2)) for m in _PASSAGE.finditer(message)]


def parse_document(system: str) -> tuple[str, str, str] | None:
    """``(title, doc_type, body)`` of the rendered prompt's document block (offline fake)."""
    m = _DOCUMENT.search(system)
    return (m["title"], m["type"], m["body"]) if m else None


def contexts_from_output(value: Mapping[str, object], numbers: Iterable[int]) -> dict[int, str]:
    """The model's context per asked passage number (the first one wins; others ignored)."""
    wanted = set(numbers)
    out: dict[int, str] = {}
    items = value.get("contexts")
    if not isinstance(items, list):
        return out
    for item in items:
        if not isinstance(item, Mapping):
            continue
        number, context = item.get("passage"), item.get("context")
        fresh = isinstance(number, int) and number in wanted and number not in out
        if fresh and isinstance(number, int) and isinstance(context, str):
            out[number] = context
    return out


def _numbers(text: str) -> set[str]:
    return {str(int(run)) for run in _DIGITS.findall(text)}


class ContextChecker:
    """The checks for one document: ``source`` is everything the model was given about it
    (title, issuer, type and text), the only place a context's facts may come from."""

    def __init__(self, config: ContextualConfig, source: str) -> None:
        self._config = config
        folded = unicodedata.normalize("NFC", source).casefold()
        self._words = frozenset(_WORD.findall(folded))
        self._numbers = _numbers(folded)

    def check(self, raw: str, language: Locale | None) -> Checked:
        text = self._cut(_one_line(raw))
        reason = self._problem(text, language)
        if reason is not None:
            return Checked(None, reason)
        cleaned = redact(text).strip()
        if len(cleaned) < self._config.min_chars:
            return Checked(None, TOO_SHORT)
        return Checked(cleaned)

    def _cut(self, text: str) -> str:
        """At most ``max_chars``, at a word boundary (a long context is shortened, not lost)."""
        limit = self._config.max_chars
        if len(text) <= limit:
            return text
        cut = text.rfind(" ", 0, limit + 1)
        return text[: cut if cut > limit // 2 else limit].rstrip(" ,;:-")

    def _problem(self, text: str, language: Locale | None) -> str | None:
        if len(text) < self._config.min_chars:
            return TOO_SHORT
        has_telugu = bool(_TELUGU.search(text))
        if (language == "te" and not has_telugu) or (language == "en" and has_telugu):
            return WRONG_SCRIPT
        if _LINK.search(text):
            return LINK
        if not _numbers(text) <= self._numbers:
            return NEW_NUMBER
        generic = self._config.generic_words
        for m in _CAPITALISED.finditer(text):
            word = m.group(0).casefold()
            if word not in generic and word not in self._words:
                return NEW_NAME
        return None


__all__ = [
    "LINK",
    "MISSING",
    "NEW_NAME",
    "NEW_NUMBER",
    "SCHEMA",
    "SCHEMA_TAG",
    "TOO_SHORT",
    "WRONG_SCRIPT",
    "Checked",
    "ContextChecker",
    "Passage",
    "clip_document",
    "contexts_from_output",
    "escape_tags",
    "document_text",
    "parse_document",
    "parse_passages",
    "passages_message",
]
