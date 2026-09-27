"""Cell value conversion for imports (FR-IMP-003). Pure.

- Text: NFC, whitespace collapsed; whole-number floats (``1001.0``) become ``1001``.
- Dates: ``DD/MM/YYYY``, ``DD-MM-YYYY``, ``DD.MM.YYYY``, ``YYYY-MM-DD``, ``14-Mar-2012``,
  ``14 March 2012``, date cells and Excel serial numbers (1900 date system). Day-first is the
  Indian convention: a value such as ``03/04/2012`` is read as 3 April and flagged
  ``ambiguous_date`` unless the same column proves the order (some first part above 12).
  Two-digit years are refused (the century is a guess).
- Classes: codes, Roman/Arabic numbers, words and Telugu forms (``9``, ``IX``, ``9th``,
  ``Class 9``, ``9వ తరగతి``) and combined class-section cells (``9-A``, ``IX A``, ``9A``).
"""

from __future__ import annotations

import datetime as dt
import re
import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Final

from app.imports.sheet import CellValue

EXCEL_EPOCH: Final = dt.date(1899, 12, 30)
SERIAL_MIN: Final = 10_000  # 1927-05-18: smaller numbers are not dates of birth/admission
SERIAL_MAX: Final = 73_050  # 2099-12-31
_NUMERIC_DATE_RE: Final = re.compile(r"^(\d{1,4})[/.\-\s](\d{1,2})[/.\-\s](\d{1,4})$")
_TEXT_MONTH_RE: Final = re.compile(r"^(\d{1,2})[\s\-/.]+([^\d\s\-/.]+)[\s\-/.,]+(\d{2,4})$")
_WS_RE: Final = re.compile(r"\s+")
_CONTROL_RE: Final = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_MONTHS: Final = {
    name: i
    for i, names in enumerate(
        (
            ("jan", "january"),
            ("feb", "february"),
            ("mar", "march"),
            ("apr", "april"),
            ("may",),
            ("jun", "june"),
            ("jul", "july"),
            ("aug", "august"),
            ("sep", "sept", "september"),
            ("oct", "october"),
            ("nov", "november"),
            ("dec", "december"),
        ),
        start=1,
    )
    for name in names
}


def clean_text(value: str) -> str:
    return _WS_RE.sub(" ", unicodedata.normalize("NFC", value)).strip()


def has_control_chars(value: str) -> bool:
    return _CONTROL_RE.search(value) is not None


def cell_text(value: CellValue) -> str | None:  # noqa: PLR0911  - one early return per input shape keeps the table-driven rules readable
    """The cell as display text (``None`` when empty)."""
    if value is None:
        return None
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, dt.datetime):
        return value.date().isoformat() if value.time() == dt.time() else value.isoformat()
    if isinstance(value, dt.date):
        return value.isoformat()
    if isinstance(value, float):
        if value.is_integer():
            return str(int(value))
        return repr(value)
    text = clean_text(str(value))
    return text or None


# --- dates ----------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DateResult:
    value: dt.date | None
    error: str | None = None
    ambiguous: bool = False


def _serial(number: float) -> dt.date | None:
    if not SERIAL_MIN <= number <= SERIAL_MAX or not float(number).is_integer():
        return None
    return EXCEL_EPOCH + dt.timedelta(days=int(number))


def numeric_parts(value: CellValue) -> tuple[int, int] | None:
    """(first, second) of a numeric ``a/b/yyyy`` text date, for column-level order evidence."""
    if not isinstance(value, str):
        return None
    match = _NUMERIC_DATE_RE.match(clean_text(value))
    if match is None or len(match.group(1)) == 4:
        return None
    return int(match.group(1)), int(match.group(2))


def day_first_proven(values: Iterable[CellValue]) -> bool:
    """True when some value in the column can only be day-first (first part above 12)."""
    for value in values:
        parts = numeric_parts(value)
        if parts is not None and parts[0] > 12 >= parts[1]:
            return True
    return False


def _safe_date(year: int, month: int, day: int) -> dt.date | None:
    try:
        return dt.date(year, month, day)
    except ValueError:
        return None


def parse_date(  # noqa: PLR0911, PLR0912  - one early return per input shape keeps the table-driven rules readable
    value: CellValue, *, day_first_proven: bool = False
) -> DateResult:
    if value is None:
        return DateResult(None, "missing")
    if isinstance(value, dt.datetime):
        return DateResult(value.date())
    if isinstance(value, dt.date):
        return DateResult(value)
    if isinstance(value, bool):
        return DateResult(None, "invalid_date")
    if isinstance(value, int | float):
        serial = _serial(float(value))
        return DateResult(serial) if serial else DateResult(None, "invalid_date")
    text = clean_text(str(value))
    if text.isdigit() and len(text) == 5:
        serial = _serial(float(text))
        return DateResult(serial) if serial else DateResult(None, "invalid_date")
    match = _NUMERIC_DATE_RE.match(text)
    if match is not None:
        a, b, c = match.groups()
        if len(a) == 4:  # YYYY-MM-DD
            parsed = _safe_date(int(a), int(b), int(c))
            return DateResult(parsed) if parsed else DateResult(None, "invalid_date")
        if len(c) != 4:
            return DateResult(None, "year_needs_four_digits")
        day, month, year = int(a), int(b), int(c)
        parsed = _safe_date(year, month, day)
        if parsed is None:
            return DateResult(None, "invalid_date")
        ambiguous = day != month and day <= 12 and month <= 12 and not day_first_proven
        return DateResult(parsed, ambiguous=ambiguous)
    match = _TEXT_MONTH_RE.match(text)
    if match is not None:
        day_s, month_s, year_s = match.groups()
        month_no = _MONTHS.get(month_s.casefold().rstrip("."))
        if month_no is None:
            return DateResult(None, "invalid_date")
        if len(year_s) != 4:
            return DateResult(None, "year_needs_four_digits")
        parsed = _safe_date(int(year_s), month_no, int(day_s))
        return DateResult(parsed) if parsed else DateResult(None, "invalid_date")
    return DateResult(None, "invalid_date")


# --- enums ----------------------------------------------------------------------------------------


def enum_lookup(
    allowed: Sequence[str], synonyms: Mapping[str, Sequence[str]] | None
) -> dict[str, str]:
    """Casefolded cell text -> allowed enum value."""
    out = {v.casefold(): v for v in allowed}
    for value, words in (synonyms or {}).items():
        if value in allowed:
            for word in words:
                out.setdefault(clean_text(word).casefold(), value)
    return out


# --- classes and sections -------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ClassInfo:
    id: str
    code: str
    display_en: str
    display_te: str


def _token(text: str) -> str:
    return " ".join(re.sub(r"[.\-_/:]+", " ", clean_text(text).casefold()).split())


class ClassResolver:
    """Resolve class cells to a class id, and class-section cells to (class id, section name)."""

    def __init__(
        self,
        classes: Sequence[ClassInfo],
        aliases: Mapping[str, Sequence[str]],
        noise_words: Sequence[str],
    ) -> None:
        self._noise = {_token(w) for w in noise_words}
        self._lookup: dict[str, str] = {}
        for klass in classes:
            keys = {klass.code, klass.display_en, klass.display_te}
            keys.update(aliases.get(klass.code.upper(), ()))
            for key in keys:
                token = self._strip_noise(_token(key))
                if token:
                    self._lookup.setdefault(token, klass.id)
                    self._lookup.setdefault(token.replace(" ", ""), klass.id)

    def _strip_noise(self, token: str) -> str:
        words = [w for w in token.split() if w not in self._noise]
        return " ".join(words)

    def resolve_class(self, text: str) -> str | None:
        token = self._strip_noise(_token(text))
        if not token:
            return None
        return self._lookup.get(token) or self._lookup.get(token.replace(" ", ""))

    def resolve_class_section(self, text: str) -> tuple[str, str] | None:
        """``9-A`` / ``IX A`` / ``9A`` / ``Class 9 - A`` -> (class id, section name)."""
        token = self._strip_noise(_token(text))
        if not token:
            return None
        parts = token.split()
        if len(parts) >= 2:
            klass = self.resolve_class(" ".join(parts[:-1]))
            if klass is not None:
                return klass, parts[-1].upper()
        compact = token.replace(" ", "")
        # Longest class prefix first ("12a" -> XII + A, not I + "2A").
        for cut in range(len(compact) - 1, 0, -1):
            klass = self._lookup.get(compact[:cut])
            if klass is not None:
                return klass, compact[cut:].upper()
        return None


__all__ = [
    "ClassInfo",
    "ClassResolver",
    "DateResult",
    "cell_text",
    "clean_text",
    "day_first_proven",
    "enum_lookup",
    "has_control_chars",
    "numeric_parts",
    "parse_date",
]
