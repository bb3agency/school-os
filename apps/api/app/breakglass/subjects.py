"""What support-access records hold about one staff member (R3-1A; FR-DPDP-009, PRV-031).

The support access grants the member approved, denied or revoked, or held through a support
membership: when it was asked for and decided, the reason category, whether it was an
emergency, the status, the length and the window. The operator's free-text reason and name
are written by or about someone else and are not repeated.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import RowMapping
from sqlalchemy.orm import Session

from app.breakglass import repository as repo
from app.core.report_format import (
    EMPTY,
    cap_rows,
    fmt_bool,
    fmt_count,
    fmt_date,
    fmt_datetime,
    humanise,
)
from app.core.subjects import MAX_SECTION_ROWS, ReportSection, Subject

CLASS_KEY = "support_access"


def _part(row: RowMapping, membership_id: uuid.UUID) -> str:
    parts = [
        label
        for column, label in (
            ("approved_by_membership", "Approved"),
            ("denied_by_membership", "Denied"),
            ("revoked_by_membership", "Revoked"),
            ("membership_id", "Used this access"),
        )
        if row[column] == membership_id
    ]
    text = ", ".join(parts).lower()
    return text[:1].upper() + text[1:] if text else EMPTY


def _minutes(value: Any) -> str:
    return EMPTY if value is None else fmt_count(int(value))


def subject_report(session: Session, subject: Subject) -> list[ReportSection]:
    """Support access grants this staff member took part in (newest first); [] for other
    subject kinds or when there are none."""
    if subject.kind != "member":
        return []
    rows, total = repo.list_for_member(session, subject.id, limit=MAX_SECTION_ROWS)
    if total == 0:
        return []
    kept, note = cap_rows(
        [
            (
                fmt_date(r["requested_at"] or r["created_at"]),
                humanise(r["reason_code"]),
                fmt_bool(r["emergency"]),
                _part(r, subject.id),
                fmt_date(r["decided_at"]),
                humanise(r["status"]),
                _minutes(r["duration_minutes"]),
                fmt_datetime(r["starts_at"]),
                fmt_datetime(r["expires_at"]),
                fmt_datetime(r["revoked_at"]),
            )
            for r in rows
        ],
        total,
    )
    return [
        ReportSection(
            class_key=CLASS_KEY,
            title="Support access this staff member decided or used",
            columns=(
                "Asked for on",
                "Reason",
                "Emergency",
                "This staff member's part",
                "Decided on",
                "Status",
                "Length (minutes)",
                "Starts",
                "Ends",
                "Revoked at",
            ),
            rows=tuple(kept),
            note=note,
        )
    ]


__all__ = ["subject_report"]
