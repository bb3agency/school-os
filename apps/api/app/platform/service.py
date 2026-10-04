"""Public API of the control plane for OTHER modules (CLAUDE.md §4: modules meet at service.py).

School-side (tenant API) routes that the authz owner wires with ``require(...)``
(docs/16 §8.3):

- GET  /api/v1/tenant/billing (tenant.billing.read): ``current_subscription(tenant_session)``
  and, for that subscription, ``school_ai_bundle(tenant_id, subscription_id)``
- GET  /api/v1/tenant/billing/invoices (tenant.billing.read): its ``["invoices"]``
- GET  /api/v1/announcements (any authenticated member): ``active_announcements(tenant, tier)``
- POST /api/v1/support/tickets (support.ticket.create): ``open_ticket_from_tenant(...)``
- GET  /api/v1/support/tickets (support.ticket.create): ``list_tenant_tickets(tenant_id)``
- GET  /api/v1/support/tickets/{id} (support.ticket.create): ``get_tenant_ticket(...)``
- POST /api/v1/support/tickets/{id}/messages (support.ticket.create): ``reply_from_tenant``

Break-glass (school side, ``app.breakglass``; US-103, FR-OPS-004):
``breakglass_requests_for_school``,
``breakglass_request_for_school`` and ``record_breakglass_outcome`` (see ``platform.breakglass``).

Feature flags for tenant code: ``is_flag_enabled(key, tenant_id, session=tenant_session)``.

Provisioning helpers for dev tooling: ``invite_school_owner(platform_session, ...)``.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.platform import announcements, billing, breakglass, flags, repository, support
from app.platform.breakglass import SchoolBreakGlassRequest
from app.platform.schemas import (
    AnnouncementBrief,
    SchoolAiBundle,
    SchoolTicketMessageIn,
    TicketCreateSchool,
    TicketOut,
)

__all__ = [
    "AnnouncementBrief",
    "SchoolAiBundle",
    "SchoolBreakGlassRequest",
    "SchoolTicketMessageIn",
    "TicketCreateSchool",
    "TicketOut",
    "active_announcements",
    "breakglass_request_for_school",
    "breakglass_requests_for_school",
    "current_subscription",
    "get_tenant_ticket",
    "invite_school_owner",
    "is_flag_enabled",
    "list_tenant_tickets",
    "open_ticket_from_tenant",
    "record_breakglass_outcome",
    "record_breakglass_session_started",
    "reply_from_tenant",
    "school_ai_bundle",
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


def school_ai_bundle(tenant_id: uuid.UUID, subscription_id: uuid.UUID) -> SchoolAiBundle | None:
    """The calling school's AI answer bundle and this month's answer count (platform data only;
    ``None`` without a bundle). ``subscription_id`` comes from ``current_subscription``."""
    return billing.school_ai_bundle(tenant_id, subscription_id)


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


def invite_school_owner(
    platform_db: Session,
    *,
    tenant_id: uuid.UUID,
    subject: str,
    display_name: str,
    email: str | None,
    language: str,
) -> tuple[uuid.UUID, uuid.UUID, bool]:
    """Create a provisioning school's first (invited) owner via ``core.create_owner_invite``.

    Returns (user_id, membership_id, owner_role_assigned). Allowed only while the school is
    ``provisioning`` and has no members; the invitee activates it on first sign-in (ADR-0019).
    Permission assumed: ``platform.tenants.provision``. Audit is the caller's responsibility.
    """
    row = repository.call_create_owner_invite(
        platform_db,
        tenant_id=tenant_id,
        subject=subject,
        display_name=display_name,
        email=email,
        language=language,
    )
    return (
        uuid.UUID(str(row["user_id"])),
        uuid.UUID(str(row["membership_id"])),
        bool(row["owner_role_assigned"]),
    )


def breakglass_requests_for_school(tenant_id: uuid.UUID) -> list[SchoolBreakGlassRequest]:
    """Open support-access requests for this school (pulled by the school side)."""
    return breakglass.requests_for_school(tenant_id)


def breakglass_request_for_school(
    tenant_id: uuid.UUID, request_id: uuid.UUID
) -> SchoolBreakGlassRequest | None:
    return breakglass.request_for_school(tenant_id, request_id)


def record_breakglass_outcome(
    tenant_id: uuid.UUID, request_id: uuid.UUID, status: str, *, grant_id: uuid.UUID
) -> bool:
    """Report the school's decision / the grant's end to the control plane (idempotent)."""
    return breakglass.record_school_outcome(tenant_id, request_id, status, grant_id=grant_id)


def record_breakglass_session_started(
    tenant_id: uuid.UUID,
    request_id: uuid.UUID,
    *,
    grant_id: uuid.UUID,
    session_ref: str | None,
) -> bool:
    """Copy a support session start into the control-plane chain (ADR-0023 §4; IDs only)."""
    return breakglass.record_session_started(
        tenant_id, request_id, grant_id=grant_id, session_ref=session_ref
    )
