"""Pydantic v2 IO models for spreadsheet imports (docs/09 Imports; FR-IMP-001..007).

Inputs forbid unknown fields. Row previews never contain C3 values: only the keys of the
restricted attributes present in a row (``sensitive``). Errors and warnings are field-level
``{"field", "code", "message_key"}`` entries (+ ``ref``: another row number).
"""

from __future__ import annotations

import datetime as dt
import unicodedata
import uuid
from typing import Annotated, Any, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, StringConstraints

Source = Literal[
    "admission_register",
    "aadhaar_as_printed",
    "udise_plus",
    "board_registration",
    "birth_certificate",
    "parent_form",
    "tc_incoming",
    "manual_entry",
]
BatchStatus = Literal[
    "uploaded",
    "parsing",
    "parsed",
    "validating",
    "validated",
    "committing",
    "committed",
    "reverting",
    "reverted",
    "failed",
]
RowStatus = Literal["valid", "error", "committed", "skipped", "reverted"]
RowFilter = Literal["valid", "error", "committed", "skipped", "reverted", "warning"]
Target = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{1,63}$")]


def _nfc(value: Any) -> Any:
    if isinstance(value, str):
        return unicodedata.normalize("NFC", value).strip()
    return value


TemplateName = Annotated[
    str,
    BeforeValidator(_nfc),
    StringConstraints(min_length=1, max_length=100, pattern=r"^[^\x00-\x1f\x7f]*$"),
]


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class _Out(BaseModel):
    model_config = ConfigDict(frozen=True)


class ImportCreate(_In):
    """Start importing an uploaded file (purpose ``import_file``, scanned) from one source."""

    document_id: uuid.UUID
    source: Source
    kind: Literal["spreadsheet"] = "spreadsheet"


class ColumnMap(_In):
    index: int = Field(ge=0, le=59)
    target: Target  # an attribute key, class / section / class_section / roll_no, or "ignore"


class MappingIn(_In):
    """The full column mapping (unlisted columns are not imported)."""

    columns: list[ColumnMap] = Field(max_length=60)


class CommitIn(_In):
    """``skip_error_rows``: commit only the valid rows (rows with errors are marked skipped)."""

    skip_error_rows: bool = False


class TemplateCreate(_In):
    """Save the mapping of an import as a reusable template (matched by the file's headers)."""

    name: TemplateName
    import_id: uuid.UUID


class Issue(_Out):
    field: str
    code: str
    message_key: str
    ref: str | None = None
    # FR-IMP-010: the existing record that already holds this PEN or APAAR ID.
    student_id: str | None = None


class ColumnOut(_Out):
    index: int
    header: str
    suggested: str | None
    score: int
    target: str | None


class ImportOut(_Out):
    id: uuid.UUID
    kind: str
    source: str
    status: BatchStatus
    document_id: uuid.UUID | None
    file_kind: str | None
    header_row: int | None
    columns: list[ColumnOut]
    mapping_template_id: uuid.UUID | None
    stats: dict[str, int]
    row_count: int
    error_count: int
    error_code: str | None
    job_id: uuid.UUID | None
    created_by: uuid.UUID
    created_at: dt.datetime
    updated_at: dt.datetime
    committed_at: dt.datetime | None
    revert_deadline: dt.datetime | None
    reverted_at: dt.datetime | None
    raw_file_deleted_at: dt.datetime | None
    version: int
    can_commit: bool
    can_revert: bool


class ImportSummary(_Out):
    id: uuid.UUID
    source: str
    status: BatchStatus
    row_count: int
    error_count: int
    created_by: uuid.UUID
    created_at: dt.datetime
    committed_at: dt.datetime | None
    reverted_at: dt.datetime | None


class ImportRowOut(_Out):
    """One row: non-C3 values as they will be recorded, and the C3 keys present (never values)."""

    row_no: int
    status: RowStatus
    action: Literal["create", "update"] | None
    student_id: uuid.UUID | None
    admission_no: str | None
    values: dict[str, str]
    sensitive: list[str]
    section_id: uuid.UUID | None
    class_section: str | None
    roll_no: str | None
    errors: list[Issue]
    warnings: list[Issue]


# --- staged sheet (FR-IMP-008, FR-IMP-009) -------------------------------------------------------

SheetFormat = Literal["csv", "xlsx"]
SheetReadOnly = Literal["committed", "reverted", "in_progress", "failed"]


def _cell_value(value: Any) -> Any:
    """NFC; surrounding spaces trimmed; blank means "clear the cell"."""
    if isinstance(value, str):
        text = unicodedata.normalize("NFC", value).strip()
        return text or None
    return value


# The length limit applies to text only: null (and blank, after trimming) clears the cell.
CellValueIn = Annotated[Annotated[str, Field(max_length=1000)] | None, BeforeValidator(_cell_value)]


class CellEditIn(_In):
    """One cell of a staged row: ``column`` is the 0-based column index, ``value`` the new text
    (``null`` or blank clears the cell). Up to 1,000 characters, no line breaks or control
    characters, never a full Aadhaar number."""

    column: int = Field(ge=0, le=255)
    value: CellValueIn = None


class RowEditIn(_In):
    """Cells to change in one staged row (each column at most once)."""

    cells: list[CellEditIn] = Field(min_length=1, max_length=60)


class SheetColumnOut(_Out):
    """A column as uploaded: its letter and header, the field it fills (``target``, null when
    not imported), and whether its cells may be shown and edited. ``restricted`` columns fill a
    restricted (C3) field, or their header was suggested for one (even when the column is not
    imported): their values are never shown or edited here."""

    index: int
    letter: str
    header: str
    target: str | None
    restricted: bool
    editable: bool


class SheetCellOut(_Out):
    """``value`` is the display text (Aadhaar-like numbers masked; null when empty or
    restricted); ``edited`` marks a value changed in SchoolOS; ``formula`` a cell kept as inert
    text."""

    value: str | None
    edited: bool
    restricted: bool
    formula: bool


class SheetRowOut(_Out):
    """One data row as the spreadsheet numbers it, with its check result (``status`` null when
    the file was not checked with the current mapping yet)."""

    row_no: int
    cells: list[SheetCellOut]
    status: RowStatus | None
    errors: list[Issue]
    warnings: list[Issue]


class ImportSheetOut(_Out):
    """A page of the staged sheet (file order). ``editable`` is false once the import was added
    or reverted, while a check or commit is running, and for callers who cannot edit;
    ``read_only_reason`` says why. ``version`` is the import's ETag version (send it in
    ``If-Match`` to edit)."""

    import_id: uuid.UUID
    status: BatchStatus
    version: int
    editable: bool
    read_only_reason: SheetReadOnly | None
    header_row: int
    total_rows: int
    offset: int
    edited_cells: int
    columns: list[SheetColumnOut]
    data: list[SheetRowOut]
    next_cursor: str | None


class SheetEditOut(_Out):
    """The edited row after its re-check, the import's new version (ETag), its counts, and the
    other rows whose check result changed (e.g. a duplicate admission number resolved)."""

    row: SheetRowOut
    version: int
    status: BatchStatus
    row_count: int
    error_count: int
    changed_rows: list[int]


class PresetColumnOut(_Out):
    header: str
    target: str
    aliases: list[str]
    note: str


class PresetOut(_Out):
    """A packaged starting file of the import template library (FR-IMP-030)."""

    key: str
    version: int
    label_en: str
    label_te: str
    description_en: str
    import_source: Source
    template: bool = Field(description="A blank Excel template can be downloaded.")
    verified: bool = Field(
        description="False for formats owned by someone else until confirmed (ADR-0041)."
    )
    source: list[str]
    columns: list[PresetColumnOut]


class PresetColumnMatch(_Out):
    index: int
    header: str
    target: str | None


class PresetMappingOut(_Out):
    """A preset applied to an import's columns (FR-IMP-031): review it, then save it with
    ``PUT /imports/{id}/mapping``. Nothing is changed by this read."""

    preset: str
    import_source: Source
    source_matches: bool = Field(
        description="Whether the import was started from the preset's usual source."
    )
    columns: list[PresetColumnMatch]
    missing: list[str] = Field(description="Preset columns the file does not have.")


class TemplateOut(_Out):
    id: uuid.UUID
    name: str
    source: str
    headers: list[str]
    mapping: dict[str, str]
    created_by: uuid.UUID
    created_at: dt.datetime
    last_used_at: dt.datetime | None
    version: int
