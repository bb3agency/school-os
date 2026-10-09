"""Hostile spreadsheets cannot exhaust memory through one very wide row (SEC-017, docs/07 §10).

A 3 MB CSV of commas or a small XLSX whose single row holds hundreds of thousands of cell
elements used to be materialised cell by cell before any column limit was checked: the sheet
viewer, the import parser and the attendance/marks sheet previews (all on the API request path
or a worker) peaked at hundreds of megabytes for one request. Readers now refuse a row with more
cells than a spreadsheet can hold (16,384 columns, XFD) before building it. Synthetic data only.
"""

from __future__ import annotations

import io
import random
import tracemalloc
import zipfile
from dataclasses import dataclass

import pytest

from app.core.spreadsheet import MAX_ROW_CELLS, SpreadsheetError, csv_rows, xlsx_rows


@dataclass(frozen=True)
class _Limits:
    max_scanned_rows: int = 60_000
    xlsx_max_uncompressed_bytes: int = 104_857_600
    xlsx_max_compression_ratio: int = 200
    xlsx_max_members: int = 2000


_CT = (
    '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/'
    'content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-'
    'package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/>'
    '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-'
    'officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/'
    'sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.'
    'worksheet+xml"/></Types>'
)
_RELS = (
    '<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/'
    'relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/'
    'officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>'
)
_WB = (
    '<?xml version="1.0"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/'
    'main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>'
    '<sheet name="S" sheetId="1" r:id="rId1"/></sheets></workbook>'
)
_WB_RELS = (
    '<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/'
    'relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/'
    'officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/></Relationships>'
)


def _xlsx(rows: list[str], *, sheet_path: str = "xl/worksheets/sheet1.xml") -> bytes:
    body = "".join(f'<row r="{i}">{cells}</row>' for i, cells in enumerate(rows, start=1))
    sheet = (
        '<?xml version="1.0" encoding="UTF-8"?><worksheet xmlns="http://schemas.openxmlformats.'
        f'org/spreadsheetml/2006/main"><sheetData>{body}</sheetData></worksheet>'
    )
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", _CT)
        archive.writestr("_rels/.rels", _RELS)
        archive.writestr("xl/workbook.xml", _WB)
        archive.writestr(
            "xl/_rels/workbook.xml.rels",
            _WB_RELS.replace("worksheets/sheet1.xml", sheet_path.removeprefix("xl/")),
        )
        archive.writestr(sheet_path, sheet)
    return out.getvalue()


def _padded_cells(count: int) -> str:
    """``count`` text cells of 120 pseudo-random hex characters (stays under the ratio limit)."""
    rng = random.Random(17)
    return "".join(
        f'<c t="inlineStr"><is><t>{rng.randbytes(60).hex()}</t></is></c>' for _ in range(count)
    )


def _peak(fn: object) -> tuple[str | None, float]:
    tracemalloc.start()
    code: str | None = None
    try:
        try:
            for _ in fn():  # type: ignore[operator]
                pass
        except SpreadsheetError as exc:
            code = exc.code
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    return code, peak / 1e6


def test_SEC_017_csv_row_of_millions_of_fields_is_refused_cheaply() -> None:
    data = b"," * 3_000_000
    code, peak_mb = _peak(lambda: csv_rows(data, _Limits()))
    assert code == "too_many_columns"
    assert peak_mb < 60, f"peak {peak_mb:.0f} MB"


@pytest.mark.parametrize("delimiter", [",", ";", "\t", "|"])
def test_AA_hardening_10mb_csv_row_is_refused_before_the_csv_module_builds_it(
    delimiter: str,
) -> None:
    # Audit 2026-10-04 api-auth hardening note: the csv module used to build the whole
    # 10 M-field row (~150 MB) before the column check ran.
    # Ordinary rows fill the sniffer's sample, so the delimiter is detected; the wide row follows.
    ordinary = f"a{delimiter}b{delimiter}c\r\n" * 10_000
    data = ordinary.encode() + delimiter.encode() * 10_000_000
    code, peak_mb = _peak(lambda: csv_rows(data, _Limits()))
    assert code == "too_many_columns"
    assert peak_mb < 30, f"peak {peak_mb:.0f} MB"


def test_AA_hardening_csv_quoted_delimiters_and_line_breaks_do_not_count_as_columns() -> None:
    inside = "," * (MAX_ROW_CELLS * 2) + "\r\nline two, still the same cell"
    data = f'name,"{inside}",x\r\n"he said ""hi, there""",b,c\r\nlast,row,here\r\n'.encode()
    rows = list(csv_rows(data, _Limits()))
    assert [len(cells) for _, cells in rows] == [3, 3, 3]
    assert rows[1][1][0].value == 'he said "hi, there"'


def test_AA_hardening_csv_wide_row_after_a_quoted_multi_line_cell_is_refused() -> None:
    data = ('a,"multi\nline, cell",b\n' + "x," * MAX_ROW_CELLS + "x\n").encode()
    code, _ = _peak(lambda: csv_rows(data, _Limits()))
    assert code == "too_many_columns"


def test_SEC_017_xlsx_row_of_many_cell_elements_is_refused_before_parsing() -> None:
    # Coordinates make each element distinct, so the archive stays under the ratio limit.
    from openpyxl.utils import get_column_letter

    wide = "".join(f'<c r="{get_column_letter(1 + i % 16384)}1"/>' for i in range(300_000))
    data = _xlsx([wide])
    code, peak_mb = _peak(lambda: xlsx_rows(data, _Limits()))
    assert code == "too_many_columns"
    assert peak_mb < 40, f"peak {peak_mb:.0f} MB"


def test_SEC_017_xlsx_wide_row_in_an_oddly_named_part_is_refused_too() -> None:
    wide = "<c/>" * (MAX_ROW_CELLS + 1)
    data = _xlsx(["<c><v>1</v></c>", wide], sheet_path="xl/odd/data.bin")
    code, _ = _peak(lambda: xlsx_rows(data, _Limits()))
    assert code == "too_many_columns"


@pytest.mark.parametrize("kind", ["csv", "xlsx"])
def test_SEC_017_rows_up_to_the_spreadsheet_maximum_still_read(kind: str) -> None:
    if kind == "csv":
        data = ("a," * (MAX_ROW_CELLS - 1) + "a\r\nb\r\n").encode()
        rows = list(csv_rows(data, _Limits()))
    else:
        # ~2.5 MB of row XML: the scan crosses several read chunks without losing a tag.
        data = _xlsx([_padded_cells(MAX_ROW_CELLS), "<c><v>2</v></c>"])
        rows = list(xlsx_rows(data, _Limits()))
    assert [len(cells) for _, cells in rows] == [MAX_ROW_CELLS, 1]


def test_SEC_017_xlsx_one_cell_too_many_across_read_chunks_is_refused() -> None:
    data = _xlsx([_padded_cells(MAX_ROW_CELLS + 1)])
    code, _ = _peak(lambda: xlsx_rows(data, _Limits()))
    assert code == "too_many_columns"
