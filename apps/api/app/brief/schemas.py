"""Pydantic IO for the weekly management brief (FA-05; FR-BRF-001..FR-BRF-015).

Counts only: no names, no student IDs, no child-level values, no early-warning data. A student
count shown as ``null`` is a small cell ("fewer than ``small_cell_min``"; FR-BRF-009).
"""

from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ReadinessLineOut(BaseModel):
    """One readiness profile ("142 of 160 ready, up 18"). Student counts; ``null`` = fewer than
    ``small_cell_min``."""

    profile_key: str
    label: str
    students: int | None
    ready: int | None
    needs_parent: int | None
    needs_school: int | None
    blocked: int | None
    ready_change: int | None = Field(
        description="Ready students compared with the previous stored week; null when there is "
        "no previous week for this profile or either count is hidden."
    )


class FindingsOut(BaseModel):
    """Open data-check findings of the whole school (work counts, not hidden)."""

    blockers: int
    warnings: int
    students_with_blockers: int | None


class DeadlineOut(BaseModel):
    """A portal window of the school's calendar closing soon."""

    event_key: str
    portal: str
    label: str
    closes_on: dt.date
    days_left: int
    confirmed: bool


class FeesOut(BaseModel):
    """Fee totals (only when fees are on and the reader holds ``finance.read``)."""

    receipts: int
    collected: Decimal = Field(description="Net collected over the week (rupees).")
    balance: Decimal = Field(description="Balance due on the current year's accounts.")
    overdue: Decimal
    students_owing: int | None


class AskOut(BaseModel):
    """AI answers counted against the bundle this month (only for ``tenant.billing.read``)."""

    answers: int
    included: int | None


class WeeklyBriefOut(BaseModel):
    """The weekly management brief. Each section is null when the reader lacks the permission
    of the screen behind it (readiness and deadlines ``dq.readiness.read``, findings
    ``dq.findings.read``, corrections a change-request permission, certificates
    ``certificate.read``, fees ``finance.read``, Ask ``tenant.billing.read``)."""

    id: uuid.UUID | None = Field(description="Null for a live preview (no week stored yet).")
    stored: bool
    week_start: dt.date
    week_end: dt.date
    as_of: dt.date = Field(description="The day the counts were taken (IST).")
    built_at: dt.datetime | None
    previous_week_start: dt.date | None
    small_cell_min: int
    readiness: list[ReadinessLineOut] | None
    findings: FindingsOut | None
    changes_waiting: int | None
    certificates_issued: int | None
    deadlines: list[DeadlineOut] | None
    deadline_days: int
    fees: FeesOut | None
    ask: AskOut | None


class BriefHistoryItemOut(BaseModel):
    id: uuid.UUID
    week_start: dt.date
    week_end: dt.date
    built_at: dt.datetime


class BriefHistoryOut(BaseModel):
    items: list[BriefHistoryItemOut]


class BriefPreferencesOut(BaseModel):
    """How the reader receives the weekly brief (their own choice only)."""

    in_app: bool
    email: bool
    email_available: bool = Field(description="E-mail is set up for this installation.")
    version: int = Field(description="0 until the member first saves a choice.")


class BriefPreferencesIn(_In):
    in_app: bool
    email: bool


__all__ = [
    "AskOut",
    "BriefHistoryItemOut",
    "BriefHistoryOut",
    "BriefPreferencesIn",
    "BriefPreferencesOut",
    "DeadlineOut",
    "FeesOut",
    "FindingsOut",
    "ReadinessLineOut",
    "WeeklyBriefOut",
]
