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
