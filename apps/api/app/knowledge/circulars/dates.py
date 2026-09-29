"""Date mentions in circular text: numeric, English and Telugu month names (FR-CIR-003).

Pure and deterministic. Indian circulars write dates day first (``15/10/2026``, ``15-10-2026``,
``15.10.2026``), in words (``15th October 2026``, ``October 15, 2026``) or with Telugu month names
(``15 అక్టోబర్ 2026``, ``అక్టోబర్ 15న``). A mention without a year matches any year.

:func:`mentions_date` is the grounding check for deadline suggestions: a suggested due date is
kept only when the quoted sentence itself writes that date, so the model can never add a date the
circular does not contain.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass
from typing import Final

_EN_MONTHS: Final = {
    "january": 1,
    "jan": 1,
    "february": 2,
    "feb": 2,
    "march": 3,
    "mar": 3,
    "april": 4,
    "apr": 4,
    "may": 5,
    "june": 6,
    "jun": 6,
    "july": 7,
    "jul": 7,
    "august": 8,
    "aug": 8,
    "september": 9,
    "sept": 9,
    "sep": 9,
    "october": 10,
    "oct": 10,
    "november": 11,
    "nov": 11,
    "december": 12,
    "dec": 12,
}
_TE_MONTHS: Final = {
    "జనవరి": 1,
    "ఫిబ్రవరి": 2,
    "మార్చి": 3,
    "ఏప్రిల్": 4,
    "మే": 5,
    "జూన్": 6,
    "జూలై": 7,
    "జులై": 7,
    "ఆగస్టు": 8,
    "ఆగష్టు": 8,
    "సెప్టెంబర్": 9,
    "సెప్టెంబరు": 9,
    "అక్టోబర్": 10,
    "అక్టోబరు": 10,
    "నవంబర్": 11,
    "నవంబరు": 11,
    "డిసెంబర్": 12,
    "డిసెంబరు": 12,
}


def _alternation(names: dict[str, int]) -> str:
    return "|".join(re.escape(n) for n in sorted(names, key=len, reverse=True))


_EN: Final = _alternation(_EN_MONTHS)
_TE: Final = _alternation(_TE_MONTHS)
_ORD: Final = r"(?:st|nd|rd|th)?"
_YEAR: Final = r"(?P<year>(?:19|20)\d{2})"

_NUMERIC: Final = re.compile(
    r"(?<![\d/.\-])(?P<day>\d{1,2})\s?(?P<sep>[./\-])\s?(?P<month>\d{1,2})\s?(?P=sep)\s?"
    r"(?P<year>\d{4}|\d{2})(?!\d)"
)
_ISO: Final = re.compile(r"(?<!\d)(?P<year>\d{4})-(?P<month>\d{2})-(?P<day>\d{2})(?!\d)")
_DAY_MONTH_EN: Final = re.compile(
    rf"(?<![\w])(?P<day>\d{{1,2}}){_ORD}\s*(?:of\s+)?(?P<month>{_EN})\b\.?,?\s*{_YEAR}?",
    re.IGNORECASE,
)
_MONTH_DAY_EN: Final = re.compile(
    rf"(?<![\w])(?P<month>{_EN})\b\.?\s*(?P<day>\d{{1,2}}){_ORD}(?!\d)(?:,?\s*{_YEAR})?",
    re.IGNORECASE,
)
# "15 అక్టోబర్ 2026", "15వ తేదీ అక్టోబర్", "అక్టోబర్ 15న", "అక్టోబర్ 15, 2026"
_DAY_MONTH_TE: Final = re.compile(
    rf"(?<!\d)(?P<day>\d{{1,2}})\s*(?:వ)?\s*(?:తేదీ)?\s*(?P<month>{_TE})\s*,?\s*{_YEAR}?"
)
_MONTH_DAY_TE: Final = re.compile(
    rf"(?P<month>{_TE})\s*(?P<day>\d{{1,2}})(?!\d)(?:\s*(?:వ|న)?\s*(?:తేదీ)?\s*,?\s*{_YEAR})?"
)
_PATTERNS: Final = (_ISO, _NUMERIC, _DAY_MONTH_EN, _MONTH_DAY_EN, _DAY_MONTH_TE, _MONTH_DAY_TE)


@dataclass(frozen=True, slots=True)
class DateMention:
    """One date written in the text. ``year`` is None when the text gives none."""

    start: int
    end: int
    day: int
    month: int
    year: int | None

    def matches(self, when: dt.date) -> bool:
        return (self.day, self.month) == (when.day, when.month) and self.year in (None, when.year)


def _month(value: str) -> int | None:
    if value.isdigit():
        return int(value)
    return _EN_MONTHS.get(value.casefold()) or _TE_MONTHS.get(value)


def _year(value: str | None) -> int | None:
    if not value:
        return None
    year = int(value)
    return 2000 + year if len(value) == 2 else year


def _valid(day: int, month: int, year: int | None) -> bool:
    try:
        dt.date(year or 2024, month, day)  # 2024: a leap year, so 29/02 without a year is valid
    except ValueError:
        return False
    return year is None or 1990 <= year <= 2100


def find_dates(text: str) -> list[DateMention]:
    """Every date mention in ``text``, in order of position; overlapping readings of the same
    characters are reported once (the longest wins)."""
    found: list[DateMention] = []
    for pattern in _PATTERNS:
        for m in pattern.finditer(text):
            month = _month(m["month"])
            day = int(m["day"])
            year = _year(m.groupdict().get("year"))
            if month is None or not _valid(day, month, year):
                continue
            found.append(DateMention(m.start(), m.end(), day, month, year))
    found.sort(key=lambda d: (d.start, -(d.end - d.start)))
    kept: list[DateMention] = []
    for mention in found:
        if kept and mention.start < kept[-1].end:
            continue
        kept.append(mention)
    return kept


def mentions_date(text: str, when: dt.date) -> bool:
    """True when ``text`` writes ``when`` (a mention without a year matches any year)."""
    return any(m.matches(when) for m in find_dates(text))


def parse_iso(value: str) -> dt.date | None:
    """``YYYY-MM-DD`` (what the schema asks the model for), else None."""
    value = value.strip()
    if len(value) != 10:
        return None
    try:
        return dt.date.fromisoformat(value)
    except ValueError:
        return None


__all__ = ["DateMention", "find_dates", "mentions_date", "parse_iso"]
