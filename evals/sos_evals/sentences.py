"""Per-sentence citation metrics (docs/06 §9 rule 3, §13.2). Pure functions, no I/O.

The harness's own reading of an answer, independent of the application's splitter (the harness
never imports application code): it splits more eagerly (no abbreviation list), which can only
count more sentences as needing a citation, never fewer.

- A sentence ends at ``.``, ``?``, ``!``, ``।``, ``॥`` or ``…`` followed by whitespace or the end,
  and at every line break. ``[n]`` passage markers are removed first (their digits are not
  facts); a sentence is cited when it overlaps an answer segment that carries a valid citation.
- A sentence is factual unless it is markup only, a short lead-in ending with ``:`` without
  digits, or says only that nothing was found.
- **Supported:** not factual, or cited AND every figure it writes (dates, times, amounts, other
  numbers of three or more digits) appears in the title, text or issue date of a source it
  validly cites. **High severity:** an unsupported sentence that writes a figure (a date, time,
  amount or number office staff would act on).
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date

MARKERS = re.compile(r"[ \t]*\[\s*\d{1,3}(?:\s*[,;]\s*\d{1,3})*\s*\]")
_END = re.compile(r"[.?!।॥…]+[\"')\]”’]*(?=\s|$)|\n")  # noqa: RUF001 (curly quotes)
_DIGIT = re.compile(r"\d")
_LIST = re.compile(r"^\s*(?:[-*+•]|\d{1,3}[.)])\s+")
_MARKUP = re.compile(r"[|*_`#>]")
_DATE = re.compile(r"(?<!\d)(\d{1,2})[/.-](\d{1,2})[/.-](\d{4}|\d{2})(?!\d)")
_TIME = re.compile(r"(?<!\d)(\d{1,2}):(\d{2})(?!\d)")
_NUMBER = re.compile(r"(?<!\d)(\d{1,3}(?:,\d{2,3})+|\d{3,})(?!\d)")
_TRUNCATED = re.compile(r"\S*…")
LEAD_IN_WORDS = 12
NOT_FOUND = (
    "not found in school records",
    "could not find",
    "couldn't find",
    "cannot find",
    "can't find",
)

Figure = tuple[str, int, int, int]


@dataclass(frozen=True, slots=True)
class Span:
    start: int
    end: int
    text: str


def split(text: str) -> list[Span]:
    """Sentences of ``text`` with their spans (whitespace trimmed, empty ones skipped)."""
    spans: list[Span] = []
    begin = 0
    for match in _END.finditer(text):
        _add(spans, text, begin, match.end())
        begin = match.end()
    _add(spans, text, begin, len(text))
    return spans


def _add(spans: list[Span], text: str, start: int, end: int) -> None:
    piece = text[start:end]
    stripped = piece.strip()
    if stripped:
        lead = len(piece) - len(piece.lstrip())
        spans.append(Span(start + lead, start + lead + len(stripped), stripped))


def words(sentence: str) -> str:
    """The sentence without list markers and markdown markup."""
    return " ".join(_MARKUP.sub(" ", _LIST.sub("", sentence)).split())


def is_factual(sentence: str) -> bool:
    text = words(sentence)
    if not any(ch.isalnum() for ch in text):
        return False
    if _DIGIT.search(text):
        return True
    folded = text.casefold()
    if any(phrase in folded for phrase in NOT_FOUND):
        return False
    return not (folded.endswith(":") and len(folded.split()) <= LEAD_IN_WORDS)


def figures(text: str) -> frozenset[Figure]:
    """Dates (day, month, year), times, and numbers of three or more digits (Indian grouping
    read as one number) that ``text`` writes. A token cut short by ``…`` is ignored."""
    text = _TRUNCATED.sub(" ", unicodedata.normalize("NFKC", text))
    found: set[Figure] = set()
    for match in _DATE.finditer(text):
        day, month, year = (int(g) for g in match.groups())
        found.add(("date", day, month, year + 2000 if year < 100 else year))
    text = _DATE.sub(" ", text)
    for match in _TIME.finditer(text):
        found.add(("time", int(match.group(1)), int(match.group(2)), 0))
    text = _TIME.sub(" ", text)
    for match in _NUMBER.finditer(text):
        found.add(("number", int(match.group(1).replace(",", "")), 0, 0))
    return frozenset(found)


def date_figure(value: date) -> Figure:
    return ("date", value.day, value.month, value.year)


@dataclass(frozen=True, slots=True)
class SentenceScore:
    text: str
    factual: bool
    cited: bool
    supported: bool
    high_severity: bool


def score(
    segments: Sequence[tuple[str, Iterable[frozenset[Figure]]]],
) -> list[SentenceScore]:
    """Score an answer. ``segments``: each segment's text and, for each of its VALID citations,
    the figures its source writes (an empty iterable: no valid citation)."""
    joined = ""
    spans: list[tuple[int, int, list[frozenset[Figure]]]] = []
    for text, pools in segments:
        clean = MARKERS.sub("", text)
        if joined and clean and not joined[-1].isspace() and not clean[0].isspace():
            joined += " "
        spans.append((len(joined), len(joined) + len(clean), list(pools)))
        joined += clean
    scores: list[SentenceScore] = []
    for sentence in split(joined):
        pools = [
            pool
            for start, end, seg_pools in spans
            if max(start, sentence.start) < min(end, sentence.end)
            for pool in seg_pools
        ]
        factual = is_factual(sentence.text)
        cited = bool(pools)
        stated = figures(words(sentence.text))
        available = frozenset().union(*pools) if pools else frozenset()
        supported = not factual or (cited and stated <= available)
        scores.append(
            SentenceScore(
                sentence.text,
                factual,
                cited,
                supported,
                high_severity=not supported and bool(_DIGIT.search(words(sentence.text))),
            )
        )
    return scores


__all__ = [
    "MARKERS",
    "SentenceScore",
    "Span",
    "date_figure",
    "figures",
    "is_factual",
    "score",
    "split",
    "words",
]
