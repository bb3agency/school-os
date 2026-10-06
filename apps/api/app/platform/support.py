"""Support tickets (FR-PLT-027; docs/16 §5.14, §15).

No student data: every subject and message is passed through ``core.redaction.redact`` (Aadhaar,
phone and email masking) before storage; operators can flag tickets that still contain personal
data. Tickets are deleted one year after closing. Audit summaries carry IDs and statuses only,
never ticket text.

School-side functions (``open_ticket_from_tenant`` etc.) are called by the tenant API through
``app.platform.service``; they write through ``platform_session`` and always filter by the
caller's tenant (another school's ticket is ``NotFound``).
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from sqlalchemy import delete, func, select

from app.core.db import platform_session
from app.core.errors import Conflict, NotFound, PreconditionFailed, ValidationFailed
from app.core.ids import new_id
from app.core.logging import get_logger
from app.core.redaction import redact
from app.platform import models as m
from app.platform import repository as repo
from app.platform.common import (
    SYSTEM,
    Actor,
    audit_platform,
    clamp_limit,
    db_errors,
    now,
    parse_cursor,
    support_cfg,
    today_ist,
)
from app.platform.schemas import (
    TicketCreateOperator,
    TicketCreateSchool,
    TicketMessageOut,
    TicketOut,
    TicketPatch,
)

log = get_logger(__name__)

_TRANSITIONS: dict[str, set[str]] = {
    "open": {"in_progress", "waiting_on_school", "resolved", "closed"},
    "in_progress": {"waiting_on_school", "resolved", "closed"},
    "waiting_on_school": {"in_progress", "resolved", "closed"},
    "resolved": {"in_progress", "closed"},
    "closed": set(),
}


def _clean(text: str) -> str:
    return redact(text)


def _sla(priority: str, at: dt.datetime) -> tuple[dt.datetime, dt.datetime]:
    hours = support_cfg()["sla_hours"][priority]
    return (
        at + dt.timedelta(hours=int(hours["first_response"])),
        at + dt.timedelta(hours=int(hours["resolution"])),
    )


def _out(s: Any, row: Any, *, include_internal: bool, with_messages: bool = True) -> TicketOut:
    messages: list[TicketMessageOut] = []
    if with_messages:
        stmt = select(m.support_messages).where(m.support_messages.c.ticket_id == row["id"])
        if not include_internal:
            stmt = stmt.where(m.support_messages.c.internal_note.is_(False))
        messages = [
            TicketMessageOut.model_validate(dict(r))
            for r in s.execute(stmt.order_by(m.support_messages.c.created_at)).mappings()
        ]
    return TicketOut.model_validate(
        {**dict(row), "number": f"T-{row['ticket_no']}", "messages": messages}
    )


def _insert_ticket(
    s: Any,
    *,
    tenant_id: uuid.UUID,
    channel: str,
    category: str,
    priority: str,
    subject: str,
    body: str,
    user_id: uuid.UUID | None,
    operator_id: uuid.UUID | None,
) -> Any:
    created = now()
    first_due, resolution_due = _sla(priority, created)
    ticket = repo.insert_row(
        s,
        m.support_tickets,
        {
            "id": new_id(),
            "tenant_id": tenant_id,
            "opened_by_user_id": user_id,
            "opened_by_operator_id": operator_id,
            "channel": channel,
            "category": category,
            "priority": priority,
            "subject": _clean(subject),
            "status": "open",
            "first_response_due_at": first_due,
            "resolution_due_at": resolution_due,
        },
    )
    repo.insert_row(
        s,
        m.support_messages,
        {
            "id": new_id(),
            "ticket_id": ticket["id"],
            "author_type": "school_user" if user_id else "operator",
            "author_id": user_id or operator_id,
            "body": _clean(body),
            "internal_note": False,
        },
    )
    return ticket


# --- operator side ----------------------------------------------------------------------------


def list_tickets(
    *,
    status: str | None = None,
    priority: str | None = None,
    tenant_id: uuid.UUID | None = None,
    assigned_to: uuid.UUID | None = None,
    limit: int = 50,
    cursor: str | None = None,
) -> tuple[list[TicketOut], str | None]:
    limit = clamp_limit(limit)
    conds = []
    if status:
        conds.append(m.support_tickets.c.status == status)
    if priority:
        conds.append(m.support_tickets.c.priority == priority)
    if tenant_id:
        conds.append(m.support_tickets.c.tenant_id == tenant_id)
    if assigned_to:
        conds.append(m.support_tickets.c.assigned_to == assigned_to)
    with platform_session() as s:
        rows = repo.list_rows(
            s, m.support_tickets, *conds, limit=limit, cursor=parse_cursor(cursor)
        )
        items = [_out(s, r, include_internal=True, with_messages=False) for r in rows[:limit]]
    return items, (str(rows[limit - 1]["id"]) if len(rows) > limit else None)


def get_ticket(ticket_id: uuid.UUID) -> TicketOut:
    with platform_session() as s:
        row = repo.get(s, m.support_tickets, ticket_id)
        if row is None:
            raise NotFound("Ticket not found")
        return _out(s, row, include_internal=True)


def open_ticket_by_operator(actor: Actor, data: TicketCreateOperator) -> TicketOut:
    with platform_session() as s, db_errors():
        if repo.get_by(s, m.deployments, m.deployments.c.tenant_id == data.tenant_id) is None:
            raise NotFound("School not found")
        row = _insert_ticket(
            s,
            tenant_id=data.tenant_id,
            channel=data.channel,
            category=data.category,
            priority=data.priority,
            subject=data.subject,
            body=data.body,
            user_id=None,
            operator_id=actor.operator_id,
        )
        audit_platform(
            s,
            actor,
            "support.ticket_opened",
            "support_ticket",
            row["id"],
            {"channel": data.channel, "priority": data.priority},
            tenant_id=data.tenant_id,
        )
        return _out(s, row, include_internal=True)


def add_operator_message(
    actor: Actor, ticket_id: uuid.UUID, body: str, *, internal_note: bool
) -> TicketOut:
    with platform_session() as s, db_errors():
        row = repo.get(s, m.support_tickets, ticket_id, for_update=True)
        if row is None:
            raise NotFound("Ticket not found")
        if row["status"] == "closed":
            raise Conflict("The ticket is closed.", code="ticket_closed")
        repo.insert_row(
            s,
            m.support_messages,
            {
                "id": new_id(),
                "ticket_id": ticket_id,
                "author_type": "operator",
                "author_id": actor.operator_id,
                "body": _clean(body),
                "internal_note": internal_note,
            },
        )
        values: dict[str, Any] = {}
        if not internal_note and row["first_responded_at"] is None:
            values["first_responded_at"] = now()
        if not internal_note and row["status"] == "open":
            values["status"] = "in_progress"
        row = repo.update_row(s, m.support_tickets, ticket_id, values)
        audit_platform(
            s,
            actor,
            "support.ticket_updated",
            "support_ticket",
            ticket_id,
            {"message": "internal_note" if internal_note else "reply"},
            tenant_id=row["tenant_id"],
        )
        return _out(s, row, include_internal=True)


def update_ticket(
    actor: Actor, ticket_id: uuid.UUID, data: TicketPatch, *, expected_version: int | None
) -> TicketOut:
    changes = data.model_dump(exclude_unset=True)
    with platform_session() as s, db_errors():
        row = repo.get(s, m.support_tickets, ticket_id, for_update=True)
        if row is None:
            raise NotFound("Ticket not found")
        if expected_version is not None and row["version"] != expected_version:
            raise PreconditionFailed()
        values: dict[str, Any] = {}
        status = changes.get("status")
        if status is not None and status != row["status"]:
            if status not in _TRANSITIONS[row["status"]]:
                raise Conflict(
                    f"A {row['status']} ticket cannot become {status}.", code="invalid_state"
                )
            values["status"] = status
            if status == "resolved":
                values["resolved_at"] = now()
            if status == "closed":
                closed = now()
                values["closed_at"] = closed
                values["purge_after"] = today_ist(closed) + dt.timedelta(
                    days=int(support_cfg()["retention_days_after_close"])
                )
        if "priority" in changes and changes["priority"] is not None:
            values["priority"] = changes["priority"]
        if "assigned_to" in changes:
            assignee = changes["assigned_to"]
            if assignee is not None:
                op = repo.get(s, m.operators, assignee)
                if op is None or op["status"] != "active":
                    raise ValidationFailed(
                        [
                            {
                                "field": "assigned_to",
                                "code": "not_active",
                                "message_key": "errors.operator",
                            }
                        ]
                    )
            values["assigned_to"] = assignee
        if changes.get("personal_data_flagged") is not None:
            values["personal_data_flagged"] = changes["personal_data_flagged"]
        row = repo.update_row(s, m.support_tickets, ticket_id, values)
        audit_platform(
            s,
            actor,
            "support.ticket_updated",
            "support_ticket",
            ticket_id,
            {"fields": sorted(values), "status": row["status"]},
            tenant_id=row["tenant_id"],
        )
        if values.get("personal_data_flagged"):
            audit_platform(
                s,
                actor,
                "support.personal_data_flagged",
                "support_ticket",
                ticket_id,
                {},
                tenant_id=row["tenant_id"],
            )
        return _out(s, row, include_internal=True)


def purge_closed(*, today: dt.date | None = None) -> int:
    """Daily: delete tickets (and messages, by cascade) past ``purge_after``."""
    today = today or today_ist()
    with platform_session() as s:
        result = s.execute(
            delete(m.support_tickets)
            .where(m.support_tickets.c.purge_after < today)
            .returning(m.support_tickets.c.id)
        )
        count = len(result.all())
        if count:
            audit_platform(
                s, SYSTEM, "support.tickets_purged", "support_ticket", None, {"count": count}
            )
    return count


# --- school side (called by tenant routes via app.platform.service) ---------------------------


def open_ticket_from_tenant(
    tenant_id: uuid.UUID, user_id: uuid.UUID, data: TicketCreateSchool
) -> TicketOut:
    with platform_session() as s, db_errors():
        row = _insert_ticket(
            s,
            tenant_id=tenant_id,
            channel="app",
            category=data.category,
            priority=data.priority,
            subject=data.subject,
            body=data.body,
            user_id=user_id,
            operator_id=None,
        )
        audit_platform(
            s,
            SYSTEM,
            "support.ticket_opened",
            "support_ticket",
            row["id"],
            {"channel": "app", "priority": data.priority, "user_id": str(user_id)},
            tenant_id=tenant_id,
        )
        return _out(s, row, include_internal=False)


def _visible_to(row: Any, tenant_id: uuid.UUID, viewer: uuid.UUID | None) -> bool:
    """A school ticket is visible to its school only, and there to the member who opened it,
    or to every member when ``viewer`` is ``None`` (``support.manage``; audit 2026-10-06 R-17)."""
    if row["tenant_id"] != tenant_id:
        return False
    return viewer is None or row["opened_by_user_id"] == viewer


def list_tenant_tickets(
    tenant_id: uuid.UUID,
    *,
    viewer: uuid.UUID | None,
    limit: int = 50,
    cursor: str | None = None,
) -> tuple[list[TicketOut], str | None]:
    """The school's tickets, newest first: all of them for ``viewer=None`` (``support.manage``),
    otherwise only the ones ``viewer`` opened (R-17)."""
    limit = clamp_limit(limit)
    conds = [m.support_tickets.c.tenant_id == tenant_id]
    if viewer is not None:
        conds.append(m.support_tickets.c.opened_by_user_id == viewer)
    with platform_session() as s:
        rows = repo.list_rows(
            s,
            m.support_tickets,
            *conds,
            limit=limit,
            cursor=parse_cursor(cursor),
        )
        items = [_out(s, r, include_internal=False, with_messages=False) for r in rows[:limit]]
    return items, (str(rows[limit - 1]["id"]) if len(rows) > limit else None)


def get_tenant_ticket(
    tenant_id: uuid.UUID, ticket_id: uuid.UUID, *, viewer: uuid.UUID | None
) -> TicketOut:
    """One ticket; 404 for another school's and, without ``support.manage``, for another
    member's (R-17)."""
    with platform_session() as s:
        row = repo.get(s, m.support_tickets, ticket_id)
        if row is None or not _visible_to(row, tenant_id, viewer):
            raise NotFound("Ticket not found")
        return _out(s, row, include_internal=False)


def reply_from_tenant(
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    ticket_id: uuid.UUID,
    body: str,
    *,
    manager: bool,
) -> TicketOut:
    """Reply as ``user_id``: on their own ticket, or on any ticket of the school when
    ``manager`` (``support.manage``); 404 otherwise (R-17)."""
    with platform_session() as s, db_errors():
        row = repo.get(s, m.support_tickets, ticket_id, for_update=True)
        if row is None or not _visible_to(row, tenant_id, None if manager else user_id):
            raise NotFound("Ticket not found")
        if row["status"] == "closed":
            raise Conflict("The ticket is closed.", code="ticket_closed")
        repo.insert_row(
            s,
            m.support_messages,
            {
                "id": new_id(),
                "ticket_id": ticket_id,
                "author_type": "school_user",
                "author_id": user_id,
                "body": _clean(body),
                "internal_note": False,
            },
        )
        values = (
            {"status": "in_progress"} if row["status"] in ("waiting_on_school", "resolved") else {}
        )
        row = repo.update_row(s, m.support_tickets, ticket_id, values)
        audit_platform(
            s,
            SYSTEM,
            "support.ticket_updated",
            "support_ticket",
            ticket_id,
            {"message": "school_reply", "user_id": str(user_id)},
            tenant_id=tenant_id,
        )
        return _out(s, row, include_internal=False)


def open_ticket_counts() -> dict[str, int]:
    with platform_session() as s:
        rows = s.execute(
            select(m.support_tickets.c.priority, func.count())
            .where(m.support_tickets.c.status.not_in(("resolved", "closed")))
            .group_by(m.support_tickets.c.priority)
        ).all()
    return {p: int(n) for p, n in rows}
