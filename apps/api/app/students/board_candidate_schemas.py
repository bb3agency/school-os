"""Pydantic IO of the board candidate grid (FO-10; FR-STU-026..029)."""

from __future__ import annotations

import uuid
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

CandidateSource = Literal["manual_entry", "parent_form", "board_registration"]
_CODE = r"^[A-Za-z0-9][A-Za-z0-9_]{1,15}$"


class BoardCodeOut(BaseModel):
    code: str
    label: str


class BoardLanguageSlotOut(BaseModel):
    slot: str
    required: bool
    options: list[BoardCodeOut]


class BoardSubjectGroupOut(BaseModel):
    key: str
    label_en: str
    required: bool
    min: int
    max: int
    options: list[BoardCodeOut]


class BoardSchemeOut(BaseModel):
    """One board scheme of ``board_catalogue.yaml`` (``verified: false`` until confirmed)."""

    key: str
    version: int
    label_en: str
    board: str
    classes: list[str]
    verified: bool
    source: list[str]
    source_note: str
    medium_required: bool
    medium_options: list[str]
    languages: list[BoardLanguageSlotOut]
    subjects: list[BoardSubjectGroupOut]
    max_subjects: int
    photo_required: bool
    keys: list[str] = Field(description="Attribute keys this scheme asks for (grid columns).")


class BoardCandidateIssueOut(BaseModel):
    field: str
    reason: Literal["missing", "not_in_list", "repeated", "group_missing", "too_many", "photo"]
    code: str | None = None
    group: str | None = None


class BoardCandidateRowOut(BaseModel):
    student_id: uuid.UUID
    admission_no: str | None
    display_name: str | None
    roll_no: str | None
    values: dict[str, str | None]
    issues: list[BoardCandidateIssueOut]
    ready: bool


class BoardCandidateGridOut(BaseModel):
    section_id: uuid.UUID
    class_section: str | None
    class_code: str | None
    scheme: BoardSchemeOut | None
    available_schemes: list[str]
    rows: list[BoardCandidateRowOut]
    ready_count: int


class BoardCandidateEntryIn(BaseModel):
    """One student's choices; a field left out (null) is not changed. Values cannot be cleared:
    a correction is a new value (history kept, FR-STU-003)."""

    model_config = ConfigDict(extra="forbid")

    student_id: uuid.UUID
    exam_medium: str | None = Field(default=None, max_length=16)
    first_language: str | None = Field(default=None, pattern=_CODE)
    second_language: str | None = Field(default=None, pattern=_CODE)
    third_language: str | None = Field(default=None, pattern=_CODE)
    board_subjects: list[Annotated[str, Field(max_length=16, pattern=_CODE)]] | None = Field(
        default=None, max_length=10
    )
    photo_signature_ready: bool | None = None


class BoardCandidateSaveIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scheme: str = Field(pattern=r"^[a-z][a-z0-9-]{1,39}$")
    source: CandidateSource = "manual_entry"
    rows: list[BoardCandidateEntryIn] = Field(min_length=1, max_length=200)


class BoardCandidateSaveOut(BaseModel):
    recorded: int = Field(description="Values recorded (a new or changed value).")
    unchanged: int = Field(description="Values that were already recorded as sent.")
    grid: BoardCandidateGridOut


class BoardStudentCandidateOut(BaseModel):
    """One student's board candidate choices (student profile)."""

    student_id: uuid.UUID
    section_id: uuid.UUID | None
    class_section: str | None
    scheme: BoardSchemeOut | None
    values: dict[str, str | None]
    issues: list[BoardCandidateIssueOut]
    ready: bool
