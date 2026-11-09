"""The weekly brief in a data principal's access report and erasure (FA-05; FR-BRF-015;
FR-DPDP-009, FR-DPDP-013; ADR-0048 decisions 6 and 8).

A staff member (subject kind ``member``) sees their own delivery choice for the weekly brief
(in-app, e-mail). The stored weekly briefs are school-level counts and hold nothing about one
person. Erasure deletes the member's choice (they fall back to the defaults) and records
``brief.subject_erased`` with the count only.
"""

from __future__ import annotations

from collections.abc import Collection

from sqlalchemy.orm import Session

from app.audit import service as audit
from app.brief import repository as repo
from app.core.logging import get_context
from app.core.report_format import fmt_date
from app.core.subjects import ErasureOutcome, ReportSection, Subject

CLASS_KEY = "brief_preferences"
ERASED_EVENT = "brief.subject_erased"


def _yes(value: object) -> str:
    return "Yes" if value else "No"


def subject_report(session: Session, subject: Subject) -> list[ReportSection]:
    """The member's weekly-brief choice ([] for other kinds or when never saved)."""
    if subject.kind != "member":
        return []
    row = repo.get_preferences(session, subject.id)
    if row is None:
        return []
    return [
        ReportSection(
            class_key=CLASS_KEY,
            title="Weekly brief delivery",
            columns=("In-app notice", "E-mail", "Changed on"),
            rows=((_yes(row["in_app"]), _yes(row["email"]), fmt_date(row["updated_at"])),),
            note=None,
        )
    ]


def _request_id() -> str | None:
    value = get_context().get("request_id")
    return value if isinstance(value, str) else None


def erase_for_subject(
    session: Session, subject: Subject, class_keys: Collection[str], *, dry_run: bool
) -> list[ErasureOutcome]:
    """Delete the member's choice (a dry run only counts it)."""
    if subject.kind != "member" or CLASS_KEY not in class_keys:
        return []
    if dry_run:
        return [ErasureOutcome(CLASS_KEY, repo.count_preferences(session, subject.id))]
    count = repo.delete_preferences(session, subject.id)
    audit.record(
        session,
        action=ERASED_EVENT,
        resource_type="membership",
        resource_id=subject.id,
        summary={"class": CLASS_KEY, "count": count},
        request_id=_request_id(),
    )
    return [ErasureOutcome(CLASS_KEY, count)]


__all__ = ["CLASS_KEY", "ERASED_EVENT", "erase_for_subject", "subject_report"]
