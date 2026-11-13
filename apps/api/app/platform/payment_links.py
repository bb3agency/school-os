"""Online payment of SchoolOS's own invoices: Razorpay payment links (FA-13; ADR-0016;
FR-PAY-017..030; docs/16 §5.9a).

SchoolOS is the merchant for its own invoices: the link is on SchoolOS's Razorpay account and the
money goes there, never through a school's account (the school-as-merchant fees links are
``app.fees``, ADR-0042). Control plane only (``platform`` schema, ``platform_session``); no
student data, no payer details, nothing from a tenant table.

- **Create (FR-PAY-017, FR-PAY-018).** An operator with ``platform.invoices.manage`` makes one
  link for an issued invoice's balance due. At most one link is ``active`` per invoice (partial
  unique index). The provider call happens outside the database transaction; the invoice is
  re-checked under its row lock before the link is stored, and a link made for a balance that
  changed meanwhile is cancelled again (409 ``invoice_changed``).
- **Webhook (FR-PAY-019..022).** ``POST /api/v1/webhooks/schoolos-billing`` is guarded by
  ``require_webhook_signature("razorpay_schoolos")`` (HMAC-SHA256 under
  ``SOS_PLATFORM_RAZORPAY_WEBHOOK_SECRET``, replay window by event id). Only ``payment_link.paid``
  is acted on and only ids are read from the body: the link **and** the payment are re-fetched
  from Razorpay before anything is recorded. The payment must be captured, in INR, of the link's
  amount and on the link's order. It is recorded once (unique ``(provider,
  provider_payment_id)``), the invoice is re-settled as for a manual payment (``paid`` when
  covered, past due cleared) and ``payment.online_received`` is audited in the same transaction.
- **Needs review (FR-PAY-023).** A payment that cannot be applied (the invoice was voided or
  settled by hand, or its balance shrank below the link's amount) is never forced onto the
  invoice: the link becomes ``needs_review`` with a reason and ``payment_link.needs_review`` is
  audited; an operator refunds it in Razorpay's dashboard (refunds are not built here).
- **Check (FR-PAY-024).** "Check payment" re-fetches a link and applies a captured payment the
  webhook missed (same rules, same idempotency).
- **School page (FR-PAY-027).** :func:`school_payment_urls` gives the school's billing page the
  short URL of each of its own invoices' active links whose amount equals the balance due.

Links only: no UPI Autopay, e-mandate or subscription (lead default D87).
"""

from __future__ import annotations

import datetime as dt
import json
import re
import uuid
from collections.abc import Iterable, Mapping
from decimal import Decimal
from typing import Any, Literal

from sqlalchemy import RowMapping, and_, select, update
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.db import platform_session
from app.core.errors import Conflict, DomainError, NotFound, ServiceUnavailable
from app.core.ids import new_id
from app.core.logging import get_logger
from app.platform import billing
from app.platform import models as m
from app.platform import repository as repo
from app.platform.common import IST, SYSTEM, Actor, audit_platform, config, db_errors, must, now
from app.platform.link_providers import (
    InvoiceLinkRequest,
    LinkProviderError,
    ProviderLink,
    ProviderPayment,
    get_provider,
)
from app.platform.schemas import InvoicePaymentLinkOut, InvoicePaymentLinksOut

log = get_logger(__name__)

WebhookOutcome = Literal["applied", "already_recorded", "needs_review", "ignored"]


class PaymentLinkProviderFailed(DomainError):
    status, code, title = 502, "payment_link_failed", "Razorpay did not accept the request"


def _cfg() -> dict[str, Any]:
    section: dict[str, Any] = config()["payment_links"]
    return section


def enabled() -> bool:
    """``SOS_PLATFORM_PAYMENTS_ENABLED`` on the shared tier (FR-PAY-026)."""
    s = get_settings()
    return s.platform_payments_enabled and s.deployment_mode.value == "shared"


def _require_enabled() -> None:
    if not enabled():
        raise Conflict(
            "Online payment of invoices is not set up. Record the payment by hand instead.",
            code="online_payments_off",
        )


def _provider_failed(exc: LinkProviderError, action: str) -> DomainError:
    log.warning(
        "platform.payment_link.provider_failed",
        resource_type="invoice_payment_link",
        action=action,
        error_code=exc.code,
        outcome=str(exc.status) if exc.status is not None else None,
    )
    if exc.transient:
        return ServiceUnavailable("Razorpay is not answering. Try again in a few minutes.")
    return PaymentLinkProviderFailed(
        "Razorpay did not accept the request. Check SchoolOS's Razorpay account settings."
    )


def _balance(invoice: Mapping[Any, Any]) -> Decimal:
    return (
        Decimal(invoice["total_inr"])
        - Decimal(invoice["amount_paid_inr"])
        - Decimal(invoice["tds_inr"])
    )


def _out(row: Mapping[Any, Any]) -> InvoicePaymentLinkOut:
    return InvoicePaymentLinkOut.model_validate(dict(row))


def _expire_overdue(s: Session, invoice_id: uuid.UUID, at: dt.datetime) -> None:
    """Active links past their expiry are ``expired`` (Razorpay no longer accepts them)."""
    s.execute(
        update(m.invoice_payment_links)
        .where(
            m.invoice_payment_links.c.invoice_id == invoice_id,
            m.invoice_payment_links.c.status == "active",
            m.invoice_payment_links.c.expire_by <= at,
        )
        .values(status="expired", version=m.invoice_payment_links.c.version + 1)
    )


def _active_link(s: Session, invoice_id: uuid.UUID) -> RowMapping | None:
    return repo.get_by(
        s,
        m.invoice_payment_links,
        m.invoice_payment_links.c.invoice_id == invoice_id,
        m.invoice_payment_links.c.status == "active",
        for_update=True,
    )


def _open_invoice(s: Session, invoice_id: uuid.UUID) -> RowMapping:
    invoice = repo.get(s, m.invoices, invoice_id, for_update=True)
    if invoice is None:
        raise NotFound("Invoice not found")
    if invoice["status"] != "issued" or _balance(invoice) <= 0:
        raise Conflict(
            "A payment link is made only for an issued invoice with a balance due.",
            code="invalid_state",
        )
    return invoice


# --- operator actions -------------------------------------------------------------------------


def list_links(invoice_id: uuid.UUID) -> InvoicePaymentLinksOut:
    """The invoice's payment links, newest first, and whether online payment is set up."""
    with platform_session() as s:
        if repo.get(s, m.invoices, invoice_id) is None:
            raise NotFound("Invoice not found")
        _expire_overdue(s, invoice_id, now())
        rows = s.execute(
            select(m.invoice_payment_links)
            .where(m.invoice_payment_links.c.invoice_id == invoice_id)
            .order_by(
                m.invoice_payment_links.c.created_at.desc(), m.invoice_payment_links.c.id.desc()
            )
        ).mappings()
        return InvoicePaymentLinksOut(enabled=enabled(), data=[_out(r) for r in rows])


def get_link(link_id: uuid.UUID) -> InvoicePaymentLinkOut:
    with platform_session() as s:
        row = repo.get(s, m.invoice_payment_links, link_id)
        if row is None:
            raise NotFound("Payment link not found")
        return _out(row)


def create_link(actor: Actor, invoice_id: uuid.UUID) -> InvoicePaymentLinkOut:
    """A Razorpay payment link for the invoice's balance due (FR-PAY-017, FR-PAY-018).

    409 ``online_payments_off`` (not set up), ``invalid_state`` (not issued or nothing due),
    ``link_exists`` (an active link already exists), ``invoice_changed`` (the balance changed
    while the link was made); 502 ``payment_link_failed``; 503 when Razorpay is unreachable.
    Audit ``payment_link.created``."""
    _require_enabled()
    if actor.operator_id is None:
        raise Conflict("Payment links are made by an operator.", code="operator_required")
    cfg = _cfg()
    at = now()
    with platform_session() as s, db_errors():
        invoice = _open_invoice(s, invoice_id)
        _expire_overdue(s, invoice_id, at)
        if _active_link(s, invoice_id) is not None:
            raise Conflict("This invoice already has an active payment link.", code="link_exists")
        amount = _balance(invoice)
        number = str(invoice["invoice_number"])
    link_id = new_id()
    provider = get_provider()
    request = InvoiceLinkRequest(
        amount=amount,
        reference_id=str(link_id),
        description=str(cfg["description"]).format(invoice_number=number),
        expire_by=at + dt.timedelta(days=int(cfg["expiry_days"])),
        notes={"invoice_number": number},
    )
    try:
        made = provider.create_link(request)
    except LinkProviderError as exc:
        raise _provider_failed(exc, "create_link") from None
    try:
        with platform_session() as s, db_errors():
            invoice = _open_invoice(s, invoice_id)
            if _balance(invoice) != amount or _active_link(s, invoice_id) is not None:
                raise Conflict(
                    "The invoice changed while the link was made. Open it again and retry.",
                    code="invoice_changed",
                )
            row = repo.insert_row(
                s,
                m.invoice_payment_links,
                {
                    "id": link_id,
                    "invoice_id": invoice_id,
                    "tenant_id": invoice["tenant_id"],
                    "provider": provider.name,
                    "provider_link_id": made.id,
                    "short_url": made.short_url,
                    "amount_inr": amount,
                    "status": "active",
                    "expire_by": request.expire_by,
                    "created_by": actor.operator_id,
                },
            )
            audit_platform(
                s,
                actor,
                "payment_link.created",
                "invoice_payment_link",
                link_id,
                {"invoice_id": str(invoice_id), "amount_inr": str(amount)},
                tenant_id=invoice["tenant_id"],
            )
            return _out(row)
    except Exception:
        # Not stored: the link must not stay payable at Razorpay (best effort).
        try:
            provider.cancel_link(made.id)
        except LinkProviderError:
            log.warning(
                "platform.payment_link.orphan_cancel_failed",
                resource_type="invoice_payment_link",
                resource_id=str(link_id),
            )
        raise


def cancel_link(actor: Actor, link_id: uuid.UUID) -> InvoicePaymentLinkOut:
    """Cancel an active link at Razorpay, then here (FR-PAY-024). 409 ``invalid_state`` when it
    is not active; a link Razorpay reports as paid is applied instead (409 ``link_paid``)."""
    _require_enabled()
    with platform_session() as s:
        row = repo.get(s, m.invoice_payment_links, link_id)
        if row is None:
            raise NotFound("Payment link not found")
        if row["status"] != "active":
            raise Conflict("Only an active payment link can be cancelled.", code="invalid_state")
        provider_link_id = str(row["provider_link_id"])
    provider = get_provider()
    try:
        provider.cancel_link(provider_link_id)
    except LinkProviderError as exc:
        # Razorpay refuses to cancel a paid link: look at it and apply what was paid.
        if exc.code == "invalid_request":
            checked = check_link(actor, link_id)
            if checked.status != "active":
                raise Conflict(
                    "The link was paid before it could be cancelled.", code="link_paid"
                ) from None
        raise _provider_failed(exc, "cancel_link") from None
    with platform_session() as s, db_errors():
        row = repo.get(s, m.invoice_payment_links, link_id, for_update=True)
        row = must(row)
        if row["status"] != "active":
            return _out(row)
        row = repo.update_row(
            s,
            m.invoice_payment_links,
            link_id,
            {"status": "cancelled", "cancelled_by": actor.operator_id, "cancelled_at": now()},
        )
        audit_platform(
            s,
            actor,
            "payment_link.cancelled",
            "invoice_payment_link",
            link_id,
            {"invoice_id": str(row["invoice_id"])},
            tenant_id=row["tenant_id"],
        )
        return _out(row)


def check_link(actor: Actor, link_id: uuid.UUID) -> InvoicePaymentLinkOut:
    """Re-fetch a link from Razorpay and apply a captured payment the webhook missed
    (FR-PAY-024). Changes nothing when nothing was paid."""
    _require_enabled()
    with platform_session() as s:
        row = repo.get(s, m.invoice_payment_links, link_id)
        if row is None:
            raise NotFound("Payment link not found")
        provider_link_id = str(row["provider_link_id"])
        status = row["status"]
    if status == "active":
        provider = get_provider()
        try:
            link = provider.fetch_link(provider_link_id)
        except LinkProviderError as exc:
            raise _provider_failed(exc, "fetch_link") from None
        for payment_id in link.payment_ids:
            try:
                _apply(provider_link_id, payment_id, link=link)
            except LinkProviderError as exc:
                raise _provider_failed(exc, "fetch_payment") from None
    del actor  # the applied payment is the system's (it comes from Razorpay), audited as such
    return get_link(link_id)


# --- applying a provider payment (webhook and check) ------------------------------------------


def _received_on(payment: ProviderPayment) -> dt.date:
    return payment.created_at.astimezone(IST).date()


def _apply(
    provider_link_id: str, provider_payment_id: str, *, link: ProviderLink | None = None
) -> WebhookOutcome:
    """Apply one Razorpay payment of one of our links, after re-fetching both (FR-PAY-020).

    Idempotent: a payment already recorded answers ``already_recorded``; an unknown link
    (not ours) answers ``ignored``. Raises :class:`LinkProviderError` when Razorpay cannot be
    asked (the caller answers 503 so Razorpay retries)."""
    with platform_session() as s:
        row = repo.get_by(
            s,
            m.invoice_payment_links,
            m.invoice_payment_links.c.provider_link_id == provider_link_id,
        )
        if row is None:
            return "ignored"
        provider_name = str(row["provider"])
    provider = get_provider()
    if provider.name != provider_name:
        return "ignored"
    fetched_link = link or provider.fetch_link(provider_link_id)
    payment = provider.fetch_payment(provider_payment_id)
    if (
        payment.status != "captured"
        or not payment.captured
        or fetched_link.status != "paid"
        or provider_payment_id not in fetched_link.payment_ids
        or (fetched_link.order_id is not None and payment.order_id != fetched_link.order_id)
    ):
        # Not (yet) a captured payment of this link: nothing to record. A later event or a
        # check records it once Razorpay reports it captured.
        log.info(
            "platform.payment_link.not_captured",
            resource_type="invoice_payment_link",
            resource_id=str(row["id"]),
            outcome=payment.status,
        )
        return "ignored"
    with platform_session() as s, db_errors():
        # Lock order as everywhere in billing: the invoice, then its link.
        invoice = must(repo.get(s, m.invoices, row["invoice_id"], for_update=True))
        row = must(repo.get(s, m.invoice_payment_links, row["id"], for_update=True))
        existing = repo.get_by(
            s,
            m.payments,
            m.payments.c.provider == provider_name,
            m.payments.c.provider_payment_id == payment.id,
        )
        if existing is not None or row["status"] in ("paid", "needs_review"):
            # Recorded before (a retry, a replay, or the check and the webhook racing); a
            # link takes one payment (no partial payments), so nothing else can be due.
            return "already_recorded"
        amount = Decimal(row["amount_inr"])
        reason: str | None = None
        if payment.currency != "INR" or payment.amount != amount:
            reason = "amount_mismatch"
        elif invoice["status"] != "issued":
            reason = "invoice_not_open"
        elif amount > _balance(invoice):
            reason = "amount_exceeds_balance"
        # A link cancelled here while the payer was on the hosted page can still be paid.
        settled = {
            "settled_at": now(),
            "provider_payment_id": payment.id,
            "cancelled_by": None,
            "cancelled_at": None,
        }
        if reason is not None:
            repo.update_row(
                s,
                m.invoice_payment_links,
                row["id"],
                {"status": "needs_review", "review_reason": reason, **settled},
            )
            audit_platform(
                s,
                SYSTEM,
                "payment_link.needs_review",
                "invoice_payment_link",
                row["id"],
                {"invoice_id": str(invoice["id"]), "reason": reason},
                tenant_id=invoice["tenant_id"],
            )
            log.warning(
                "platform.payment_link.needs_review",
                resource_type="invoice_payment_link",
                resource_id=str(row["id"]),
                error_code=reason,
            )
            return "needs_review"
        recorded = repo.insert_row(
            s,
            m.payments,
            {
                "id": new_id(),
                "invoice_id": invoice["id"],
                "tenant_id": invoice["tenant_id"],
                "provider": provider_name,
                "method": payment.method,
                "amount_inr": amount,
                "tds_inr": Decimal("0.00"),
                "received_on": _received_on(payment),
                "reference": payment.id,
                "provider_payment_id": payment.id,
                "status": "recorded",
                "notes": None,
                "recorded_by": None,
            },
        )
        repo.update_row(
            s,
            m.invoice_payment_links,
            row["id"],
            {"status": "paid", "payment_id": recorded["id"], **settled},
        )
        billing.settle_invoice(s, invoice)
        audit_platform(
            s,
            SYSTEM,
            "payment.online_received",
            "payment",
            recorded["id"],
            {
                "invoice_id": str(invoice["id"]),
                "payment_link_id": str(row["id"]),
                "amount_inr": str(amount),
                "method": payment.method,
            },
            tenant_id=invoice["tenant_id"],
        )
        return "applied"


_PAYMENT_ID = re.compile(r"^pay_[A-Za-z0-9]{1,30}$")
_LINK_ID = re.compile(r"^plink_[A-Za-z0-9]{1,30}$")


def _entity_id(payload: Mapping[str, Any], name: str) -> str | None:
    holder = payload.get(name)
    entity = holder.get("entity") if isinstance(holder, dict) else None
    value = entity.get("id") if isinstance(entity, dict) else None
    return value if isinstance(value, str) else None


def _event_ids(body: bytes) -> tuple[str, str] | None:
    """(link id, payment id) of an event we act on, else ``None``. Nothing else is read."""
    try:
        event: Any = json.loads(body)
    except (ValueError, UnicodeDecodeError):
        return None
    if not isinstance(event, dict) or event.get("event") not in _cfg()["events"]:
        return None
    payload = event.get("payload")
    if not isinstance(payload, dict):
        return None
    link_id = _entity_id(payload, "payment_link")
    payment_id = _entity_id(payload, "payment")
    if not (link_id and payment_id and _LINK_ID.match(link_id) and _PAYMENT_ID.match(payment_id)):
        return None
    return link_id, payment_id


def handle_webhook(body: bytes) -> WebhookOutcome:
    """One verified Razorpay event of SchoolOS's account (FR-PAY-019). Only ids are read from
    the body; everything else is re-fetched. Raises :class:`ServiceUnavailable` when Razorpay
    cannot be asked (the route releases the event id so Razorpay's retry is processed)."""
    ids = _event_ids(body) if enabled() else None
    if ids is None:
        return "ignored"
    link_id, payment_id = ids
    try:
        outcome = _apply(link_id, payment_id)
    except LinkProviderError as exc:
        if exc.transient:
            raise ServiceUnavailable() from None
        log.warning(
            "platform.payment_link.refetch_failed",
            resource_type="invoice_payment_link",
            error_code=exc.code,
        )
        # A permanent failure (e.g. not found): Razorpay retrying will not help; an operator's
        # "Check payment" will. Acknowledge.
        return "ignored"
    log.info("platform.payment_link.webhook", resource_type="invoice_payment_link", outcome=outcome)
    return outcome


# --- the school's billing page ----------------------------------------------------------------


def school_payment_urls(
    tenant_id: uuid.UUID, invoices: Iterable[tuple[uuid.UUID, Decimal]]
) -> dict[uuid.UUID, str]:
    """For the calling school's own invoices ``(invoice_id, amount_due)``: the short URL of each
    active, unexpired link whose amount equals the amount due (FR-PAY-027). Platform rows of the
    caller's own tenant only; nothing when online payment is off."""
    due = dict(invoices)
    if not due or not enabled():
        return {}
    with platform_session() as s:
        rows = s.execute(
            select(
                m.invoice_payment_links.c.invoice_id,
                m.invoice_payment_links.c.short_url,
                m.invoice_payment_links.c.amount_inr,
            ).where(
                and_(
                    m.invoice_payment_links.c.tenant_id == tenant_id,
                    m.invoice_payment_links.c.invoice_id.in_(list(due)),
                    m.invoice_payment_links.c.status == "active",
                    m.invoice_payment_links.c.expire_by > now(),
                )
            )
        ).mappings()
        return {
            r["invoice_id"]: str(r["short_url"])
            for r in rows
            if Decimal(r["amount_inr"]) == due[r["invoice_id"]]
        }


__all__ = [
    "PaymentLinkProviderFailed",
    "WebhookOutcome",
    "cancel_link",
    "check_link",
    "create_link",
    "enabled",
    "get_link",
    "handle_webhook",
    "list_links",
    "school_payment_urls",
]
