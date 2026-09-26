"""Public API of the control plane for OTHER modules (CLAUDE.md §4: modules meet at service.py).

School-side (tenant API) routes that the authz owner wires with ``require(...)``
(docs/16 §8.3):

- GET  /api/v1/tenant/billing (tenant.billing.read): ``current_subscription(tenant_session)``
- GET  /api/v1/tenant/billing/invoices (tenant.billing.read): its ``["invoices"]``
- GET  /api/v1/announcements (any authenticated member): ``active_announcements(tenant, tier)``
- POST /api/v1/support/tickets (support.ticket.create): ``open_ticket_from_tenant(...)``
- GET  /api/v1/support/tickets (support.ticket.create): ``list_tenant_tickets(tenant_id)``
- GET  /api/v1/support/tickets/{id} (support.ticket.create): ``get_tenant_ticket(...)``
- POST /api/v1/support/tickets/{id}/messages (support.ticket.create): ``reply_from_tenant``

Feature flags for tenant code: ``is_flag_enabled(key, tenant_id, session=tenant_session)``.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.platform import announcements, flags, support
from app.platform.schemas import (
    AnnouncementBrief,
    SchoolTicketMessageIn,
    TicketCreateSchool,
    TicketOut,
)

__all__ = [
    "AnnouncementBrief",
    "SchoolTicketMessageIn",
    "TicketCreateSchool",
    "TicketOut",
    "active_announcements",
    "current_subscription",
    "get_tenant_ticket",
    "is_flag_enabled",
    "list_tenant_tickets",
    "open_ticket_from_tenant",
    "reply_from_tenant",
]


def current_subscription(session: Session) -> dict[str, Any] | None:
    """The calling school's plan, status, period, limits, latest usage and invoices.

    ``session`` must be the request's ``tenant_session`` (sos_app); the allowlisted definer
    ``core.current_subscription()`` only ever returns ``core.current_tenant()``'s own records.
    """
    value: dict[str, Any] | None = session.execute(
        text("SELECT core.current_subscription()")
    ).scalar_one()
    return value


def active_announcements(tenant_id: uuid.UUID, tier: str) -> list[AnnouncementBrief]:
    return announcements.active_announcements(tenant_id, tier)


def is_flag_enabled(key: str, tenant_id: uuid.UUID, *, session: Session | None = None) -> bool:
    return flags.is_enabled(key, tenant_id, session=session)


def open_ticket_from_tenant(
    tenant_id: uuid.UUID, user_id: uuid.UUID, data: TicketCreateSchool
) -> TicketOut:
    return support.open_ticket_from_tenant(tenant_id, user_id, data)


def list_tenant_tickets(
    tenant_id: uuid.UUID, *, limit: int = 50, cursor: str | None = None
) -> tuple[list[TicketOut], str | None]:
    return support.list_tenant_tickets(tenant_id, limit=limit, cursor=cursor)


def get_tenant_ticket(tenant_id: uuid.UUID, ticket_id: uuid.UUID) -> TicketOut:
    return support.get_tenant_ticket(tenant_id, ticket_id)


def reply_from_tenant(
    tenant_id: uuid.UUID, user_id: uuid.UUID, ticket_id: uuid.UUID, data: SchoolTicketMessageIn
) -> TicketOut:
    return support.reply_from_tenant(tenant_id, user_id, ticket_id, data.body)
