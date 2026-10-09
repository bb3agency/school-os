"""School-side routes backed by the control plane (docs/16 §8.3, §5.18, §14, §15; FR-PLT-026,
FR-PLT-027, FR-PLT-030).

Mounted in BOTH deployment modes (they are tenant routes guarded by ``require``):
- ``GET /api/v1/tenant/billing`` and ``/tenant/billing/invoices`` (``tenant.billing.read``): the
  school's own plan, status, period, usage vs limits and invoices, read through the allowlisted
  definer ``core.current_subscription()`` in the caller's ``tenant_session`` (never another
  school's rows). The AI answer bundle of that subscription, its ex-GST prices and this month's
  answer count are platform rows read for the caller's own tenant and subscription
  (``service.school_ai_bundle``; no tenant table, no definer). On a dedicated host there is no
  local billing data yet (M1: heartbeat).
- ``GET /api/v1/announcements`` (any member): active banners for this school and tier.
- ``POST/GET /api/v1/support/tickets``, ``GET /support/tickets/{id}``,
  ``POST /support/tickets/{id}/messages`` (``support.ticket.create``): the school's own tickets;
  text is redacted before storage; other schools' tickets answer 404. A member reads and
  answers only the tickets they opened; ``support.manage`` holders every ticket of the school
  (others' tickets answer 404 too; audit 2026-10-06 R-17).
"""

from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, ConfigDict

from app.audit import service as audit
from app.authz.catalog import AUTHENTICATED
from app.authz.context import UserContext
from app.authz.dependencies import TenantDB, require
from app.authz.http import Cursor, IdempotencyDep, Limit, Page
from app.core.config import get_settings
from app.platform import service
from app.platform.schemas import (
    AnnouncementBrief,
    SchoolAiBundle,
    SchoolTicketMessageIn,
    TicketCreateSchool,
    TicketOut,
)

router = APIRouter(prefix="/api/v1", tags=["school"])

BillingReader = Annotated[UserContext, Depends(require("tenant.billing.read"))]
Member = Annotated[UserContext, Depends(require(AUTHENTICATED))]
TicketUser = Annotated[UserContext, Depends(require("support.ticket.create"))]
SUPPORT_MANAGE = "support.manage"


def _ticket_viewer(ctx: UserContext) -> uuid.UUID | None:
    """``None`` (every ticket of the school) for ``support.manage`` holders, otherwise the
    caller: a member sees only the tickets they opened (audit 2026-10-06 R-17)."""
    return None if ctx.has(SUPPORT_MANAGE) else ctx.user_id


# plan limit key -> usage field in core.current_subscription()["usage"]
_USAGE_FOR_LIMIT = {
    "students": "students_active",
    "staff_users": "staff_users",
    "documents": "documents",
    "storage_gb": "storage_bytes",
    "ai_tokens_month": "ai_tokens",
    "ai_budget_inr": "ai_cost_inr",
}


class UsageAgainstLimit(BaseModel):
    model_config = ConfigDict(frozen=True)
    metric: str
    used: Decimal
    limit: Decimal | None
    percent: int | None


class TenantInvoice(BaseModel):
    model_config = ConfigDict(frozen=True)
    invoice_id: uuid.UUID
    invoice_number: str | None
    period_start: dt.date
    period_end: dt.date
    issue_date: dt.date | None
    due_date: dt.date | None
    total_inr: Decimal
    amount_due_inr: Decimal
    status: str


class TenantBillingOut(BaseModel):
    """Plan & billing page (FR-PLT-030). ``available`` is false on dedicated hosts until the
    heartbeat carries a billing summary (M1); invoices are then sent by email."""

    model_config = ConfigDict(frozen=True)
    available: bool
    plan_code: str | None = None
    plan_name: str | None = None
    tier: str | None = None
    billing_period: str | None = None
    status: str | None = None
    current_period_start: dt.date | None = None
    current_period_end: dt.date | None = None
    trial_ends_at: dt.datetime | None = None
    past_due_since: dt.date | None = None
    grace_ends_on: dt.date | None = None
    cancel_at_period_end: bool | None = None
    usage_date: dt.date | None = None
    usage: list[UsageAgainstLimit] = []
    amount_due_inr: Decimal = Decimal("0.00")
    ai_bundle: SchoolAiBundle | None = None


def _usage(limits: dict[str, Any], usage: dict[str, Any] | None) -> list[UsageAgainstLimit]:
    out = []
    for key, field in _USAGE_FOR_LIMIT.items():
        raw = (usage or {}).get(field, 0) or 0
        used = Decimal(str(raw))
        if key == "storage_gb":
            used = (used / Decimal(10**9)).quantize(Decimal("0.01"))
        limit = limits.get(key)
        lim = Decimal(str(limit)) if limit not in (None, "") else None
        pct = int(used * 100 / lim) if lim else None
        out.append(UsageAgainstLimit(metric=key, used=used, limit=lim, percent=pct))
    return out


def _invoices(data: dict[str, Any] | None) -> list[TenantInvoice]:
    return [TenantInvoice.model_validate(i) for i in (data or {}).get("invoices", [])]


@router.get("/tenant/billing", response_model=TenantBillingOut)
def get_billing(ctx: BillingReader, db: TenantDB) -> TenantBillingOut:
    """Current plan, status, period, usage vs limits and the AI answer bundle (permission
    ``tenant.billing.read``). ``ai_bundle`` is ``null`` when the school has no bundle."""
    data = service.current_subscription(db)
    if data is None:
        return TenantBillingOut(available=False)
    invoices = _invoices(data)
    return TenantBillingOut(
        available=True,
        plan_code=data["plan_code"],
        plan_name=data["plan_name"],
        tier=data["tier"],
        billing_period=data["billing_period"],
        status=data["status"],
        current_period_start=data["current_period_start"],
        current_period_end=data["current_period_end"],
        trial_ends_at=data["trial_ends_at"],
        past_due_since=data["past_due_since"],
        grace_ends_on=data["grace_ends_on"],
        cancel_at_period_end=data["cancel_at_period_end"],
        usage_date=(data.get("usage") or {}).get("usage_date"),
        usage=_usage(dict(data.get("limits") or {}), data.get("usage")),
        amount_due_inr=sum((i.amount_due_inr for i in invoices), Decimal("0.00")),
        ai_bundle=service.school_ai_bundle(ctx.tenant_id, uuid.UUID(str(data["subscription_id"]))),
    )


@router.get("/tenant/billing/invoices", response_model=Page[TenantInvoice])
def list_billing_invoices(ctx: BillingReader, db: TenantDB) -> Page[TenantInvoice]:
    """This school's issued invoices, newest first (last 24; permission ``tenant.billing.read``)."""
    return Page[TenantInvoice](data=_invoices(service.current_subscription(db)), next_cursor=None)


@router.get("/announcements", response_model=list[AnnouncementBrief])
def list_announcements(ctx: Member) -> list[AnnouncementBrief]:
    """Active platform announcements for this school (any active member; EN and TE text)."""
    tier = get_settings().deployment_mode.value
    return service.active_announcements(ctx.tenant_id, tier)


@router.post("/support/tickets", response_model=TicketOut, status_code=201)
def open_ticket(
    ctx: TicketUser, db: TenantDB, body: TicketCreateSchool, idem: IdempotencyDep
) -> Response:
    """Open a support ticket (permission ``support.ticket.create``). Do not include student
    names, dates of birth, Aadhaar or phone numbers: text is redacted before it is stored."""

    def operation() -> TicketOut:
        ticket = service.open_ticket_from_tenant(ctx.tenant_id, ctx.user_id, body)
        audit.record(
            db,
            action="support.ticket_opened",
            resource_type="support_ticket",
            resource_id=ticket.id,
            summary={"category": body.category, "priority": body.priority},
            request_id=ctx.request_id,
        )
        return ticket

    return idem.run(
        db,
        body,
        operation,
        headers=lambda t: {"Location": f"/api/v1/support/tickets/{t.id}"},
    )


@router.get("/support/tickets", response_model=Page[TicketOut])
def list_tickets(ctx: TicketUser, limit: Limit = 50, cursor: Cursor = None) -> Page[TicketOut]:
    """This school's tickets, newest first (permission ``support.ticket.create``): the ones
    you opened, or every ticket of the school with ``support.manage``."""
    items, nxt = service.list_tenant_tickets(
        ctx.tenant_id, viewer=_ticket_viewer(ctx), limit=limit, cursor=cursor
    )
    return Page[TicketOut](data=items, next_cursor=nxt)


@router.get("/support/tickets/{ticket_id}", response_model=TicketOut)
def get_ticket(ctx: TicketUser, ticket_id: uuid.UUID) -> TicketOut:
    """One of this school's tickets with its messages (internal notes are never shown). 404
    for a ticket someone else opened unless you hold ``support.manage``."""
    return service.get_tenant_ticket(ctx.tenant_id, ticket_id, viewer=_ticket_viewer(ctx))


@router.post("/support/tickets/{ticket_id}/messages", response_model=TicketOut)
def reply_to_ticket(
    ctx: TicketUser,
    db: TenantDB,
    ticket_id: uuid.UUID,
    body: SchoolTicketMessageIn,
    idem: IdempotencyDep,
) -> Response:
    """Reply on a ticket you opened, or on any ticket of the school with ``support.manage``
    (404 otherwise; permission ``support.ticket.create``). Accepts
    ``Idempotency-Key``: a retry with the same key does not post the reply twice."""

    def operation() -> TicketOut:
        ticket = service.reply_from_tenant(
            ctx.tenant_id, ctx.user_id, ticket_id, body, manager=ctx.has(SUPPORT_MANAGE)
        )
        audit.record(
            db,
            action="support.ticket_updated",
            resource_type="support_ticket",
            resource_id=ticket_id,
            summary={"message": "school_reply"},
            request_id=ctx.request_id,
        )
        return ticket

    return idem.run(db, body, operation, status_code=200)
