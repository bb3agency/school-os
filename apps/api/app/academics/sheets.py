"""Attendance and marks sheets (FR-ATT-004, FR-MRK-004). Pure: no database, no web.

Input is the ``(row number, cells)`` stream of :func:`app.core.spreadsheet.raw_rows` (formulas
never evaluated, zip bombs refused before parsing, NFC text). Students are matched only against
the section's roster (admission number or roll number); nothing else in the file (names, extra
columns) is kept or returned.

- **Attendance** (FR-ATT-004): header row = student column (admission or roll number), an
  optional name column, then one date per column; cells are the codes of ``config.yaml``
  (``P``/``A``/``L``/``LV``) or empty (not marked).
- **Marks** (FR-MRK-004): header row = student column, optional name column, one subject per
  column; the next row gives each subject's maximum marks; then one row per student with marks,
  an absent code (``AB``) or empty (not entered).

Whole-file problems raise :class:`SheetRefused` (a code, never a cell value): a full Aadhaar
number anywhere (invariant 4), no header row, no student column, too many rows, dates or
subjects. Everything else is an :class:`Issue` (row, column, code) so the preview can list
every problem at once; entries under a bad header are dropped.
"""

from __future__ import annotations

import datetime as dt
import re
import unicodedata
import uuid
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Final

from app.academics.config import AcademicsConfig, AttendanceStatus, load_config
from app.core.redaction import contains_full_aadhaar
from app.core.spreadsheet import Cell, display_text

AADHAAR_CODE: Final = "aadhaar_full_number_rejected"
_DATE_PATTERNS: Final = (
    (re.compile(r"^(\d{1,2})[/.-](\d{1,2})[/.-](\d{4})$"), "dmy"),
    (re.compile(r"^(\d{4})-(\d{1,2})-(\d{1,2})$"), "ymd"),
)
_TWO_PLACES: Final = Decimal("0.01")
_SPACE_RE: Final = re.compile(r"\s+")

Rows = Iterable[tuple[int, list[Cell]]]


class SheetRefused(Exception):
    """The whole file cannot be used (``code`` is shown to the user; never a value)."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class Issue:
    row: int
    column: int | None
    code: str


def normalise_header(text: str) -> str:
    """NFC, lower case, only letters, combining marks and digits (Telugu vowel signs are
    marks)."""
    clean = unicodedata.normalize("NFC", text).casefold()
    return "".join(ch for ch in clean if unicodedata.category(ch)[0] in "LMN")


def normalise_key(text: str) -> str:
    """An admission or roll number for matching: NFC, trimmed, case-folded, spaces collapsed."""
    return _SPACE_RE.sub(" ", unicodedata.normalize("NFC", text).strip()).casefold()


@dataclass(frozen=True, slots=True)
class Roster:
    """The section's students by normalised admission number and roll number."""

    admission: Mapping[str, uuid.UUID]
    roll: Mapping[str, uuid.UUID]

    @classmethod
    def build(cls, members: Iterable[tuple[uuid.UUID, str | None, str | None]]) -> Roster:
        admission: dict[str, uuid.UUID] = {}
        roll: dict[str, uuid.UUID] = {}
        for student_id, admission_no, roll_no in members:
            if admission_no:
                admission[normalise_key(admission_no)] = student_id
            if roll_no:
                roll[normalise_key(roll_no)] = student_id
        return cls(admission=admission, roll=roll)


@dataclass(frozen=True, slots=True)
class AttendanceEntry:
    student_id: uuid.UUID
    on_date: dt.date
    status: AttendanceStatus


@dataclass(frozen=True, slots=True)
class AttendanceSheet:
    entries: tuple[AttendanceEntry, ...]
    issues: tuple[Issue, ...]
    issue_count: int
    dates: tuple[dt.date, ...]
    students: int


@dataclass(frozen=True, slots=True)
class MarksEntry:
    student_id: uuid.UUID
    subject: str
    max_marks: Decimal
    marks: Decimal | None
    absent: bool


@dataclass(frozen=True, slots=True)
class MarksSheet:
    entries: tuple[MarksEntry, ...]
    issues: tuple[Issue, ...]
    issue_count: int
    subjects: tuple[str, ...]
    students: int


class _Issues:
    def __init__(self, limit: int) -> None:
        self.limit = limit
        self.items: list[Issue] = []
        self.count = 0

    def add(self, row: int, column: int | None, code: str) -> None:
        self.count += 1
        if len(self.items) < self.limit:
            self.items.append(Issue(row, column, code))


def _text(cell: Cell) -> str | None:
    return display_text(cell.value)


def _refuse_aadhaar(rows: list[tuple[int, list[Cell]]]) -> None:
    for _, cells in rows:
        for cell in cells:
            text = _text(cell)
            if text and contains_full_aadhaar(text):
                raise SheetRefused(AADHAAR_CODE)


def _materialise(rows: Rows, cfg: AcademicsConfig) -> list[tuple[int, list[Cell]]]:
    """Non-empty rows, bounded (``too_many_rows``); trailing empty cells dropped."""
    limits = cfg.sheets.limits
    out: list[tuple[int, list[Cell]]] = []
    for row_no, cells in rows:
        trimmed = list(cells)
        while trimmed and trimmed[-1].empty:
            trimmed.pop()
        if not trimmed:
            continue
        if len(out) >= limits.max_rows + 2:
            raise SheetRefused("too_many_rows")
        if len(trimmed) > limits.max_columns:
            raise SheetRefused("too_many_columns")
        out.append((row_no, trimmed))
    return out


@dataclass(frozen=True, slots=True)
class _Layout:
    header_row: int
    key_column: int  # 0-based
    key_kind: str  # "admission" | "roll"
    skip: frozenset[int]  # 0-based columns that are not data (key, name)
    headers: list[Cell]


def _layout(rows: list[tuple[int, list[Cell]]], cfg: AcademicsConfig) -> _Layout:
    if not rows:
        raise SheetRefused("header_missing")
    header_row, headers = rows[0]
    rules = cfg.sheets
    admission = {normalise_header(h) for h in rules.admission_headers}
    roll = {normalise_header(h) for h in rules.roll_headers}
    names = {normalise_header(h) for h in rules.name_headers}
    key_column: int | None = None
    key_kind = ""
    skip: set[int] = set()
    for index, cell in enumerate(headers):
        text = _text(cell)
        if text is None or isinstance(cell.value, dt.date):
            continue
        norm = normalise_header(text)
        if key_column is None and norm in admission:
            key_column, key_kind = index, "admission"
        elif key_column is None and norm in roll:
            key_column, key_kind = index, "roll"
        elif norm in names:
            skip.add(index)
    if key_column is None:
        raise SheetRefused("student_column_missing")
    skip.add(key_column)
    return _Layout(header_row, key_column, key_kind, frozenset(skip), headers)


def _student(
    roster: Roster,
    layout: _Layout,
    row: tuple[int, list[Cell]],
    *,
    seen: set[uuid.UUID],
    issues: _Issues,
) -> uuid.UUID | None:
    row_no, cells = row
    column = layout.key_column + 1
    cell = cells[layout.key_column] if layout.key_column < len(cells) else Cell(None)
    if cell.formula:
        issues.add(row_no, column, "formula_not_allowed")
        return None
    text = _text(cell)
    if text is None:
        issues.add(row_no, column, "student_missing")
        return None
    lookup = roster.admission if layout.key_kind == "admission" else roster.roll
    student_id = lookup.get(normalise_key(text))
    if student_id is None:
        issues.add(row_no, column, "unknown_student")
        return None
    if student_id in seen:
        issues.add(row_no, column, "duplicate_student")
        return None
    seen.add(student_id)
    return student_id


def _data_cells(
    layout: _Layout, row_no: int, cells: Sequence[Cell], issues: _Issues
) -> Iterator[tuple[int, int, Cell]]:
    """``(index, 1-based column, cell)`` of the non-empty data cells of a row; formula cells are
    reported and skipped (SEC-017)."""
    for index, cell in enumerate(cells):
        if index in layout.skip or cell.empty:
            continue
        if cell.formula:
            issues.add(row_no, index + 1, "formula_not_allowed")
            continue
        yield index, index + 1, cell


def _parse_date(cell: Cell) -> dt.date | None:
    value = cell.value
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    text = _text(cell)
    if text is None:
        return None
    for pattern, order in _DATE_PATTERNS:
        match = pattern.match(text.strip())
        if match is None:
            continue
        a, b, c = (int(g) for g in match.groups())
        day, month, year = (a, b, c) if order == "dmy" else (c, b, a)
        try:
            return dt.date(year, month, day)
        except ValueError:
            return None
    return None


def _date_code(day: dt.date | None, *, first: dt.date, last: dt.date, today: dt.date) -> str | None:
    if day is None:
        return "bad_date"
    if day > today:
        return "future_date"
    if day < first or day > last:
        return "date_out_of_range"
    return None


def _date_columns(
    layout: _Layout, issues: _Issues, *, first: dt.date, last: dt.date, today: dt.date
) -> dict[int, dt.date]:
    columns: dict[int, dt.date] = {}
    for index, column, cell in _data_cells(layout, layout.header_row, layout.headers, issues):
        day = _parse_date(cell)
        code = _date_code(day, first=first, last=last, today=today)
        if code is None and day in columns.values():
            code = "duplicate_date"
        if code is not None or day is None:
            issues.add(layout.header_row, column, code or "bad_date")
        else:
            columns[index] = day
    return columns


def read_attendance(
    rows: Rows,
    roster: Roster,
    *,
    first: dt.date,
    last: dt.date,
    today: dt.date,
    cfg: AcademicsConfig | None = None,
) -> AttendanceSheet:
    """Parse an attendance sheet for dates ``first..last`` (the section's academic year), none
    after ``today`` (IST)."""
    cfg = cfg or load_config()
    data = _materialise(rows, cfg)
    _refuse_aadhaar(data)
    layout = _layout(data, cfg)
    issues = _Issues(cfg.sheets.max_issues)
    columns = _date_columns(layout, issues, first=first, last=last, today=today)
    if len(columns) > cfg.attendance.max_sheet_dates:
        raise SheetRefused("too_many_dates")
    codes = {k.casefold(): v for k, v in cfg.attendance.codes.items()}
    max_chars = cfg.sheets.limits.max_cell_chars
    entries: list[AttendanceEntry] = []
    seen: set[uuid.UUID] = set()
    for row in data[1:]:
        student_id = _student(roster, layout, row, seen=seen, issues=issues)
        for index, column, cell in _data_cells(layout, row[0], row[1], issues):
            day = columns.get(index)
            if day is None:
                continue  # under a missing or bad header (already reported)
            text = (_text(cell) or "").strip()
            status = codes.get(text.casefold()) if len(text) <= max_chars else None
            if status is None:
                issues.add(
                    row[0], column, "bad_code" if len(text) <= max_chars else "cell_too_long"
                )
            elif student_id is not None:
                entries.append(AttendanceEntry(student_id, day, status))
    return AttendanceSheet(
        entries=tuple(entries),
        issues=tuple(issues.items),
        issue_count=issues.count,
        dates=tuple(sorted(columns.values())),
        students=len(seen),
    )


def _decimal(cell: Cell) -> Decimal | None:
    value = cell.value
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        number = Decimal(str(value))
    else:
        text = (_text(cell) or "").strip()
        try:
            number = Decimal(text)
        except InvalidOperation:
            return None
    if not number.is_finite():
        return None
    return number.quantize(_TWO_PLACES, rounding=ROUND_HALF_UP)


def _subjects(layout: _Layout, cfg: AcademicsConfig, issues: _Issues) -> dict[int, str]:
    out: dict[int, str] = {}
    seen: set[str] = set()
    for index, column, cell in _data_cells(layout, layout.header_row, layout.headers, issues):
        subject = _SPACE_RE.sub(" ", (_text(cell) or "").strip())
        if len(subject) > cfg.marks.max_subject_chars:
            issues.add(layout.header_row, column, "subject_too_long")
        elif subject.casefold() in seen:
            issues.add(layout.header_row, column, "duplicate_subject")
        else:
            seen.add(subject.casefold())
            out[index] = subject
    if len(out) > cfg.marks.max_subjects:
        raise SheetRefused("too_many_subjects")
    return out


def _maxima(
    data: list[tuple[int, list[Cell]]],
    layout: _Layout,
    subjects: Mapping[int, str],
    cfg: AcademicsConfig,
    issues: _Issues,
) -> dict[int, Decimal]:
    """The maximum-marks row (the row after the header, labelled e.g. "Max marks")."""
    labels = {normalise_header(h) for h in cfg.sheets.max_label_rows}
    if len(data) < 2:
        raise SheetRefused("max_row_missing")
    row_no, cells = data[1]
    label = cells[layout.key_column] if layout.key_column < len(cells) else Cell(None)
    if normalise_header(_text(label) or "") not in labels:
        raise SheetRefused("max_row_missing")
    ceiling = Decimal(cfg.marks.max_marks_ceiling)
    maxima: dict[int, Decimal] = {}
    for index in subjects:
        cell = cells[index] if index < len(cells) else Cell(None)
        value = None if cell.formula else _decimal(cell)
        if value is None or value <= 0 or value > ceiling:
            issues.add(row_no, index + 1, "bad_max_marks")
        else:
            maxima[index] = value
    return maxima


def _mark(
    cell: Cell, maximum: Decimal, absent_codes: frozenset[str]
) -> tuple[Decimal | None, bool, str | None]:
    """``(marks, absent, problem code)`` of one marks cell."""
    if (_text(cell) or "").strip().casefold() in absent_codes:
        return None, True, None
    value = _decimal(cell)
    if value is None or value < 0:
        return None, False, "bad_marks"
    if value > maximum:
        return None, False, "marks_over_max"
    return value, False, None


def read_marks(rows: Rows, roster: Roster, *, cfg: AcademicsConfig | None = None) -> MarksSheet:
    """Parse a marks sheet (subjects, a maximum-marks row, one row per student)."""
    cfg = cfg or load_config()
    data = _materialise(rows, cfg)
    _refuse_aadhaar(data)
    layout = _layout(data, cfg)
    issues = _Issues(cfg.sheets.max_issues)
    subjects = _subjects(layout, cfg, issues)
    maxima = _maxima(data, layout, subjects, cfg, issues)
    absent_codes = frozenset(c.casefold() for c in cfg.marks.absent_codes)
    entries: list[MarksEntry] = []
    seen: set[uuid.UUID] = set()
    for row in data[2:]:
        student_id = _student(roster, layout, row, seen=seen, issues=issues)
        for index, column, cell in _data_cells(layout, row[0], row[1], issues):
            if index not in maxima:
                continue  # under a missing or bad header (already reported)
            marks, absent, code = _mark(cell, maxima[index], absent_codes)
            if code is not None:
                issues.add(row[0], column, code)
            elif student_id is not None:
                entries.append(
                    MarksEntry(student_id, subjects[index], maxima[index], marks, absent)
                )
    return MarksSheet(
        entries=tuple(entries),
        issues=tuple(issues.items),
        issue_count=issues.count,
        subjects=tuple(subjects[i] for i in sorted(subjects)),
        students=len(seen),
    )


__all__ = [
    "AADHAAR_CODE",
    "AttendanceEntry",
    "AttendanceSheet",
    "Issue",
    "MarksEntry",
    "MarksSheet",
    "Roster",
    "SheetRefused",
    "load_config",
    "normalise_header",
    "normalise_key",
    "read_attendance",
    "read_marks",
]
