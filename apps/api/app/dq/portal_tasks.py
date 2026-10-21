"""Portal calendar and M4 tasks: the daily reminders and linking a circular's task to an event
(FR-RDY-006..008, FR-RDY-010, US-3103, US-3104, ADR-0053).

Kept apart from :mod:`app.dq.calendar_service` because it works through ``circulars.service``
(tasks) and ``notifications.service``; the readiness screens only read the calendar, so the
knowledge tools that read findings never depend on circulars (import-linter
``knowledge-layers``).

- **Reminders (staff only; D45).** Once a day per active school (a suspended school gets none,
  D31): for each applicable event whose window closes within a lead day (30, 14, 7, 1 by
  default) one step is sent: the owner's M4 task is created or moved (through
  ``circulars.service``), and one in-app notification goes to the owner and to the holders of
  ``dq.readiness.manage`` (without an owner, to the holders of ``readiness.calendar.manage``)
  with counts only. ``ops.portal_reminders`` makes it idempotent per (school, year, event, lead
  day). Nothing is ever sent to parents.
- **Linking a task (FR-RDY-010).** A person links a task confirmed from a circular to an event;
  the due date becomes the confirmed closing date, from that circular.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Collection
from typing import Final

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.authz.context import Scopes, UserContext
from app.circulars import service as circulars
from app.core.errors import ValidationFailed
from app.core.ids import new_id
from app.core.logging import get_logger
from app.dq import calendar as cal
from app.dq import repository as repo
from app.dq.calendar_service import (
    MANAGE,
    READ,
    CalendarEvent,
    academic_year,
    active_member_ids,
    audit_calendar,
    confirmation_values,
    event_detail,
    events_in_force,
    find_event,
    readiness_counts,
    readiness_keys,
    today_ist,
)
from app.dq.schemas import CalendarEventOut, PortalTaskLinkIn
from app.notifications import service as notifications
from app.tenancy import service as tenancy

log = get_logger(__name__)

READINESS_MANAGE: Final = "dq.readiness.manage"
STUDENT_READ: Final = "student.read_basic"
TEMPLATE: Final = "portal_calendar.reminder"
SYSTEM_ID: Final = uuid.UUID(int=0)


def _error(field: str, code: str) -> dict[str, str]:
    return {"field": field, "code": code, "message_key": f"errors.{code}"}


def link_task(
    session: Session, ctx: UserContext, task_id: uuid.UUID, data: PortalTaskLinkIn
) -> CalendarEventOut:
    """A person links a task confirmed from a circular to a calendar event (FR-RDY-010): the
    task's due date becomes the event's confirmed closing date, from that circular (``source_kind
    = circular``, the circular as the document), and the task's owner owns the work unless the
    event already has one. Needs ``readiness.calendar.manage`` and a school-wide
    ``task.manage``; a task not from a circular: 422 ``not_circular_task``. Audited
    ``readiness.calendar.confirmed`` with the task id."""
    ref = circulars.task_for_portal_link(session, ctx, task_id)
    if ref.source != "circular":
        raise ValidationFailed([_error("task_id", "not_circular_task")])
    year = academic_year(session, data.academic_year_id)
    event = find_event(session, year, data.event_key)
    row = repo.calendar_row(session, year.id, data.event_key, lock=True)
    opens_on = min(event.window.opens_on, ref.due_on)
    owner = row["owner_membership_id"] if row is not None else None
    active = active_member_ids(session)
    if owner not in active:
        owner = ref.owner_membership_id if ref.owner_membership_id in active else None
    values = confirmation_values(
        ctx,
        opens_on=opens_on,
        closes_on=ref.due_on,
        source_kind="circular",
        document_id=ref.document_id,
        owner_membership_id=owner,
    )
    if row is None:
        row = repo.insert_calendar_row(
            session,
            {"id": new_id(), "academic_year_id": year.id, "event_key": data.event_key, **values},
        )
        created = True
    else:
        row = repo.update_calendar_row(session, row["id"], values)
        created = False
    audit_calendar(
        session,
        "readiness.calendar.confirmed",
        row["id"],
        {
            "event_key": data.event_key,
            "academic_year_id": year.id,
            "source_kind": "circular",
            "document_id": ref.document_id,
            "owner_membership_id": owner,
            "task_id": task_id,
            "created": created,
        },
    )
    return event_detail(session, ctx, year, data.event_key)


# --- daily reminders (FR-RDY-006..008) --------------------------------------------------------


def system_context(tenant_id: uuid.UUID) -> UserContext:
    """Whole-school read context for the readiness counts of a reminder (never used to write)."""
    return UserContext(
        user_id=SYSTEM_ID,
        tenant_id=tenant_id,
        membership_id=SYSTEM_ID,
        roles=frozenset({"system"}),
        permissions=frozenset({READ, STUDENT_READ}),
        scopes=Scopes(school=True),
        mfa=False,
        auth_time=None,
    )


def _remind(
    session: Session,
    tenant_id: uuid.UUID,
    event: CalendarEvent,
    *,
    lead: int,
    left: int,
    counts: tuple[int, int],
    active: Collection[uuid.UUID],
) -> int:
    row = event.row
    owner = row["owner_membership_id"] if row is not None else None
    if owner not in active:
        owner = None  # the owner left: the calendar managers are told instead
    task_id = None
    if owner is not None and row is not None:
        task_id = circulars.upsert_portal_task(
            session,
            event_key=event.key,
            title=event.label_en,
            due_on=event.window.closes_on,
            owner_membership_id=owner,
            created_by=row["confirmed_by"],
            created_by_membership=row["confirmed_by_membership"],
        )
    ready, students = counts
    params = {"portal": event.portal, "days": max(left, 0), "ready": ready, "students": students}
    dedupe = f"portal_calendar:{event.year.id}:{event.key}:{lead}"
    recipients: list[notifications.Recipients] = [
        notifications.PermissionSelector(READINESS_MANAGE)
    ]
    if owner is not None:
        recipients.insert(0, [owner])
    else:
        recipients.append(notifications.PermissionSelector(MANAGE))
    notified = sum(
        notifications.notify(
            session,
            tenant_id=tenant_id,
            recipients=who,
            template_key=TEMPLATE,
            params=params,
            resource_id=event.year.id,
            dedupe_key=dedupe,
        )
        for who in recipients
    )
    repo.insert_reminder(
        session,
        {
            "id": new_id(),
            "tenant_id": tenant_id,
            "academic_year_id": event.year.id,
            "event_key": event.key,
            "lead_day": lead,
            "closes_on": event.window.closes_on,
            "task_id": task_id,
            "notified": notified,
        },
    )
    audit_calendar(
        session,
        "readiness.calendar.reminded",
        None if row is None else row["id"],
        {
            "event_key": event.key,
            "academic_year_id": event.year.id,
            "lead_day": lead,
            "task_id": task_id,
            "notified": notified,
        },
        system=True,
    )
    return notified


def _unique_violation(exc: IntegrityError) -> bool:
    diag = getattr(exc.orig, "diag", None)
    return getattr(diag, "constraint_name", None) == "portal_reminders_once"


def send_reminders(session: Session, *, today: dt.date | None = None) -> int:
    """One school's reminders for today (inside its ``tenant_session``); returns how many
    notifications were created. Each event's step runs in a savepoint: a concurrent run that
    sent the same step first wins and this one leaves no task or notification behind."""
    day = today or today_ist()
    tenant_id = repo.current_tenant(session)
    years = [
        y
        for y in tenancy.list_academic_years(session, include_archived=False)
        if y.ends_on >= day - dt.timedelta(days=cal.MAX_LEAD_DAY)
    ]
    ctx = system_context(tenant_id)
    cache: dict[str, tuple[int, int]] = {}
    active: set[uuid.UUID] | None = None
    sent = 0
    for event in events_in_force(session, years):
        left = cal.days_left(event.window, day)
        lead = cal.due_lead_day(
            event.lead_days, left, repo.sent_lead_days(session, event.year.id, event.key)
        )
        if lead is None:
            continue
        keys = readiness_keys(event)
        counts = readiness_counts(session, ctx, keys[0], cache) if keys else (0, 0)
        if active is None:
            active = active_member_ids(session)
        try:
            with session.begin_nested():
                sent += _remind(
                    session, tenant_id, event, lead=lead, left=left, counts=counts, active=active
                )
        except IntegrityError as exc:
            if not _unique_violation(exc):
                raise
            log.info("dq.calendar.reminder_already_sent", lead_day=lead)
    log.info("dq.calendar.reminders_sent", count=sent)
    return sent


__all__ = [
    "TEMPLATE",
    "link_task",
    "send_reminders",
    "system_context",
]
