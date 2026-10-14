"""Pydantic IO for data-quality routes (docs/09 Data quality; US-501, US-502)."""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

# needs_confirmation (A-01): a blocker a write without evidence removed; unresolved until a
# dq.findings.waive holder confirms it (POST /dq/findings/{id}/resolve, step-up).
FindingStatus = Literal["open", "resolved", "waived", "reopened", "needs_confirmation"]
SeverityName = Literal["blocker", "high", "medium", "low", "info"]
PROFILE_PATTERN = r"^[a-z0-9][a-z0-9-]{0,63}$"
RuleId = Literal[
    "DQ-001", "DQ-002", "DQ-003", "DQ-004", "DQ-005", "DQ-006",
    "DQ-007", "DQ-008", "DQ-009", "DQ-010", "DQ-011", "DQ-012",
    "DQ-021", "DQ-022", "DQ-030", "DQ-031",
]  # fmt: skip


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class _Out(BaseModel):
    model_config = ConfigDict(frozen=True)


# --- runs -----------------------------------------------------------------------------------------


class RunScopeIn(_In):
    """At most one of the fields; empty = every student you can see."""

    section_ids: list[uuid.UUID] | None = Field(default=None, min_length=1, max_length=100)
    class_ids: list[uuid.UUID] | None = Field(default=None, min_length=1, max_length=50)
    student_ids: list[uuid.UUID] | None = Field(default=None, min_length=1, max_length=2000)
    batch_id: uuid.UUID | None = None

    @model_validator(mode="after")
    def _one(self) -> RunScopeIn:
        given = [
            f for f in (self.section_ids, self.class_ids, self.student_ids, self.batch_id) if f
        ]
        if len(given) > 1:
            raise ValueError("choose sections, classes, students or one import batch")
        return self


class RunCreate(_In):
    scope: RunScopeIn = Field(default_factory=RunScopeIn)
    profile_key: str | None = Field(default=None, pattern=PROFILE_PATTERN)


class RunOut(_Out):
    id: uuid.UUID
    trigger: Literal["manual", "event"]
    event_type: str | None
    status: Literal["queued", "running", "completed", "failed"]
    profile_key: str | None
    scope: dict[str, Any]
    stats: dict[str, Any] | None
    created_at: dt.datetime
    started_at: dt.datetime | None
    finished_at: dt.datetime | None
    error_code: str | None


# --- findings -------------------------------------------------------------------------------------


class Bilingual(_Out):
    code: str
    en: str
    te: str


class FindingValue(_Out):
    """A compared value: always masked; ``value`` only for non-sensitive (C2) values that are
    still current (C3 values are revealed on the student record, with an audit event)."""

    attribute_key: str
    source: str
    value_id: uuid.UUID
    masked: str | None
    value: str | None
    sensitive: bool


class StudentRef(_Out):
    id: uuid.UUID
    display_name: str | None
    admission_no: str | None


class FindingOut(_Out):
    id: uuid.UUID
    student: StudentRef
    related_student_id: uuid.UUID | None
    rule_id: str
    rule_version: int
    profile_key: str | None
    attribute_key: str | None
    sources: list[str]
    match_class: str | None
    severity: SeverityName
    blocker: bool
    status: FindingStatus
    explanation: Bilingual
    match_explanation: Bilingual | None
    routes: list[Bilingual]
    values: list[FindingValue]
    details: dict[str, Any]
    resolution: Literal["note", "change_request", "auto_cleared"] | None
    resolution_note: str | None
    change_request_id: uuid.UUID | None
    resolved_by: uuid.UUID | None
    resolved_at: dt.datetime | None
    waived_by: uuid.UUID | None
    waived_at: dt.datetime | None
    waived_reason: str | None
    reopened_count: int
    first_seen_at: dt.datetime
    last_seen_at: dt.datetime
    first_seen_run_id: uuid.UUID | None
    last_seen_run_id: uuid.UUID | None
    version: int


class ResolveIn(_In):
    """US-502 AC1: a note, a change request, or both."""

    note: str | None = Field(default=None, min_length=1, max_length=1000)
    change_request_id: uuid.UUID | None = None

    @model_validator(mode="after")
    def _something(self) -> ResolveIn:
        if (self.note is None or not self.note.strip()) and self.change_request_id is None:
            raise ValueError("add a note or link a change request")
        return self


class WaiveIn(_In):
    reason: str = Field(min_length=3, max_length=1000)


class FindingFilters(_In):
    severity: list[SeverityName] | None = None
    rule_id: list[RuleId] | None = None
    status: list[FindingStatus] | None = None
    section_id: uuid.UUID | None = None
    student_id: uuid.UUID | None = None
    profile_key: str | None = Field(default=None, pattern=PROFILE_PATTERN)
    attribute_key: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_]*$")


# --- catalog and summary --------------------------------------------------------------------------


class SeverityPolicyOut(_Out):
    mode: Literal["fixed", "match_class"]
    level: SeverityName | None
    floor: SeverityName | None
    cap: SeverityName | None


class RuleOut(_Out):
    id: str
    version: int
    check: str
    scope: str
    attribute_keys: list[str]
    sources: list[str]
    requires_profile: bool
    severity: SeverityPolicyOut
    explanation: Bilingual
    routes: list[Bilingual]


class BoardFieldOut(_Out):
    """A column of the board's own form; ``attribute`` null when SchoolOS does not hold it."""

    name: str
    attribute: str | None
    required: bool
    note: str


class ProfileOut(_Out):
    key: str
    version: int
    label_en: str
    label_te: str
    required_fields: list[str]
    needs_apaar: bool
    board: str | None = Field(
        default=None, description="Board code (BSEAP, CBSE, CISCE); null for a portal."
    )
    classes: list[str] = Field(default_factory=list, description="Class codes it registers.")
    applies_to_classes: list[str] = Field(
        default_factory=list,
        description="The profile's classes that follow its board in this school (FR-TEN-021).",
    )
    verified: bool = Field(
        default=False,
        description="False until the format is confirmed against the official document "
        "(owner decision D3, ADR-0041).",
    )
    source: list[str] = Field(default_factory=list, description="Public sources of the format.")
    supersedes: str | None = None
    superseded: bool = Field(
        default=False, description="A newer cycle replaces it; it still works for old checks."
    )
    parent_verification_slip: bool = False
    board_fields: list[BoardFieldOut] = Field(default_factory=list)
    # A board/portal readiness check exists for this profile (GET /dq/readiness/{key}).
    readiness: bool = False


class RuleCount(_Out):
    rule_id: str
    severity: SeverityName
    count: int


class SummaryOut(_Out):
    """Open findings for the pre-check screen: blockers apart from warnings (US-501 AC2)."""

    profile_key: str | None
    blockers: int
    warnings: int
    students_with_blockers: int
    by_severity: dict[str, int]
    by_rule: list[RuleCount]
    last_run: RunOut | None


# --- board and portal readiness (FR-DQ-040..FR-DQ-046, ADR-0040) ---------------------------------

ReadinessStatus = Literal["ready", "needs_parent", "needs_school", "blocked"]
FixOwner = Literal["parent_aadhaar", "school_udise", "school_register", "unknown"]
ReadinessReason = Literal["mismatch", "undecided", "missing", "needs_confirmation"]


class ReadinessProfileOut(_Out):
    key: str
    version: int
    label_en: str
    label_te: str
    source: list[str]
    verified: bool
    classes: list[str]
    fields: list[str]


class ReadinessCounts(_Out):
    students: int
    ready: int
    needs_parent: int
    needs_school: int
    blocked: int


class ReadinessSectionOut(ReadinessCounts):
    section_id: uuid.UUID
    class_id: uuid.UUID


class ReadinessSummaryOut(_Out):
    """Class/section readiness ("142 of 160 ready", US-504 AC3), limited to your scope."""

    profile: ReadinessProfileOut
    totals: ReadinessCounts
    sections: list[ReadinessSectionOut]
    last_run: RunOut | None


class ReadinessStudentOut(_Out):
    """One student of a section: status and who must act (no values)."""

    student: StudentRef
    section_id: uuid.UUID | None
    status: ReadinessStatus
    owners: list[FixOwner]
    attribute_keys: list[str]
    open_items: int


class DiffSegment(_Out):
    """A piece of the character-level diff of the right value and another record."""

    op: Literal["equal", "insert", "delete", "replace"]
    reference: str
    other: str


class ReadinessValueOut(_Out):
    """A record's current value: ``value`` in clear only for C2 values, or C3 values when you
    hold ``student.read_sensitive`` for the student (audited); ``masked`` always."""

    source: str
    value: str | None
    masked: str | None
    sensitive: bool


class ReadinessItemOut(_Out):
    reason: ReadinessReason
    owner: FixOwner
    status: ReadinessStatus
    source: str | None
    against: str | None
    sources: list[str]
    kinds: list[str]
    advisory: bool
    waived: bool
    severity: SeverityName
    explanation: Bilingual
    owner_label: Bilingual
    kinds_text: Bilingual
    # Character-level detail: only when every value involved may be shown to you.
    segments: list[DiffSegment] | None
    changes: list[Bilingual] | None
    finding_id: uuid.UUID | None
    finding_status: FindingStatus | None


class ReadinessFieldOut(_Out):
    attribute_key: str
    reference: str | None
    values: list[ReadinessValueOut]
    items: list[ReadinessItemOut]


class ReadinessStudentDetailOut(_Out):
    profile: ReadinessProfileOut
    student: StudentRef
    section_id: uuid.UUID | None
    applies: bool
    status: ReadinessStatus
    values_shown: bool
    fields: list[ReadinessFieldOut]


class ReadinessRunIn(_In):
    """Sections, classes or students to check; empty = the profile's classes in your scope."""

    scope: RunScopeIn = Field(default_factory=RunScopeIn)
