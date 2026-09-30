"""Provider-neutral citations: numbered passages and ``[n]`` markers (docs/06 §7, §9; ADR-0033).

The Anthropic Messages API cites ``search_result`` blocks natively. Gemini has no such block, so
for it the gateway numbers every block the caller handed over in this request (``[1]`` for the
first block of the first tool result, and so on across tool rounds; the numbering is derived
from the conversation, so every round of one question numbers the same passages the same way)
and the model writes ``[n]`` after each statement (``citations.marker_instructions`` in
``models.yaml``). :func:`segments_from_markers` turns the answer text back into
:class:`AnswerSegment` values with :class:`Citation` values, so ``knowledge.answer`` validates
them exactly like native citations (§9 rules 1-3: the source must be one of THIS request's blocks
and the cited text a substring of it; an answer without a valid citation says "not found").

Stricter than a bare marker (FR-KB-005, FR-KB-007):

- a marker that names no passage of this request is dropped (it cannot point at anything the
  caller may not see: only the caller's own blocks are numbered);
- with ``require_numbers_in_passage`` every number the statement writes (dates, amounts, counts,
  class numbers) must appear in the passages it cites (title or text; Roman class numerals in a
  passage count as their number), else all its markers are dropped and the
  statement counts as uncited (so an answer built on an invented date falls back to search-only
  or "not found");
- ``cited_text`` is the part of the passage that supports the statement (the sentences that write
  its numbers, fewest first, else the sentence sharing most words with it), copied from
  the passage (always a substring of it), so the source chip shows the supporting text.

:class:`MarkerStripper` removes markers from streamed preview text (the final answer is
renumbered by ``knowledge.answer.cited_sources``).
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

from app.knowledge.domain import (
    AnswerSegment,
    Citation,
    ConversationItem,
    SearchResultBlock,
    ToolResultsMessage,
)

MARKER: Final = re.compile(r"\[\s*(\d{1,3}(?:\s*[,;]\s*\d{1,3})*)\s*\]")
"""``[3]``, ``[2, 5]``; consecutive markers (``[2][5]``) are read together."""
_PARTIAL: Final = re.compile(r"[ \t]?\[[\d\s,;]{0,40}$")
"""An unfinished marker at the end of streamed text (held back with the space before it)."""
_STRIP: Final = re.compile(r"[ \t]?" + MARKER.pattern)
"""A marker with the one space written before it (stream preview only)."""
_TRAILING_PUNCT: Final = re.compile(r"^[.,;:!?।)\]]+")
_SENTENCE: Final = re.compile(r"(?:[^\n.!?।]|[.!?।](?![\s)]|$))*(?:[.!?।]+(?=[\s)]|$)|\n|$)")
"""A sentence ends at ``.``, ``!``, ``?`` or ``।`` followed by a space or the end (so ``12,500.00``
and ``Rc.No.12`` stay whole), or at a line break."""
_WORD: Final = re.compile(r"[^\W_]+", re.UNICODE)
_DIGITS: Final = re.compile(r"\d+(?:,\d{2,3})*")
_ROMAN: Final = re.compile(r"\b(XII|XI|X|IX|VIII|VII|VI|V|IV|III|II|I)\b")
_ROMAN_VALUES: Final = {
    "I": 1,
    "II": 2,
    "III": 3,
    "IV": 4,
    "V": 5,
    "VI": 6,
    "VII": 7,
    "VIII": 8,
    "IX": 9,
    "X": 10,
    "XI": 11,
    "XII": 12,
}


@dataclass(frozen=True, slots=True)
class Passage:
    """One block given to the model in this request, with its 1-based marker number."""

    number: int
    block: SearchResultBlock


def number_passages(conversation: Sequence[ConversationItem]) -> tuple[Passage, ...]:
    """Every block of every tool result in conversation order, numbered from 1."""
    blocks = [
        b
        for item in conversation
        if isinstance(item, ToolResultsMessage)
        for outcome in item.outcomes
        for b in outcome.blocks
    ]
    return tuple(Passage(i, b) for i, b in enumerate(blocks, start=1))


def numbers_in(text: str, *, roman: bool = False) -> frozenset[int]:
    """The numbers a text writes (Indian digit grouping ``1,25,000`` read as one number; any
    Unicode digits; leading zeros ignored). ``roman`` also reads class numerals ``IX``, ``X``."""
    found: set[int] = set()
    for match in _DIGITS.finditer(unicodedata.normalize("NFKC", text)):
        try:
            found.add(int(match.group(0).replace(",", "")))
        except ValueError:
            continue
    if roman:
        found.update(_ROMAN_VALUES[m.group(1)] for m in _ROMAN.finditer(text))
    return frozenset(found)


def _words(text: str) -> set[str]:
    return {w.casefold() for w in _WORD.findall(unicodedata.normalize("NFC", text))}


def supporting_sentence(claim: str, passage: str) -> str:
    """The part of the passage that supports the claim, always an exact substring of it.

    When the claim writes numbers: the passage from the first to the last of the fewest sentences
    that write them (so every figure the claim states is inside the cited text). Otherwise the
    sentence sharing most words with the claim (the first one on a tie)."""
    claim_words, claim_numbers = _words(claim), numbers_in(claim)
    sentences = [m for m in _SENTENCE.finditer(passage) if m.group(0).strip()]
    numbers = [numbers_in(m.group(0), roman=True) for m in sentences]
    # Fewest sentences that write the claim's numbers (greedy cover, earliest on a tie), so a
    # sentence that shares only a stray month or year with the claim is not pulled in.
    chosen: list[int] = []
    remaining = set(claim_numbers)
    while remaining and sentences:
        best = max(range(len(sentences)), key=lambda i: (len(numbers[i] & remaining), -i))
        if not numbers[best] & remaining:
            break
        chosen.append(best)
        remaining -= numbers[best]
    if chosen:
        first, last = sentences[min(chosen)], sentences[max(chosen)]
        return passage[first.start() : last.end()].strip()
    best, best_score = "", -1
    for match in sentences:
        sentence = match.group(0).strip()
        score = len(claim_words & _words(sentence))
        if score > best_score:
            best, best_score = sentence, score
    return best or passage.strip()


@dataclass(frozen=True, slots=True)
class MarkedText:
    segments: tuple[AnswerSegment, ...]
    dropped: int
    """Markers that named no passage of this request or were not supported by it."""


def _split(text: str) -> list[tuple[str, list[int]]]:
    """Text pieces, each with the marker numbers written right after it."""
    pieces: list[tuple[str, list[int]]] = []
    pos = 0
    for match in MARKER.finditer(text):
        before = text[pos : match.start()]
        numbers = [int(n) for n in re.split(r"\s*[,;]\s*", match.group(1).strip())]
        if pieces and not before.strip() and pieces[-1][1]:
            pieces[-1][1].extend(numbers)  # "[2][5]" or "[2] [5]": one group
        else:
            pieces.append((before, numbers))
        pos = match.end()
    pieces.append((text[pos:], []))
    # Punctuation written after a marker ("... 2026 [1].") belongs to the cited statement.
    fixed: list[tuple[str, list[int]]] = []
    for piece, numbers in pieces:
        rest = piece
        if fixed and fixed[-1][1]:
            punct = _TRAILING_PUNCT.match(piece)
            if punct:
                prev_text, prev_numbers = fixed[-1]
                fixed[-1] = (prev_text.rstrip() + punct.group(0), prev_numbers)
                rest = piece[punct.end() :]
        fixed.append((rest, numbers))
    return fixed


def segments_from_markers(
    text: str, passages: Sequence[Passage], *, require_numbers: bool
) -> MarkedText:
    by_number = {p.number: p for p in passages}
    segments: list[AnswerSegment] = []
    dropped = 0
    for piece, numbers in _split(text):
        claim = piece.rstrip() if numbers else piece
        if not numbers:
            if claim.strip():
                segments.append(AnswerSegment(claim))
            continue
        cited = [by_number[n] for n in dict.fromkeys(numbers) if n in by_number]
        dropped += len(numbers) - len(cited)
        if require_numbers and cited:
            available = frozenset().union(
                *(numbers_in(f"{p.block.title}\n{p.block.text}", roman=True) for p in cited)
            )
            if not numbers_in(claim) <= available:
                dropped += len(cited)
                cited = []
        citations: list[Citation] = []
        seen: set[str] = set()
        for passage in cited:
            if passage.block.source in seen:
                continue
            seen.add(passage.block.source)
            citations.append(
                Citation(passage.block.source, supporting_sentence(claim, passage.block.text))
            )
        if claim.strip() or citations:
            segments.append(AnswerSegment(claim, tuple(citations)))
    return MarkedText(tuple(segments), dropped)


class MarkerStripper:
    """Removes ``[n]`` markers from streamed text, holding back a marker cut between pieces."""

    def __init__(self) -> None:
        self._held = ""

    def feed(self, text: str) -> str:
        buffer = self._held + text
        partial = _PARTIAL.search(buffer)
        cut = partial.start() if partial else len(buffer)
        self._held = buffer[cut:]
        return _STRIP.sub("", buffer[:cut])

    def flush(self) -> str:
        held, self._held = self._held, ""
        return _STRIP.sub("", held)


__all__ = [
    "MARKER",
    "MarkedText",
    "MarkerStripper",
    "Passage",
    "number_passages",
    "numbers_in",
    "segments_from_markers",
    "supporting_sentence",
]
