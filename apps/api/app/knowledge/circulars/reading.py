"""Circular reading: request text, strict JSON schema and server-side validation (FR-CIR-002/003).

The model receives only one circular version's indexed passages (already Aadhaar-masked by
ingestion), each numbered ``[n]`` with its page, plus the metadata the office typed. It returns
metadata, a short English and Telugu summary and deadline suggestions, each citing a passage
number and quoting it. Structured outputs cannot carry Messages API citations (they are mutually
exclusive), so citations are passage numbers plus a quote, and this module checks them itself
(docs/06 §9 rules 1-2 applied to JSON):

- a deadline is kept only when its passage exists, its quote (NFC, case- and
  whitespace-insensitive) is part of that passage, and the quote itself writes the due date
  (:func:`.dates.mentions_date`); everything else is dropped and counted, never stored;
- issuer, reference number and subject are kept only when they appear in the passages, the
  issue date only when a passage writes it; otherwise they stay empty (never guessed, §4.4);
- the Telugu summary must use Telugu script and the English one must not;
- values are cut to the configured lengths, duplicates (same date and title) dropped.

Nothing here logs or raises with text from the circular or the model (invariant 5).
"""

from __future__ import annotations

import datetime as dt
import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final

from app.core.redaction import mask_aadhaar
from app.knowledge.circulars.dates import mentions_date, parse_iso
from app.knowledge.config.circulars import ReadingConfig

SCHEMA_TAG: Final = "sos:circular_reading.v1"
"""The schema's ``description``: names the request for the offline fake provider."""

_TELUGU: Final = re.compile(r"[ఀ-౿]")
_WS: Final = re.compile(r"\s+")


def _nullable_string(description: str) -> dict[str, Any]:
    return {"anyOf": [{"type": "string"}, {"type": "null"}], "description": description}


SCHEMA: Final[dict[str, Any]] = {
    "type": "object",
    "description": SCHEMA_TAG,
    "additionalProperties": False,
    "required": [
        "issuer",
        "reference_no",
        "issued_on",
        "subject",
        "summary_en",
        "summary_te",
        "summary_passages",
        "deadlines",
    ],
    "properties": {
        "issuer": _nullable_string("Office that issued the circular, exactly as written"),
        "reference_no": _nullable_string("Reference number exactly as written"),
        "issued_on": _nullable_string("Date of the circular as YYYY-MM-DD"),
        "subject": _nullable_string("Subject line exactly as written"),
        "summary_en": {"type": "string", "description": "Two or three plain English sentences"},
        "summary_te": {"type": "string", "description": "The same summary in Telugu script"},
        "summary_passages": {
            "type": "array",
            "items": {"type": "integer"},
            "description": "Numbers of the passages the summary is based on",
        },
        "deadlines": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["title", "details", "due_on", "passage", "quote"],
                "properties": {
                    "title": {"type": "string", "description": "What the school must do"},
                    "details": _nullable_string("Who, where or how, if the passage says"),
                    "due_on": {"type": "string", "description": "YYYY-MM-DD, written in quote"},
                    "passage": {"type": "integer", "description": "The passage number"},
                    "quote": {
                        "type": "string",
                        "description": "The exact sentence of that passage that gives the date",
                    },
                },
            },
        },
    },
}


@dataclass(frozen=True, slots=True)
class Passage:
    """One indexed chunk of the circular version, as sent to the model."""

    number: int
    """1-based position in the request (``[n]``)."""
    chunk_no: int
    page: int | None
    source: str
    """``sos://doc/{id}/v{n}#p{page}`` (docs/06 §8)."""
    text: str


@dataclass(frozen=True, slots=True)
class CircularContext:
    """What the office typed when uploading (shown to the model as a hint, never trusted)."""

    title: str
    issuer: str | None
    issued_on: dt.date | None


@dataclass(frozen=True, slots=True)
class PassageCitation:
    source: str
    passage: int
    chunk_no: int
    page: int | None
    quote: str


@dataclass(frozen=True, slots=True)
class DeadlineSuggestion:
    title: str
    details: str | None
    due_on: dt.date
    citation: PassageCitation


@dataclass(frozen=True, slots=True)
class CircularReading:
    issuer: str | None
    reference_no: str | None
    issued_on: dt.date | None
    subject: str | None
    summary_en: str | None
    summary_te: str | None
    summary_sources: tuple[PassageCitation, ...]
    deadlines: tuple[DeadlineSuggestion, ...]
    dropped: int
    """Deadline suggestions refused by validation (counted for audit and evals)."""
    passages_sent: int
    passages_total: int


@dataclass(frozen=True, slots=True)
class ReadingRequest:
    text: str
    passages: tuple[Passage, ...]
    """The passages actually sent (a long circular is cut to the configured limits)."""
    passages_total: int


def normalise(text: str) -> str:
    """NFC, case-folded, whitespace collapsed (for substring checks)."""
    return _WS.sub(" ", unicodedata.normalize("NFC", text)).strip().casefold()


def _cut(text: str, limit: int) -> str:
    text = _WS.sub(" ", unicodedata.normalize("NFC", text)).strip()
    if len(text) <= limit:
        return text
    # Within ``limit`` with the "…": at the last space (at most limit - 1 characters before
    # it), else mid-word.
    window = text[:limit]
    head = window.rsplit(" ", 1)[0] if " " in window else ""
    return (head.rstrip(" ,;:") or window[: limit - 1]) + "…"


def _one_line(text: str) -> str:
    """A passage on one line (each ``[n]`` line is one passage; spacing is not significant for
    the quote check, which collapses whitespace)."""
    return " ".join(unicodedata.normalize("NFC", text).split())


def _date_ddmmyyyy(value: dt.date | None) -> str:
    return value.strftime("%d/%m/%Y") if value else "(not entered)"


def build_request(
    context: CircularContext, passages: Sequence[Passage], config: ReadingConfig
) -> ReadingRequest:
    """The user message: the office's metadata, then the numbered passages (within limits)."""
    head = (
        "Circular details typed by the school office (may be incomplete):\n"
        f"Title: {context.title or '(not entered)'}\n"
        f"Issued by: {context.issuer or '(not entered)'}\n"
        f"Date: {_date_ddmmyyyy(context.issued_on)}\n\n"
        "Passages of the circular, in order. Each starts with its number:\n"
    )
    budget = config.max_input_chars - len(head)
    sent: list[Passage] = []
    lines: list[str] = []
    for passage in passages[: config.max_passages]:
        page = f" (page {passage.page})" if passage.page else ""
        line = f"[{len(sent) + 1}]{page} {_one_line(passage.text)}\n"
        if len(line) > budget:
            break
        budget -= len(line)
        sent.append(
            Passage(
                number=len(sent) + 1,
                chunk_no=passage.chunk_no,
                page=passage.page,
                source=passage.source,
                text=passage.text,
            )
        )
        lines.append(line)
    return ReadingRequest(
        text=mask_aadhaar(head + "".join(lines)),
        passages=tuple(sent),
        passages_total=len(passages),
    )


def _string(raw: Mapping[str, object], key: str) -> str | None:
    value = raw.get(key)
    return value.strip() or None if isinstance(value, str) else None


def _grounded(value: str | None, corpus: str, limit: int) -> str | None:
    """``value`` only when the passages contain it (never guessed; docs/06 §4.4)."""
    if value is None or len(value) > limit or normalise(value) not in corpus:
        return None
    return _cut(value, limit)


def _summary(value: str | None, *, telugu: bool, limit: int) -> str | None:
    if value is None or bool(_TELUGU.search(value)) != telugu:
        return None
    return _cut(mask_aadhaar(value), limit)


def _citation(passage: Passage, quote: str) -> PassageCitation:
    return PassageCitation(
        source=passage.source,
        passage=passage.number,
        chunk_no=passage.chunk_no,
        page=passage.page,
        quote=quote,
    )


def _issued_on(raw: Mapping[str, object], passages: Sequence[Passage]) -> dt.date | None:
    value = _string(raw, "issued_on")
    when = parse_iso(value) if value else None
    if when is None or not any(mentions_date(p.text, when) for p in passages):
        return None
    return when


def _deadline(
    item: object, by_number: Mapping[int, Passage], config: ReadingConfig
) -> DeadlineSuggestion | None:
    if not isinstance(item, Mapping):
        return None
    passage = by_number.get(item.get("passage"))  # type: ignore[arg-type]
    quote, title = _string(item, "quote"), _string(item, "title")
    due_raw = _string(item, "due_on")
    due_on = parse_iso(due_raw) if due_raw else None
    if passage is None or quote is None or title is None or due_on is None:
        return None
    if len(quote) > config.max_quote_chars or normalise(quote) not in normalise(passage.text):
        return None
    if not mentions_date(quote, due_on):
        return None
    details = _string(item, "details")
    return DeadlineSuggestion(
        title=_cut(mask_aadhaar(title), config.max_title_chars),
        details=_cut(mask_aadhaar(details), config.max_details_chars) if details else None,
        due_on=due_on,
        citation=_citation(passage, _WS.sub(" ", unicodedata.normalize("NFC", quote)).strip()),
    )


def validate_reading(
    raw: Mapping[str, object], request: ReadingRequest, config: ReadingConfig
) -> CircularReading:
    """Keep only what the passages support (see the module docstring)."""
    passages = request.passages
    corpus = normalise(" ".join(p.text for p in passages))
    by_number = {p.number: p for p in passages}
    raw_deadlines = raw.get("deadlines")
    items = raw_deadlines if isinstance(raw_deadlines, list) else []
    kept: list[DeadlineSuggestion] = []
    seen: set[tuple[dt.date, str]] = set()
    dropped = 0
    for item in items:
        suggestion = _deadline(item, by_number, config)
        key = (suggestion.due_on, normalise(suggestion.title)) if suggestion else None
        if suggestion is None or key is None or key in seen or len(kept) >= config.max_deadlines:
            dropped += 1
            continue
        seen.add(key)
        kept.append(suggestion)
    raw_sources = raw.get("summary_passages")
    numbers = (
        [n for n in raw_sources if isinstance(n, int)] if isinstance(raw_sources, list) else []
    )
    sources = tuple(
        _citation(by_number[n], _cut(by_number[n].text, config.snippet_chars))
        for n in dict.fromkeys(numbers)
        if n in by_number
    )
    return CircularReading(
        issuer=_grounded(_string(raw, "issuer"), corpus, config.max_field_chars),
        reference_no=_grounded(_string(raw, "reference_no"), corpus, config.max_field_chars),
        issued_on=_issued_on(raw, passages),
        subject=_grounded(_string(raw, "subject"), corpus, config.max_field_chars),
        summary_en=_summary(
            _string(raw, "summary_en"), telugu=False, limit=config.max_summary_chars
        ),
        summary_te=_summary(
            _string(raw, "summary_te"), telugu=True, limit=config.max_summary_chars
        ),
        summary_sources=sources,
        deadlines=tuple(sorted(kept, key=lambda d: (d.due_on, d.citation.passage))),
        dropped=dropped,
        passages_sent=len(passages),
        passages_total=request.passages_total,
    )


__all__ = [
    "SCHEMA",
    "SCHEMA_TAG",
    "CircularContext",
    "CircularReading",
    "DeadlineSuggestion",
    "Passage",
    "PassageCitation",
    "ReadingRequest",
    "build_request",
    "normalise",
    "validate_reading",
]
