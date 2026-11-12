"""Data-principal access report contribution of the certificates module (R3-1A; FR-DPDP-009,
ADR-0048 decision 6, PRV-031).

A student's certificates: type, serial number, request and issue dates, status and how many
times the printed codes were checked. Never the verification codes, handles, content hashes or
the certificate content itself. Certificates are official records: this module never erases.
"""

from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from app.certificates import repository as repo
from app.certificates import tc_requests_repository as tc_repo
from app.certificates.config import load_config
from app.core.report_format import cap_rows, fmt_count, fmt_date, humanise
from app.core.subjects import MAX_SECTION_ROWS, ReportSection, Subject

CLASS_KEY = "certificates"
COLUMNS = (
    "Certificate",
    "Serial number",
    "Requested on",
    "Issued on",
    "Status",
    "Duplicate",
    "Times checked online",
)
TC_REQUEST_COLUMNS = ("Received on", "How it was received", "Status", "Ready on")


def _label(certificate_type: str) -> str:
    spec = load_config().types.get(certificate_type)
    return spec.label_en if spec is not None else humanise(certificate_type)


def subject_report(session: Session, subject: Subject) -> list[ReportSection]:
    """The student's certificates and TC requests (FO-09), newest first; [] for other subject
    kinds or none held."""
    if subject.kind != "student":
        return []
    found, total = repo.certificates_of_student(session, subject.id, limit=MAX_SECTION_ROWS)
    if total == 0:
        return _tc_requests(session, subject.id)
    rows = [
        (
            _label(c.certificate_type),
            c.serial or "Not numbered yet",
            fmt_date(c.requested_at),
            fmt_date(c.issued_at),
            humanise(c.status),
            f"Duplicate {c.duplicate_no}" if c.duplicate_no else "No",
            fmt_count(checks),
        )
        for c, checks in found
    ]
    kept, note = cap_rows(rows, total)
    sections = [
        ReportSection(
            class_key=CLASS_KEY,
            title="Certificates",
            columns=COLUMNS,
            rows=tuple(kept),
            note=note,
        )
    ]
    return sections + _tc_requests(session, subject.id)


def _tc_requests(session: Session, student_id: uuid.UUID) -> list[ReportSection]:
    """FO-09: the student's TC requests (when received, how, status); never staff text."""
    found = tc_repo.requests_of_student(session, student_id, limit=MAX_SECTION_ROWS + 1)
    if not found:
        return []
    rows = [
        (fmt_date(r.received_on), humanise(r.channel), humanise(r.status), fmt_date(r.ready_at))
        for r in found
    ]
    kept, note = cap_rows(rows, len(rows))
    return [
        ReportSection(
            class_key=CLASS_KEY,
            title="Transfer certificate requests",
            columns=TC_REQUEST_COLUMNS,
            rows=tuple(kept),
            note=note,
        )
    ]


def certificate_ids_of_student(session: Session, student_id: uuid.UUID) -> list[uuid.UUID]:
    """The ids of the student's certificates (what certificate check codes point at; used by
    ``app.public`` for the access report, FR-DPDP-009)."""
    return repo.certificate_ids_of_student(session, student_id)


__all__ = ["certificate_ids_of_student", "subject_report"]
