"""Pydantic v2 IO models for documents (FR-DOC-001..006, FR-DOC-008, docs/09 Documents).

Inputs forbid unknown fields and NFC-normalise + trim text. Outputs are explicit allowlists: no
object keys, hashes or uploader file names are ever returned.
"""

from __future__ import annotations

import datetime as dt
import unicodedata
import uuid
from typing import Annotated, Any, Final, Literal, Self

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)

# Purposes a client may upload for; ``certificate`` documents are generated (FR-CERT-010).
UploadPurpose = Literal["evidence", "register_scan", "circular", "policy", "other", "import_file"]
Purpose = Literal[
    "evidence", "register_scan", "circular", "policy", "other", "import_file", "certificate"
]
DocType = Literal[
    "circular",
    "policy",
    "minutes",
    "register_scan",
    "certificate",
    "letter",
    "form",
    "report",
    "verified_answer",
    "other",
    "evidence",
    "import_file",
]
Sensitivity = Literal["C1", "C2", "C3"]
Language = Literal["en", "te", "mixed"]
PrincipalType = Literal["role", "section", "class", "membership"]
VersionStatus = Literal[
    "queued", "scanning", "extracting", "chunking", "embedding", "ready", "failed", "quarantined"
]
DocumentStatus = Literal["active", "archived"]

PURPOSES: Final[tuple[str, ...]] = (
    "evidence",
    "register_scan",
    "circular",
    "policy",
    "other",
    "import_file",
    "certificate",
)
MAX_ACL_ENTRIES: Final = 50


def nfc(value: Any) -> Any:
    if isinstance(value, str):
        return unicodedata.normalize("NFC", value).strip()
    return value


_NO_CONTROL = r"^[^\x00-\x1f\x7f]+$"
Title = Annotated[
    str, BeforeValidator(nfc), StringConstraints(min_length=1, max_length=200, pattern=_NO_CONTROL)
]
FileName = Annotated[
    str, BeforeValidator(nfc), StringConstraints(min_length=1, max_length=255, pattern=_NO_CONTROL)
]
ContentType = Annotated[
    str,
    BeforeValidator(nfc),
    StringConstraints(
        min_length=3, max_length=127, pattern=r"^[A-Za-z0-9!#$&^_.+-]+/[^\x00-\x1f]+$"
    ),
]
PrincipalRef = Annotated[
    str, BeforeValidator(nfc), StringConstraints(min_length=1, max_length=64, pattern=_NO_CONTROL)
]


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class _Out(BaseModel):
    model_config = ConfigDict(from_attributes=True, frozen=True)


# --- inputs ---------------------------------------------------------------------------------


class UploadCreate(_In):
    """Ask for a presigned POST. ``document_id`` asks to upload a new version of that document."""

    filename: FileName
    content_type: ContentType
    size_bytes: int = Field(ge=1, le=100 * 1024 * 1024)
    purpose: UploadPurpose
    document_id: uuid.UUID | None = None


class AclEntry(_In):
    principal_type: PrincipalType
    principal_ref: PrincipalRef


class DocumentCreate(_In):
    """Register an uploaded object as a new document (metadata FR-DOC-005 + ACL)."""

    upload_id: uuid.UUID
    title: Title
    doc_type: DocType | None = None
    issuer: Title | None = None
    issued_on: dt.date | None = None
    academic_year_id: uuid.UUID | None = None
    language: Language | None = None
    sensitivity: Sensitivity | None = None
    acl: list[AclEntry] = Field(default_factory=list, max_length=MAX_ACL_ENTRIES)


class VersionCreate(_In):
    upload_id: uuid.UUID


class AclUpdate(_In):
    acl: list[AclEntry] = Field(max_length=MAX_ACL_ENTRIES)


class DocumentUpdate(_In):
    """Change a document's metadata (FR-DOC-005): send only what changes, at least one field.

    ``title`` and ``doc_type`` cannot be null; ``issuer``, ``issued_on`` and ``language`` may be
    cleared with ``null``. The type must suit the document's purpose (422
    ``doc_type_not_allowed_for_purpose``).
    """

    title: Title | None = None
    doc_type: DocType | None = None
    language: Language | None = None
    issuer: Title | None = None
    issued_on: dt.date | None = None

    @model_validator(mode="after")
    def _something_to_change(self) -> Self:
        if not self.model_fields_set:
            raise ValueError("send at least one field to change")
        for name in ("title", "doc_type"):
            if name in self.model_fields_set and getattr(self, name) is None:
                raise ValueError(f"{name} cannot be null")
        return self


# --- outputs --------------------------------------------------------------------------------


class UploadOut(_Out):
    upload_id: uuid.UUID
    url: str
    fields: dict[str, str]
    expires_at: dt.datetime
    max_bytes: int
    purpose: Purpose
    document_id: uuid.UUID | None = None
    batch_id: uuid.UUID | None = None


class AclEntryOut(_Out):
    principal_type: PrincipalType
    principal_ref: str


class UploaderOut(_Out):
    """Who uploaded: the staff member's membership id and display name only (no contact
    details). ``null`` in ``uploaded_by`` when the account is no longer a member here."""

    membership_id: uuid.UUID
    display_name: str


class VersionOut(_Out):
    id: uuid.UUID
    version_no: int
    mime_type: str
    size_bytes: int
    status: VersionStatus
    error: str | None
    created_at: dt.datetime
    uploaded_by: UploaderOut | None = None
    uploaded_by_me: bool = False


class DocumentOut(_Out):
    id: uuid.UUID
    purpose: Purpose
    doc_type: DocType
    title: str
    issuer: str | None
    issued_on: dt.date | None
    academic_year_id: uuid.UUID | None
    language: Language | None
    sensitivity: Sensitivity
    status: DocumentStatus
    current_version: VersionOut | None
    acl: list[AclEntryOut]
    created_by: uuid.UUID
    uploaded_by: UploaderOut | None = Field(
        default=None, description="Who registered the document (version 1)."
    )
    uploaded_by_me: bool = Field(default=False, description="You registered this document.")
    created_at: dt.datetime
    updated_at: dt.datetime
    version: int


class DocumentDetail(DocumentOut):
    versions: list[VersionOut]
    allowed_doc_types: list[DocType] = Field(
        default_factory=list,
        description="Document types that suit this document's purpose; PATCH answers 422 "
        "``doc_type_not_allowed_for_purpose`` for any other (FR-DOC-005).",
    )


class DownloadUrlOut(_Out):
    url: str
    expires_at: dt.datetime
    version_no: int
    mime_type: str
    filename: str


# --- sheets (FR-DOC-009..011) ----------------------------------------------------------------

SheetFormat = Literal["csv", "xlsx"]
SheetReadOnly = Literal[
    "no_permission", "not_versionable", "archived", "several_sheets", "formulas", "newer_version"
]


def _cell_value(value: Any) -> Any:
    """NFC; surrounding spaces trimmed; blank means "clear the cell"."""
    if isinstance(value, str):
        text = unicodedata.normalize("NFC", value).strip()
        return text or None
    return value


# The length limit applies to text only: null (and blank, after trimming) clears the cell.
SheetCellValue = Annotated[
    Annotated[str, Field(max_length=1000)] | None, BeforeValidator(_cell_value)
]


class SheetCellEdit(_In):
    """One edited cell: ``row_no`` as the sheet shows it (data rows start at 2; row 1 is the
    header row), 0-based ``column``, the new text (``null`` or blank clears it). No line breaks
    or control characters, never a full Aadhaar number."""

    row_no: int = Field(ge=2, le=1_000_000)
    column: int = Field(ge=0, le=255)
    value: SheetCellValue = None


class SheetSaveIn(_In):
    """Save edited cells as the document's next version. ``base_version_no`` is the version
    the edits were made on; it must still be the current version."""

    base_version_no: int = Field(ge=1, le=999_999)
    edits: list[SheetCellEdit] = Field(min_length=1, max_length=5000)


class SheetExportIn(_In):
    """Download the sheet of ``base_version_no`` (default: the one shown) with ``edits``
    applied (unsaved edits may be included)."""

    format: SheetFormat = "csv"
    base_version_no: int | None = Field(default=None, ge=1, le=999_999)
    edits: list[SheetCellEdit] = Field(default_factory=list, max_length=5000)


class DocSheetColumnOut(_Out):
    index: int
    letter: str
    header: str | None


class DocSheetCellOut(_Out):
    """Display text (Aadhaar-like numbers masked; null when empty); ``formula`` marks a cell
    kept as inert formula text (never evaluated)."""

    value: str | None
    formula: bool


class DocSheetRowOut(_Out):
    row_no: int
    cells: list[DocSheetCellOut]


class DocumentSheetOut(_Out):
    """A page of the first worksheet of the newest checked version (row 1 is the header).
    ``sheet_count`` > 1 means the workbook has more sheets that are not shown. ``editable``
    says whether you can save edits as a new version; ``read_only_reason`` says why not.
    ``version`` is the document's ETag version (send it in ``If-Match`` to save)."""

    document_id: uuid.UUID
    version_no: int
    version: int
    kind: SheetFormat
    sheet_count: int
    editable: bool
    read_only_reason: SheetReadOnly | None
    total_rows: int
    offset: int
    columns: list[DocSheetColumnOut]
    data: list[DocSheetRowOut]
    next_cursor: str | None
