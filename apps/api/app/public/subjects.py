"""Data-principal access report contribution of the public handles (R3-1A; FR-DPDP-009,
ADR-0048 decision 6, PRV-031).

- A guardian: the parent links sent to them (kind, sent, expires, status, times opened).
- A student: the parent links that point at the student's fee account or receipts (whichever
  guardian they were sent to), and the number of certificate check codes of the student's
  certificates.

Never a key, key hash, handle id or URL: only stored hashes exist and a link is shown by its
kind and dates. Expired parent links are deleted 30 days after expiry by the daily purge, so the
report shows the active and recent ones. This module never erases here (the purge does).

The student's fee accounts, receipts and certificates belong to ``app.fees`` and
``app.certificates``; their ids are read through those services, imported inside the function
because both import this module's service at load time.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Collection, Mapping
from typing import Final

from sqlalchemy.orm import Session

from app.core.report_format import EMPTY, cap_rows, fmt_count, fmt_date, fmt_datetime, humanise
from app.core.subjects import MAX_SECTION_ROWS, ReportSection, Subject
from app.public import repository as repo
from app.public.config import PARENT_KINDS, PARENT_TARGET_TYPES
from app.public.models import PublicHandle

CLASS_KEY: Final = "public_links"
CERT_KIND: Final = "cert_verify"
CERT_TARGET: Final = "certificate"
_LABELS: Final[dict[str, str]] = {
    "parent_notice": "Notice link",
    "parent_receipt": "Fee receipt link",
    "parent_dues": "Fee dues link",
    "parent_optin": "WhatsApp opt-in link",
}
COLUMNS: Final = ("Link", "Sent on", "Expires on", "Status", "Times opened", "Last opened")


def _status(row: PublicHandle, now: dt.datetime) -> str:
    if row.revoked_at is not None:
        return "Withdrawn"
    if row.expires_at is not None and row.expires_at <= now:
        return "Expired"
    return "Active"


def _links_section(
    session: Session,
    *,
    title: str,
    guardian_id: uuid.UUID | None,
    targets: Mapping[str, Collection[uuid.UUID]],
) -> ReportSection | None:
    rows, total = repo.handles_for_subject(
        session,
        kinds=PARENT_KINDS,
        guardian_id=guardian_id,
        targets=targets,
        limit=MAX_SECTION_ROWS,
    )
    if total == 0:
        return None
    now = repo.now(session)
    values = [
        (
            _LABELS.get(r.kind, humanise(r.kind)),
            fmt_date(r.created_at),
            fmt_date(r.expires_at),
            _status(r, now),
            fmt_count(r.view_count),
            fmt_datetime(r.last_viewed_at) if r.last_viewed_at is not None else EMPTY,
        )
        for r in rows
    ]
    kept, note = cap_rows(values, total)
    return ReportSection(
        class_key=CLASS_KEY, title=title, columns=COLUMNS, rows=tuple(kept), note=note
    )


def _student_sections(session: Session, student_id: uuid.UUID) -> list[ReportSection]:
    # Lazy: certificates.service and fees.service import public.service at module level, so a
    # top-level import here would be a cycle (import-linter still checks the direction).
    from app.certificates import service as certificates  # noqa: PLC0415
    from app.fees import service as fees  # noqa: PLC0415

    account_ids, receipt_ids = fees.fee_targets_of_student(session, student_id)
    out: list[ReportSection] = []
    links = _links_section(
        session,
        title="Parent links about the student's fees",
        guardian_id=None,
        targets={
            PARENT_TARGET_TYPES["parent_dues"]: account_ids,
            PARENT_TARGET_TYPES["parent_receipt"]: receipt_ids,
        },
    )
    if links is not None:
        out.append(links)
    certificate_ids = certificates.certificate_ids_of_student(session, student_id)
    live, withdrawn = repo.count_by_state(session, CERT_KIND, CERT_TARGET, certificate_ids)
    if live or withdrawn:
        out.append(
            ReportSection(
                class_key=CLASS_KEY,
                title="Certificate check codes",
                columns=("In use", "Withdrawn"),
                rows=((fmt_count(live), fmt_count(withdrawn)),),
                note="The codes themselves are printed on the certificates and not repeated here.",
            )
        )
    return out


def subject_report(session: Session, subject: Subject) -> list[ReportSection]:
    """Parent links of a guardian, or the links and check codes about a student; [] for a
    member or when there are none."""
    if subject.kind == "guardian":
        section = _links_section(
            session,
            title="Parent links sent to the guardian",
            guardian_id=subject.id,
            targets={},
        )
        return [section] if section is not None else []
    if subject.kind == "student":
        return _student_sections(session, subject.id)
    return []


__all__ = ["subject_report"]
