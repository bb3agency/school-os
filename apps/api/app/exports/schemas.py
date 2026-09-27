"""Pydantic IO for export routes (docs/09 Exports; US-501 AC4, US-901, FR-EXP-001..004)."""

from __future__ import annotations

import datetime as dt
import re
import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

ExportKind = Literal["board_precheck", "portal_precheck", "student_list"]
ExportStatus = Literal["queued", "running", "ready", "failed", "expired"]
PrecheckFormat = Literal["xlsx", "pdf"]
ListFormat = Literal["csv", "xlsx"]
FileFormat = Literal["xlsx", "pdf", "csv"]
Language = Literal["en", "te"]
PROFILE_PATTERN = r"^[a-z0-9][a-z0-9-]{0,63}$"
COLUMN_PATTERN = r"^[a-z][a-z0-9_]{0,63}$"


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class _Out(BaseModel):
    model_config = ConfigDict(frozen=True)


class ExportScopeIn(_In):
    """Sections or classes of the current academic year (at most one of the two); empty = every
    student you can see."""

    section_ids: list[uuid.UUID] | None = Field(default=None, min_length=1, max_length=100)
    class_ids: list[uuid.UUID] | None = Field(default=None, min_length=1, max_length=50)

    @model_validator(mode="after")
    def _one(self) -> ExportScopeIn:
        if self.section_ids and self.class_ids:
            raise ValueError("choose sections or classes, not both")
        return self


def _unique[T](values: list[T]) -> list[T]:
    if len(set(values)) != len(values):
        raise ValueError("list each item once")
    return values


class PrecheckCreate(_In):
    """US-501 AC4: a board or portal pre-check report."""

    profile_key: str = Field(pattern=PROFILE_PATTERN)
    scope: ExportScopeIn = Field(default_factory=ExportScopeIn)
    format: list[PrecheckFormat] = Field(default=["xlsx", "pdf"], min_length=1, max_length=2)
    language: Language = "en"
    include_sensitive: bool = False

    _formats = field_validator("format")(_unique)


class StudentListCreate(_In):
    """A student list with chosen columns (``student.export``, step-up)."""

    columns: list[str] = Field(min_length=1, max_length=60)
    scope: ExportScopeIn = Field(default_factory=ExportScopeIn)
    format: ListFormat = "xlsx"
    language: Language = "en"

    @field_validator("columns")
    @classmethod
    def _columns(cls, value: list[str]) -> list[str]:
        if not all(re.match(COLUMN_PATTERN, c) for c in value):
            raise ValueError("columns are attribute keys or class, section, roll_no")
        return _unique(value)


class ExportFileOut(_Out):
    format: FileFormat
    content_type: str
    size_bytes: int


class ExportOut(_Out):
    id: uuid.UUID
    kind: ExportKind
    profile_key: str | None
    profile_version: int | None
    layout_version: int
    formats: list[FileFormat]
    language: Language
    scope: dict[str, list[uuid.UUID]]
    columns: list[str] | None
    include_sensitive: bool
    student_count: int
    status: ExportStatus
    error_code: str | None
    created_at: dt.datetime
    started_at: dt.datetime | None
    finished_at: dt.datetime | None
    expires_at: dt.datetime | None
    files: list[ExportFileOut]


class ExportProfileOut(_Out):
    key: str
    kind: Literal["board", "portal"]
    permission: str
    version: int
    layout_version: int
    label_en: str
    label_te: str
    fields: list[str]
    required_fields: list[str]
    allowed: bool


class ExportDownloadOut(_Out):
    url: str
    expires_at: dt.datetime
    format: FileFormat
    content_type: str
    filename: str
