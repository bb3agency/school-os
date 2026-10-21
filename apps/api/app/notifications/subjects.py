"""In-app notifications in a data principal's access report and erasure (R3-1A; FR-DPDP-009,
FR-DPDP-013, PRV-031, PRV-032; ADR-0048 decisions 6 and 8).

A staff member (subject kind ``member``) sees how many notifications SchoolOS showed them, per
kind, with how many are unread and the latest one's date. The texts are not repeated (they are
rendered from ids and codes about other records). Erasure deletes the member's notifications in
the caller's transaction and records ``notification.subject_erased`` with the count only.
"""

from __future__ import annotations

from collections.abc import Collection

from sqlalchemy.orm import Session

from app.audit import service as audit
from app.core.logging import get_context
from app.core.report_format import cap_rows, fmt_count, fmt_date, humanise
from app.core.subjects import ErasureOutcome, ReportSection, Subject
from app.notifications import repository as repo

CLASS_KEY = "notifications"
ERASED_EVENT = "notification.subject_erased"


def subject_report(session: Session, subject: Subject) -> list[ReportSection]:
    """Counts of the member's notifications per kind ([] for other kinds or none)."""
    if subject.kind != "member":
        return []
    groups = repo.counts_by_template(session, subject.id)
    if not groups:
        return []
    rows = [
        (
            humanise(g["template_key"]),
            fmt_count(int(g["total"])),
            fmt_count(int(g["unread"])),
            fmt_date(g["latest"]),
        )
        for g in groups
    ]
    kept, note = cap_rows(rows)
    return [
        ReportSection(
            class_key=CLASS_KEY,
            title="In-app notifications",
            columns=("Kind", "Notifications", "Unread", "Latest on"),
            rows=tuple(kept),
            note=note,
        )
    ]


def _request_id() -> str | None:
    value = get_context().get("request_id")
    return value if isinstance(value, str) else None


def erase_for_subject(
    session: Session, subject: Subject, class_keys: Collection[str], *, dry_run: bool
) -> list[ErasureOutcome]:
    """Delete the member's notifications (a dry run only counts them)."""
    if subject.kind != "member" or CLASS_KEY not in class_keys:
        return []
    if dry_run:
        return [ErasureOutcome(CLASS_KEY, repo.count_for_recipient(session, subject.id))]
    count = repo.delete_for_recipient(session, subject.id)
    audit.record(
        session,
        action=ERASED_EVENT,
        resource_type="membership",
        resource_id=subject.id,
        summary={"class": CLASS_KEY, "count": count},
        request_id=_request_id(),
    )
    return [ErasureOutcome(CLASS_KEY, count)]


__all__ = ["ERASED_EVENT", "erase_for_subject", "subject_report"]
