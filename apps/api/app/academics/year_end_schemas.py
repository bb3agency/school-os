"""Pydantic IO for the year-end progression pack (FT-08; lead decision D85; FR-YRE-*; docs/09
Year-end results). Text is NFC-normalised and trimmed on input.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.academics.schemas import RecordSectionOut, RosterStudentOut, Text


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class _Out(BaseModel):
    model_config = ConfigDict(frozen=True)


YearResultCode = Literal["passed", "promoted", "detained", "not_appeared"]
YearEndWarningCode = Literal["no_detention_class"]
ProgressionBlank = Literal["result", "percent", "days_attended", "udise_pen"]


class YearEndRulesOut(_Out):
    """The proposal rule in force (``app/academics/config.yaml`` ``year_end``)."""

    rule_version: int
    pass_percent: float = Field(
        description="Overall percentage at or above which 'passed' is proposed"
    )
    pass_percent_verified: bool = Field(
        description="False until a pilot school confirms the pass mark (owner decision D3)"
    )
    no_detention_class_codes: list[str]
    no_detention_verified: bool = Field(
        description="False until the state's no-detention order is confirmed (D3); the rule "
        "only warns, it never refuses a decision"
    )


class YearProposalOut(_Out):
    """What SchoolOS proposes from the records (never applied by itself; FR-YRE-003)."""

    result: YearResultCode | None = Field(description="None: no marks in this year")
    percent: float | None = Field(
        description="Sum of marks / sum of maximum marks over the year's exams"
    )
    exams_counted: int
    days_attended: int | None = Field(description="Days marked present or late in the year")
    working_days: int | None = Field(
        description="Days the student's section was marked while the student was in it (until "
        "the school calendar gives working days)"
    )


class YearConfirmedOut(_Out):
    """The result a person confirmed, with the figures shown then (FR-YRE-004)."""

    result: YearResultCode
    proposed_result: YearResultCode | None
    reason: str | None
    percent: float | None
    days_attended: int | None
    working_days: int | None
    rule_version: int
    confirmed_by_name: str | None
    confirmed_at: dt.datetime
    version: int


class YearResultStudentOut(_Out):
    student: RosterStudentOut
    proposal: YearProposalOut
    confirmed: YearConfirmedOut | None
    warnings: list[YearEndWarningCode]


class YearResultCountsOut(_Out):
    students: int
    confirmed: int
    unconfirmed: int
    passed: int
    promoted: int
    detained: int
    not_appeared: int


class YearResultsOut(_Out):
    section: RecordSectionOut
    academic_year_label: str
    no_detention_class: bool = Field(
        description="The section's class is in the no-detention list (config, unverified)"
    )
    rules: YearEndRulesOut
    counts: YearResultCountsOut
    students: list[YearResultStudentOut]


class YearResultIn(_In):
    student_id: uuid.UUID
    result: YearResultCode
    reason: Text | None = Field(
        default=None,
        max_length=500,
        description="Why the student is detained (required for 'detained'; optional otherwise). "
        "No Aadhaar numbers.",
    )
    expected_version: int | None = Field(
        default=None,
        ge=0,
        le=2_147_483_647,
        description="The confirmed result's ``version`` (0: none yet). When sent, the save is "
        "refused (412 ``year_result_changed``) if someone else changed it since.",
    )


class YearResultsWrite(_In):
    entries: list[YearResultIn] = Field(min_length=1, max_length=200)


class YearEndWarningOut(_Out):
    code: YearEndWarningCode
    student_id: uuid.UUID


class YearResultsWriteOut(_Out):
    written: int = Field(description="Results confirmed or changed")
    unchanged: int
    warnings: list[YearEndWarningOut]


class HeldBackOut(_Out):
    """Held-back suggestions for the promotion screen (FR-YRE-008): students whose confirmed
    result is ``detained``. The promotion itself is unchanged: the person reviews the list and
    commits through the promotion flow."""

    academic_year_id: uuid.UUID
    held_back_student_ids: list[uuid.UUID]
    no_detention_student_ids: list[uuid.UUID] = Field(
        description="Held-back students in a no-detention class (warning only)"
    )
    confirmed: int
    unconfirmed: int = Field(description="Students finishing the year without a confirmed result")
    no_detention_verified: bool


class ProgressionRowOut(_Out):
    student_id: uuid.UUID
    admission_no: str | None
    full_name: str | None
    udise_pen: str | None
    class_code: str
    section_label: str
    result: YearResultCode | None = Field(description="Confirmed result only")
    percent: float | None
    days_attended: int | None
    working_days: int | None
    blanks: list[ProgressionBlank]


class ProgressionOut(_Out):
    """UDISE+ progression check sheet of one year (FR-YRE-010): the previous year's class,
    result, % marks and days attended per student, with the blanks to fill first."""

    academic_year_id: uuid.UUID
    academic_year_label: str
    students: int
    blank_counts: dict[str, int]
    rows: list[ProgressionRowOut]


class TcSuggestion(_Out):
    """A TC input SchoolOS can suggest from its records (FR-YRE-014)."""

    key: Literal["promotion", "working_days", "days_attended"]
    value: str
    source: Literal["year_result", "attendance_register"]


__all__ = [
    "HeldBackOut",
    "ProgressionBlank",
    "ProgressionOut",
    "ProgressionRowOut",
    "TcSuggestion",
    "YearConfirmedOut",
    "YearEndRulesOut",
    "YearEndWarningCode",
    "YearEndWarningOut",
    "YearProposalOut",
    "YearResultCode",
    "YearResultCountsOut",
    "YearResultIn",
    "YearResultStudentOut",
    "YearResultsOut",
    "YearResultsWrite",
    "YearResultsWriteOut",
]
