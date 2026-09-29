"""Attendance and marks sheets (FR-ATT-004, FR-MRK-004; invariant 4; SEC-017). Pure: no database.

Synthetic registers only: admission numbers like ``SYN-001``, no names are ever returned.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Sequence
from decimal import Decimal

import pytest

from app.academics import sheets
from app.core.redaction import verhoeff_check_digit
from app.core.spreadsheet import Cell, CellValue, csv_rows

A, B, C = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
ROSTER = sheets.Roster.build([(A, "SYN-001", "1"), (B, "SYN-002", "2"), (C, "syn-003", None)])
FIRST, LAST, TODAY = dt.date(2026, 6, 1), dt.date(2027, 3, 31), dt.date(2026, 9, 29)


def _rows(*rows: Sequence[CellValue]) -> list[tuple[int, list[Cell]]]:
    out: list[tuple[int, list[Cell]]] = []
    for i, row in enumerate(rows, start=1):
        cells = [
            Cell(v, isinstance(v, str) and v.startswith("=")) if v != "" else Cell(None)
            for v in row
        ]
        out.append((i, cells))
    return out


def _attendance(*rows: Sequence[CellValue]) -> sheets.AttendanceSheet:
    return sheets.read_attendance(_rows(*rows), ROSTER, first=FIRST, last=LAST, today=TODAY)


def _aadhaar() -> str:
    base = "23456789012"
    return base + verhoeff_check_digit(base)


def test_FR_ATT_004_reads_dates_codes_and_students_by_admission_number() -> None:
    out = _attendance(
        ["Adm No", "Name", "22/09/2026", dt.date(2026, 9, 23), "2026-09-24"],
        ["SYN-001", "Synthetic One", "P", "a", "LV"],
        ["syn-002", "Synthetic Two", "A", "A", "late"],
        ["SYN-003", "", "", "P", ""],
    )
    assert out.issues == ()
    assert out.dates == (dt.date(2026, 9, 22), dt.date(2026, 9, 23), dt.date(2026, 9, 24))
    assert out.students == 3
    got = {(e.student_id, e.on_date): e.status for e in out.entries}
    assert got[(A, dt.date(2026, 9, 22))] == "present"
    assert got[(A, dt.date(2026, 9, 23))] == "absent"
    assert got[(A, dt.date(2026, 9, 24))] == "leave"
    assert got[(B, dt.date(2026, 9, 24))] == "late"
    assert (C, dt.date(2026, 9, 22)) not in got  # empty cell: not marked
    assert len(out.entries) == 7


def test_FR_ATT_004_roll_numbers_match_too_and_whole_numbers_read_as_text() -> None:
    out = _attendance(["Roll No", "01/09/2026"], [1, "P"], [2.0, "A"])
    assert out.issues == ()
    assert {e.student_id for e in out.entries} == {A, B}


def test_FR_ATT_004_every_problem_is_listed_with_row_and_column() -> None:
    out = _attendance(
        ["Admission number", "22/09/2026", "31/12/2026", "15/05/2026", "not a date"],
        ["SYN-001", "X", "P", "P", "P"],
        ["SYN-999", "P", "", "", ""],
        ["SYN-001", "P", "", "", ""],
        ["SYN-002", '=HYPERLINK("x")', "", "", ""],
    )
    codes = {(i.row, i.column, i.code) for i in out.issues}
    assert (1, 3, "future_date") in codes
    assert (1, 4, "date_out_of_range") in codes
    assert (1, 5, "bad_date") in codes
    assert (2, 2, "bad_code") in codes
    assert (3, 1, "unknown_student") in codes
    assert (4, 1, "duplicate_student") in codes
    assert (5, 2, "formula_not_allowed") in codes
    assert out.issue_count == len(out.issues)


def test_FR_ATT_004_student_column_is_required() -> None:
    with pytest.raises(sheets.SheetRefused) as err:
        _attendance(["Name", "22/09/2026"], ["Synthetic", "P"])
    assert err.value.code == "student_column_missing"
    with pytest.raises(sheets.SheetRefused) as err:
        _attendance()
    assert err.value.code == "header_missing"


@pytest.mark.parametrize("where", ["key", "cell", "header"])
def test_invariant_4_a_full_aadhaar_number_refuses_the_whole_file(where: str) -> None:
    number = _aadhaar()
    header = ["Adm No", "22/09/2026" if where != "header" else number]
    row = [number if where == "key" else "SYN-001", number if where == "cell" else "P"]
    with pytest.raises(sheets.SheetRefused) as err:
        _attendance(header, row)
    assert err.value.code == "aadhaar_full_number_rejected"
    assert number not in str(err.value)


def test_FR_ATT_004_too_many_dates_is_refused() -> None:
    header: list[CellValue] = ["Adm No"]
    header += [(dt.date(2026, 7, 1) + dt.timedelta(days=i)).isoformat() for i in range(32)]
    with pytest.raises(sheets.SheetRefused) as err:
        _attendance(header, ["SYN-001"] + ["P"] * 32)
    assert err.value.code == "too_many_dates"


def test_FR_ATT_004_csv_through_the_shared_reader() -> None:
    data = b"Adm No,22/09/2026\nSYN-001,A\n"
    rows = list(csv_rows(data, sheets.load_config().sheets.limits))
    out = sheets.read_attendance(rows, ROSTER, first=FIRST, last=LAST, today=TODAY)
    assert [(e.student_id, e.status) for e in out.entries] == [(A, "absent")]


# --- marks ----------------------------------------------------------------------------------------


def _marks(*rows: Sequence[CellValue]) -> sheets.MarksSheet:
    return sheets.read_marks(_rows(*rows), ROSTER)


def test_FR_MRK_004_reads_subjects_max_marks_and_absent() -> None:
    out = _marks(
        ["Adm No", "Name", "Telugu", "English", "Maths"],
        ["Max marks", "", 100, 100, "50"],
        ["SYN-001", "Synthetic One", 35, "AB", 49.5],
        ["SYN-002", "Synthetic Two", "", 72, 0],
    )
    assert out.issues == ()
    assert out.subjects == ("Telugu", "English", "Maths")
    got = {(e.student_id, e.subject): e for e in out.entries}
    assert got[(A, "Telugu")].marks == Decimal("35.00")
    assert got[(A, "English")].absent is True
    assert got[(A, "English")].marks is None
    assert got[(A, "Maths")].max_marks == Decimal("50.00")
    assert got[(A, "Maths")].marks == Decimal("49.50")
    assert (B, "Telugu") not in got
    assert got[(B, "Maths")].marks == Decimal("0.00")


def test_FR_MRK_004_problems_are_listed() -> None:
    out = _marks(
        ["Adm No", "Telugu", "Maths", "Maths"],
        ["Max marks", 100, 0, 50],
        ["SYN-001", 101, 10, "ten"],
    )
    codes = {(i.row, i.column, i.code) for i in out.issues}
    assert (1, 4, "duplicate_subject") in codes
    assert (2, 3, "bad_max_marks") in codes
    assert (3, 2, "marks_over_max") in codes


def test_FR_MRK_004_the_max_marks_row_is_required() -> None:
    with pytest.raises(sheets.SheetRefused) as err:
        _marks(["Adm No", "Telugu"], ["SYN-001", 30])
    assert err.value.code == "max_row_missing"


def test_FR_MRK_004_bad_marks_text() -> None:
    out = _marks(["Roll no", "Science"], ["Max", 40], [1, "forty"])
    assert [(i.row, i.column, i.code) for i in out.issues] == [(3, 2, "bad_marks")]


def test_normalised_headers_ignore_case_spaces_and_punctuation() -> None:
    assert sheets.normalise_header(" Adm. No ") == sheets.normalise_header("admno")
    assert sheets.normalise_header("ప్రవేశ సంఖ్య") == sheets.normalise_header("ప్రవేశసంఖ్య")
    assert sheets.normalise_key(" syn-001 ") == sheets.normalise_key("SYN-001")
