"""Circular reading evaluation (M4; docs/06 §13.3; FR-CIR-002, FR-CIR-003, FR-CIR-008).

A second, small question set: synthetic circulars (English, Telugu script, code-mixed) with the
deadlines a careful office clerk would write down. The system under test reads each circular and
returns deadline suggestions, each quoting the sentence it comes from. Scoring:

- **deadline recall**: expected deadlines (by date) found / expected (exit criterion of 14 · M4:
  deadlines captured for >= 90 % of circulars; gate >= 0.90);
- **deadline precision**: suggestions whose date is an expected deadline / suggestions;
- **citation validity**: suggestions whose quote is part of the circular AND writes the due date
  / suggestions (docs/06 §9 rules 1-2 applied to readings);
- **hallucinated deadlines**: suggestions whose date is written nowhere in the circular (hard 0);
- **complete rate**: circulars with every expected deadline found / circulars;
- **metadata accuracy**: reference number and issue date right, where the circular has them.

The harness has its own date reader (below) so it never trusts the application's. Pure: no I/O.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Sequence
from datetime import date
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from sos_evals.schema import Locale

_WS = re.compile(r"\s+")
_EN_MONTHS = {
    m: i + 1
    for i, m in enumerate(
        (
            "january",
            "february",
            "march",
            "april",
            "may",
            "june",
            "july",
            "august",
            "september",
            "october",
            "november",
            "december",
        )
    )
}
_TE_MONTHS = {
    "జనవరి": 1,
    "ఫిబ్రవరి": 2,
    "మార్చి": 3,
    "ఏప్రిల్": 4,
    "మే": 5,
    "జూన్": 6,
    "జూలై": 7,
    "ఆగస్టు": 8,
    "సెప్టెంబర్": 9,
    "అక్టోబర్": 10,
    "నవంబర్": 11,
    "డిసెంబర్": 12,
}
_NUMERIC = re.compile(r"(?<!\d)(\d{1,2})[./-](\d{1,2})[./-](\d{4})(?!\d)")
_WORDS = re.compile(
    r"(?<!\d)(\d{1,2})(?:st|nd|rd|th)?\s+("
    + "|".join(_EN_MONTHS)
    + r"|"
    + "|".join(_TE_MONTHS)
    + r")\s*,?\s*(\d{4})",
    re.IGNORECASE,
)
_WORDS_MONTH_FIRST = re.compile(
    r"\b(" + "|".join(_EN_MONTHS) + r")\s+(\d{1,2})(?:st|nd|rd|th)?,?\s*(\d{4})",
    re.IGNORECASE,
)


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class CircularCase(_Model):
    """One synthetic circular and what a careful reader writes down from it."""

    id: str = Field(pattern=r"^circ-[a-z0-9-]{2,60}$")
    locale: Locale
    title: str
    lines: tuple[str, ...] = Field(min_length=1)
    expected_deadlines: tuple[date, ...]
    reference_no: str | None = None
    issued_on: date | None = None
    note: str = ""
    """Why the case exists (distractor dates, format, language)."""

    @property
    def text(self) -> str:
        return "\n".join(self.lines)


class SuggestedDeadline(_Model):
    due_on: date
    quote: str
    title: str = ""
    details: str | None = None


class CircularResult(_Model):
    deadlines: tuple[SuggestedDeadline, ...] = ()
    reference_no: str | None = None
    issued_on: date | None = None
    issuer: str | None = None
    subject: str | None = None
    summary_en: str | None = None
    summary_te: str | None = None
    failed: bool = False
    """The system could not read it (manual review): counts as nothing found."""


class CircularAdapter(Protocol):
    name: str

    def read_circular(self, case: CircularCase) -> CircularResult: ...


def normalize(text: str) -> str:
    return _WS.sub(" ", unicodedata.normalize("NFC", text)).strip().casefold()


def dates_in(text: str) -> set[date]:
    """Dates written in ``text`` (the dataset's formats: DD/MM/YYYY-style and day month year)."""
    found: set[date] = set()
    for day, month, year in _NUMERIC.findall(text):
        found |= _date(int(year), int(month), int(day))
    for day, month, year in _WORDS.findall(text):
        number = _EN_MONTHS.get(month.casefold()) or _TE_MONTHS.get(month)
        if number:
            found |= _date(int(year), number, int(day))
    for month, day, year in _WORDS_MONTH_FIRST.findall(text):
        found |= _date(int(year), _EN_MONTHS[month.casefold()], int(day))
    return found


def _date(year: int, month: int, day: int) -> set[date]:
    try:
        return {date(year, month, day)}
    except ValueError:
        return set()


class CircularOutcome(_Model):
    id: str
    locale: Locale
    expected: int
    found: int
    suggested: int
    correct: int
    valid_citations: int
    hallucinated: int
    complete: bool
    metadata_checked: int
    metadata_right: int
    failed: bool


def score(case: CircularCase, result: CircularResult) -> CircularOutcome:
    expected = set(case.expected_deadlines)
    written = dates_in(case.text)
    body = normalize(case.text)
    suggested = [] if result.failed else list(result.deadlines)
    got = {d.due_on for d in suggested}
    valid = sum(
        1 for d in suggested if normalize(d.quote) in body and d.due_on in dates_in(d.quote)
    )
    checked = right = 0
    if case.reference_no is not None:
        checked += 1
        right += result.reference_no is not None and normalize(result.reference_no) == normalize(
            case.reference_no
        )
    if case.issued_on is not None:
        checked += 1
        right += result.issued_on == case.issued_on
    return CircularOutcome(
        id=case.id,
        locale=case.locale,
        expected=len(expected),
        found=len(expected & got),
        suggested=len(suggested),
        correct=sum(1 for d in suggested if d.due_on in expected),
        valid_citations=valid,
        hallucinated=sum(1 for d in suggested if d.due_on not in written),
        complete=expected <= got,
        metadata_checked=checked,
        metadata_right=right,
        failed=result.failed,
    )


class CircularMetrics(_Model):
    circular_items: int
    circular_deadline_recall: float | None
    circular_deadline_precision: float | None
    circular_citation_validity: float | None
    circular_hallucinated_deadlines: int | None
    circular_complete_rate: float | None
    circular_metadata_accuracy: float | None


def _ratio(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def aggregate(outcomes: Sequence[CircularOutcome]) -> CircularMetrics:
    if not outcomes:
        return CircularMetrics(
            circular_items=0,
            circular_deadline_recall=None,
            circular_deadline_precision=None,
            circular_citation_validity=None,
            circular_hallucinated_deadlines=None,
            circular_complete_rate=None,
            circular_metadata_accuracy=None,
        )
    suggested = sum(o.suggested for o in outcomes)
    return CircularMetrics(
        circular_items=len(outcomes),
        circular_deadline_recall=_ratio(
            sum(o.found for o in outcomes), sum(o.expected for o in outcomes)
        ),
        circular_deadline_precision=_ratio(sum(o.correct for o in outcomes), suggested),
        circular_citation_validity=_ratio(sum(o.valid_citations for o in outcomes), suggested),
        circular_hallucinated_deadlines=sum(o.hallucinated for o in outcomes),
        circular_complete_rate=_ratio(sum(1 for o in outcomes if o.complete), len(outcomes)),
        circular_metadata_accuracy=_ratio(
            sum(o.metadata_right for o in outcomes), sum(o.metadata_checked for o in outcomes)
        ),
    )


def run(
    cases: Iterable[CircularCase], adapter: CircularAdapter
) -> tuple[CircularMetrics, tuple[CircularOutcome, ...]]:
    outcomes = tuple(score(case, adapter.read_circular(case)) for case in cases)
    return aggregate(outcomes), outcomes


def validate_cases(cases: Sequence[CircularCase]) -> None:
    """Every expected deadline is written in its circular (a wrong key would hide a miss)."""
    seen: set[str] = set()
    for case in cases:
        if case.id in seen:
            raise ValueError(f"duplicate circular case {case.id}")
        seen.add(case.id)
        written = dates_in(case.text)
        missing = sorted(d.isoformat() for d in set(case.expected_deadlines) - written)
        if missing:
            raise ValueError(f"{case.id}: expected deadlines not written in it: {missing}")
        if case.issued_on is not None and case.issued_on not in written:
            raise ValueError(f"{case.id}: issued_on is not written in it")
        if case.reference_no is not None and normalize(case.reference_no) not in normalize(
            case.text
        ):
            raise ValueError(f"{case.id}: reference_no is not written in it")


__all__ = [
    "CircularAdapter",
    "CircularCase",
    "CircularMetrics",
    "CircularOutcome",
    "CircularResult",
    "SuggestedDeadline",
    "aggregate",
    "dates_in",
    "normalize",
    "run",
    "score",
    "validate_cases",
]
