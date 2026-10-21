"""Student records in a data principal's access report (R3-1A; FR-DPDP-009, PRV-031; ADR-0048
decision 6).

- **Student** (``student_identity``): the record itself (status, admission number), every
  recorded detail with its source and verification, history included (C3 values of the
  student's own record decrypted; Aadhaar as-printed details only as "held", the Aadhaar
  reference only as "Last 4 digits held"), and the enrolments. Under ``guardians``: the parents
  and guardians on the record with relationship and whether a phone or address is held (their
  numbers and addresses are theirs: each guardian's own report shows them).
- **Guardian** (``guardians``): the guardian's own details (name, phone and address decrypted,
  language) and the linked children with relationship and admission number only.

Not an eraser: the admission record is an official record (``refused_official_record``).
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from typing import Final

from sqlalchemy.orm import Session

from app.core.report_format import (
    AADHAAR_HELD,
    cap_rows,
    fmt_bool,
    fmt_date,
    fmt_text,
    humanise,
)
from app.core.subjects import ReportSection, Subject
from app.students import crypto
from app.students import repository as repo
from app.students.models import AttributeValue, Guardian
from app.tenancy import service as tenancy

IDENTITY: Final = "student_identity"
GUARDIANS: Final = "guardians"
LAST4_HELD: Final = "Last 4 digits held"
AADHAAR_SOURCE: Final = "aadhaar_as_printed"
_GUARDIANS_TABLE: Final = "sis.guardians"
_LANGUAGES: Final[Mapping[str, str]] = {"en": "English", "te": "Telugu"}


def _value(session: Session, row: AttributeValue) -> str:
    if row.attribute_key == "aadhaar_last4":
        return LAST4_HELD
    if row.source == AADHAAR_SOURCE or row.attribute_key.startswith("aadhaar_"):
        return AADHAAR_HELD
    if row.value_date is not None:
        return fmt_date(row.value_date)
    from app.students import service as _svc  # noqa: PLC0415 - service re-exports this module

    return fmt_text(_svc._plain(session, row))  # C3 decrypted: the subject's own record


def _record_section(session: Session, student_id: uuid.UUID) -> ReportSection | None:
    student = repo.get_student(session, student_id)
    if student is None:
        return None
    return ReportSection(
        class_key=IDENTITY,
        title="Student record",
        columns=("Detail", "Value"),
        rows=(
            ("Admission number", fmt_text(student.admission_no)),
            ("Status", humanise(student.status)),
            ("Record created", fmt_date(student.created_at)),
            ("Last changed", fmt_date(student.updated_at)),
        ),
    )


def _values_section(session: Session, student_id: uuid.UUID) -> ReportSection:
    labels = {d.key: d.label_en for d in repo.list_definitions(session)}
    rows = [
        (
            labels.get(v.attribute_key) or humanise(v.attribute_key),
            humanise(v.source),
            _value(session, v),
            humanise(v.verification_status),
            fmt_bool(v.superseded_by is None),
            fmt_date(v.recorded_at),
        )
        for v in repo.values_of_student(session, student_id)
    ]
    kept, note = cap_rows(rows)
    return ReportSection(
        class_key=IDENTITY,
        title="Recorded details and their sources",
        columns=("Detail", "Source", "Value", "Verification", "Current", "Recorded on"),
        rows=tuple(kept),
        note=note,
    )


def _enrolments_section(session: Session, student_id: uuid.UUID) -> ReportSection | None:
    enrolments = repo.enrollments_of(session, student_id)
    if not enrolments:
        return None
    years = {y.id: y.label for y in tenancy.list_academic_years(session)}
    classes = {c.id: c.display_en for c in tenancy.list_classes(session)}
    sections = {s.id: s for s in tenancy.list_sections(session)}

    def place(section_id: uuid.UUID) -> str:
        section = sections.get(section_id)
        if section is None:
            return fmt_text(None)
        return f"{classes.get(section.class_id, '')} {section.name}".strip()

    rows = [
        (
            fmt_text(years.get(e.academic_year_id)),
            place(e.section_id),
            fmt_text(e.roll_no),
            humanise(e.status),
            fmt_date(e.started_on),
            fmt_date(e.ended_on),
        )
        for e in enrolments
    ]
    kept, note = cap_rows(rows)
    return ReportSection(
        class_key=IDENTITY,
        title="Enrolments",
        columns=("Academic year", "Class and section", "Roll no.", "Status", "From", "To"),
        rows=tuple(kept),
        note=note,
    )


def _student_guardians_section(session: Session, student_id: uuid.UUID) -> ReportSection | None:
    links = repo.guardians_of(session, student_id)
    if not links:
        return None
    rows = [
        (
            fmt_text(g.full_name),
            humanise(link.relationship),
            fmt_bool(link.is_primary),
            fmt_bool(g.phone_ciphertext is not None),
            fmt_bool(g.address_ciphertext is not None),
        )
        for g, link in links
    ]
    kept, note = cap_rows(rows)
    return ReportSection(
        class_key=GUARDIANS,
        title="Parents and guardians on the record",
        columns=("Name", "Relationship", "Main contact", "Phone held", "Address held"),
        rows=tuple(kept),
        note=note,
    )


def _decrypt(session: Session, guardian: Guardian, column: str) -> str:
    blob: bytes | None = getattr(guardian, column)
    if blob is None:
        return fmt_text(None)
    return fmt_text(
        crypto.decrypt_value(
            session, blob, table=_GUARDIANS_TABLE, column=column, row_id=guardian.id
        )
    )


def _guardian_sections(session: Session, guardian_id: uuid.UUID) -> list[ReportSection]:
    guardian = repo.get_guardian(session, guardian_id)
    if guardian is None:
        return []
    language = _LANGUAGES.get(guardian.preferred_language or "", "The school's language")
    details = ReportSection(
        class_key=GUARDIANS,
        title="Parent or guardian details",
        columns=("Detail", "Value"),
        rows=(
            ("Name", fmt_text(guardian.full_name)),
            ("Phone", _decrypt(session, guardian, "phone_ciphertext")),
            ("Address", _decrypt(session, guardian, "address_ciphertext")),
            ("Language for messages", language),
            ("Record created", fmt_date(guardian.created_at)),
            ("Last changed", fmt_date(guardian.updated_at)),
        ),
    )
    children = [
        (humanise(link.relationship), fmt_text(admission_no), fmt_bool(link.is_primary))
        for link, admission_no in repo.children_of_guardian(session, guardian_id)
    ]
    kept, note = cap_rows(children)
    linked = ReportSection(
        class_key=GUARDIANS,
        title="Linked children",
        columns=("Relationship", "Admission number", "Main contact"),
        rows=tuple(kept),
        note=note,
    )
    return [details, linked]


def subject_report(session: Session, subject: Subject) -> list[ReportSection]:
    """The student's record or the guardian's own details ([] for members or unknown ids)."""
    if subject.kind == "guardian":
        return _guardian_sections(session, subject.id)
    if subject.kind != "student":
        return []
    record = _record_section(session, subject.id)
    if record is None:
        return []
    optional = (
        _enrolments_section(session, subject.id),
        _student_guardians_section(session, subject.id),
    )
    return [
        record,
        _values_section(session, subject.id),
        *(s for s in optional if s is not None),
    ]


__all__ = ["subject_report"]
