"""Pydantic IO of the enrolment statistics sheet (FO-06; FR-EXP-009..019) and the government
scheme verification pack (FO-11; FR-EXP-020..030). Schema names are module-specific (no
``app__`` qualified names in the OpenAPI document)."""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints


class _Out(BaseModel):
    model_config = ConfigDict(frozen=True)


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


# --- FO-06 enrolment statistics ---------------------------------------------------------------

EnrolmentStatsTableKey = Literal[
    "gender", "age", "category", "minority", "cwsn", "repeaters", "movement"
]


class EnrolmentStatsColumnOut(_Out):
    key: str
    label: str


class EnrolmentStatsRowOut(_Out):
    class_id: uuid.UUID | None = Field(description="Null on the totals row")
    label: str = Field(description="Class name, or 'Total'")
    counts: list[int] = Field(description="One count per column, in column order")
    total: int = Field(description="Row total (students on the row, not the sum of columns)")


class EnrolmentStatsTableOut(_Out):
    key: EnrolmentStatsTableKey
    title: str
    restricted: bool = Field(
        description="Counted from restricted (C3) values (category, religion, disability)"
    )
    columns: list[EnrolmentStatsColumnOut]
    rows: list[EnrolmentStatsRowOut]
    totals: EnrolmentStatsRowOut


class EnrolmentStatsGapOut(_Out):
    attribute_key: str
    label: str
    count: int = Field(description="Students counted on the sheet without this value")


class EnrolmentStatsYearOut(_Out):
    id: uuid.UUID
    label: str
    starts_on: dt.date
    ends_on: dt.date


class EnrolmentStatisticsOut(_Out):
    """Counts only: no names, no student ids (FR-EXP-009)."""

    year: EnrolmentStatsYearOut
    as_of: dt.date = Field(description="Enrolment and age are counted on this day")
    reference_date: dt.date = Field(description="The configured default day of this year")
    students: int = Field(description="Students enrolled on the day")
    verified: bool = Field(description="False: the UDISE+ shapes are not verified yet")
    source: list[str]
    restricted_hidden: bool = Field(
        description="True when the category, minority and CWSN tables are left out because "
        "the caller lacks school-wide student.read_sensitive"
    )
    tables: list[EnrolmentStatsTableOut]
    gaps: list[EnrolmentStatsGapOut]


# --- FO-11 scheme verification pack -----------------------------------------------------------

SchemeKey = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{0,63}$")]
SchemeCertificateType = Literal["study", "bonafide"]
PurposeNote = Annotated[str, StringConstraints(min_length=1, max_length=120)]


class SchemeOut(_Out):
    key: str
    label: str
    certificate_type: SchemeCertificateType
    purpose_note: str
    note: str
    verified: bool
    source: list[str]


class SchemePackConfigOut(_Out):
    verified: bool
    note: str
    present_statuses: list[str]
    excluded_statuses: list[str]
    max_certificates: int
    schemes: list[SchemeOut]


class SchemeAttendanceStudentOut(_Out):
    student_id: uuid.UUID
    roll_no: str | None
    full_name: str | None
    admission_no: str | None
    days_counted: int = Field(description="Marked days, leave days left out")
    days_present: int = Field(description="Present or late")
    leave_days: int
    percent: float | None = Field(description="days_present / days_counted x 100, 1 decimal")


class SchemeAttendanceListOut(_Out):
    """Names, admission numbers and the percentage only (FR-EXP-021); no eligibility."""

    scheme: SchemeOut
    section_id: uuid.UUID
    section_label: str
    academic_year_label: str
    from_date: dt.date
    to_date: dt.date
    students: list[SchemeAttendanceStudentOut]


class SchemeCertificatesIn(_In):
    """Issue one study or bonafide certificate per chosen student for a scheme (FR-EXP-024).
    ``purpose_note`` defaults to the scheme's."""

    scheme_key: SchemeKey
    student_ids: list[uuid.UUID] = Field(min_length=1, max_length=200)
    certificate_type: SchemeCertificateType | None = Field(
        default=None, description="Defaults to the scheme's certificate type"
    )
    purpose_note: PurposeNote | None = None


SchemeCertificateStatus = Literal["issued", "pending", "refused"]


class SchemeCertificateResultOut(_Out):
    student_id: uuid.UUID
    status: SchemeCertificateStatus
    certificate_id: uuid.UUID | None
    serial: str | None
    error_code: str | None = Field(
        description="Why it was not issued (e.g. not_found, certificate_blocked)"
    )


class SchemeCertificatesOut(_Out):
    scheme_key: str
    certificate_type: SchemeCertificateType
    issued: int
    refused: int
    results: list[SchemeCertificateResultOut]
