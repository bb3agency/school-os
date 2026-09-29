"""Read an uploaded spreadsheet into plain cells (FR-IMP-001, SEC-017, docs/07 §10). Pure.

- **XLSX** is opened with openpyxl ``read_only=True, data_only=False`` after a zip pre-check
  (member count, declared uncompressed size, compression ratio: zip bombs are refused before any
  XML is parsed). openpyxl parses XML through defusedxml when it is installed (entity expansion
  and external entities are refused). ``data_only=False`` means a formula cell yields its
  formula text; nothing is ever evaluated or recalculated, and cached results are ignored.
- **CSV** (and Google Sheets CSV exports) must be UTF-8 (a BOM is accepted); the delimiter is
  sniffed among ``, ; TAB |``.
- **Formulas** (formula cells, or text starting with ``=``/``@``, or ``+``/``-`` followed by
  something other than a number) are kept as inert literal text and flagged
  (``formula_not_evaluated``); they never become values.
- The header is the first of the first rows with at least three text cells. Limits: rows,
  columns, cell length and physical rows scanned (config.yaml).

The low-level readers live in :mod:`app.core.spreadsheet` (shared with the documents sheet
viewer, FR-DOC-009); :class:`SheetError` is its ``SpreadsheetError``.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

from app.core.spreadsheet import (
    Cell,
    CellValue,
    FileKind,
    looks_like_formula,
)
from app.core.spreadsheet import SpreadsheetError as SheetError
from app.core.spreadsheet import raw_rows as read_raw_rows
from app.imports.config import Limits


@dataclass(frozen=True, slots=True)
class SheetRow:
    row_no: int  # 1-based row number as the spreadsheet shows it
    cells: tuple[Cell, ...]

    def cell(self, index: int) -> Cell:
        return self.cells[index] if index < len(self.cells) else Cell(None)


@dataclass(frozen=True, slots=True)
class Sheet:
    kind: FileKind
    header_row: int
    headers: tuple[str, ...]
    rows: tuple[SheetRow, ...]
    formula_cells: int


# --- common ---------------------------------------------------------------------------------------


def _is_text(cell: Cell) -> bool:
    return isinstance(cell.value, str) and bool(cell.value.strip()) and not cell.formula


def _trim(cells: Sequence[Cell]) -> list[Cell]:
    end = len(cells)
    while end and cells[end - 1].empty:
        end -= 1
    return list(cells[:end])


def _check_cells(cells: Sequence[Cell], limits: Limits) -> None:
    if len(cells) > limits.max_columns:
        raise SheetError("too_many_columns")
    for cell in cells:
        if isinstance(cell.value, str) and len(cell.value) > limits.max_cell_chars:
            raise SheetError("cell_too_long")


def build_sheet(
    kind: FileKind, raw_rows: Iterable[tuple[int, list[Cell]]], limits: Limits
) -> Sheet:
    header_row = 0
    headers: tuple[str, ...] = ()
    rows: list[SheetRow] = []
    formulas = 0
    for row_no, raw in raw_rows:
        cells = _trim(raw)
        if not header_row:
            if row_no > limits.header_search_rows:
                raise SheetError("header_not_found")
            if sum(1 for c in cells if _is_text(c)) >= limits.header_min_text_cells:
                _check_cells(cells, limits)
                header_row = row_no
                headers = tuple(
                    " ".join(str(c.value).split()) if not c.empty else "" for c in cells
                )
            continue
        if not cells:
            continue
        _check_cells(cells, limits)
        formulas += sum(1 for c in cells if c.formula)
        rows.append(SheetRow(row_no, tuple(cells)))
        if len(rows) > limits.max_rows:
            raise SheetError("too_many_rows")
    if not header_row:
        raise SheetError("header_not_found")
    if not rows:
        raise SheetError("no_data_rows")
    width = max(len(headers), *(len(r.cells) for r in rows))
    if width > limits.max_columns:
        raise SheetError("too_many_columns")
    headers = headers + ("",) * (width - len(headers))
    return Sheet(kind, header_row, headers, tuple(rows), formulas)


def read_sheet(data: bytes, kind: FileKind, limits: Limits) -> Sheet:
    """Parse ``data`` (never evaluating formulas) or raise :class:`SheetError`."""
    if len(data) > limits.max_file_bytes:
        raise SheetError("file_too_large")
    if not data:
        raise SheetError("empty_file")
    return build_sheet(kind, read_raw_rows(data, kind, limits), limits)


def edited_cell(value: str | None) -> Cell:
    """The cell a staged edit puts in place (FR-IMP-008): text is inert, formula-looking text
    is flagged exactly as in an uploaded file; ``None`` clears the cell."""
    if value is None or not value.strip():
        return Cell(None)
    return Cell(value, looks_like_formula(value))


def with_edits(sheet: Sheet, edits: Mapping[tuple[int, int], str | None]) -> Sheet:
    """``sheet`` with staged cell edits applied (``(row_no, column index) -> value``). The raw
    file is never changed; edits for rows or columns the sheet does not have are ignored."""
    if not edits:
        return sheet
    width = len(sheet.headers)
    by_row: dict[int, dict[int, str | None]] = {}
    for (row_no, column), value in edits.items():
        if 0 <= column < width:
            by_row.setdefault(row_no, {})[column] = value
    rows: list[SheetRow] = []
    formulas = 0
    for original in sheet.rows:
        row = original
        changes = by_row.get(original.row_no)
        if changes:
            cells = list(original.cells) + [Cell(None)] * (max(changes) + 1 - len(original.cells))
            for column, value in changes.items():
                cells[column] = edited_cell(value)
            row = SheetRow(original.row_no, tuple(cells))
        formulas += sum(1 for c in row.cells if c.formula)
        rows.append(row)
    return Sheet(sheet.kind, sheet.header_row, sheet.headers, tuple(rows), formulas)


__all__ = [
    "Cell",
    "CellValue",
    "FileKind",
    "Sheet",
    "SheetError",
    "SheetRow",
    "edited_cell",
    "looks_like_formula",
    "read_sheet",
    "with_edits",
]
