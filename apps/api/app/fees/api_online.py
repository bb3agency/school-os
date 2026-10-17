"""Online payment routes (docs/09 "Online payments", "Webhooks"; US-2106 AC2, US-2107, US-2108,
US-2111; FR-PAY-*; ADR-0042, ADR-0047).

Every route declares its guard (invariant 2). Connecting, testing, rotating and disconnecting the
school's Razorpay account need ``payments.configure`` with step-up (owner); reading the
connection ``payments.configure`` or ``finance.read``. Payment links need ``fee.collect`` (reads
also ``finance.read``); the online reconciliation list ``finance.read`` or ``fee.manage`` and
explaining an item ``fee.manage``. Fee permissions are school-wide only (FR-FEE-024). While
fees are off for the school every route answers 404 ``module_hidden``.

``POST /api/v1/webhooks/razorpay/{route}`` takes no member: ``require_webhook_signature
("razorpay")`` (route handle, the school's secret, ``X-Razorpay-Signature`` over the raw body,
256 KB) and it answers 200 once the event is stored (or was stored before).
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Response

from app.authz.context import UserContext
from app.authz.dependencies import TenantDB, require, require_any
from app.authz.http import Cursor, IdempotencyDep, Limit, OptionalIfMatch, etag
from app.fees import _common as c
from app.fees import online
from app.fees.online_schemas import (
    ConnectionTestOut,
    ExplainIn,
    ItemKind,
    ItemStatus,
    ManualConnectOut,
    ManualKeysIn,
    OAuthCallbackIn,
    OAuthStartOut,
    PaymentConnectionOut,
    PaymentIntentCreate,
    PaymentIntentOut,
    ReconciliationItemOut,
    ReconciliationPage,
    ReconciliationSummaryOut,
    WebhookSetupOut,
)
from app.public.guards import WebhookCaller, forget_event, require_webhook_signature

router = APIRouter(prefix="/api/v1", tags=["payments"])
webhook_router = APIRouter(prefix="/api/v1", tags=["webhooks"])

ConnectionReader = Annotated[UserContext, Depends(require_any(online.CONFIGURE, c.READ))]
Configurer = Annotated[
    UserContext, Depends(require(online.CONFIGURE, scope="school", step_up=True))
]
Collector = Annotated[UserContext, Depends(require(c.COLLECT, scope="school"))]
LinkReader = Annotated[UserContext, Depends(require_any(c.COLLECT, c.READ))]
ReconReader = Annotated[UserContext, Depends(require_any(c.READ, c.MANAGE))]
Manager = Annotated[UserContext, Depends(require(c.MANAGE, scope="school"))]
RazorpayDelivery = Annotated[WebhookCaller, Depends(require_webhook_signature("razorpay"))]
# The route value of the school's razorpay_route handle (24 letters and digits, ADR-0047); the
# guard resolves it (an unknown value is the uniform 404).
RouteValue = Annotated[str, Path(min_length=1, max_length=64)]

_NO_STORE = {"Cache-Control": "no-store"}


def _etag(response: Response, version: int) -> None:
    response.headers["ETag"] = etag(version)


# --- connection (FR-PAY-002..004, FR-PAY-012..014) ------------------------------------------------


@router.get("/payments/connection", response_model=PaymentConnectionOut)
def get_payment_connection(ctx: ConnectionReader, db: TenantDB) -> PaymentConnectionOut:
    """The school's Razorpay connection: mode, environment, status, account id, webhook URL,
    expiry dates and counts; never a secret (``payments.configure`` or ``finance.read``)."""
    return online.get_connection(db, ctx)


@router.post("/payments/connection/manual", response_model=ManualConnectOut, status_code=201)
def connect_payments_manual(
    ctx: Configurer, db: TenantDB, body: ManualKeysIn, response: Response
) -> ManualConnectOut:
    """Connect with the school's own key id and secret (pilots, D13; ``payments.configure``,
    step-up). The answer carries the webhook URL and secret **once** (``Cache-Control:
    no-store``; no idempotency replay, which would store the secret). Errors: 403
    ``manual_keys_not_allowed``; 409 ``payments_already_connected``; 422 ``keys_rejected``."""
    response.headers.update(_NO_STORE)
    return online.connect_manual(db, ctx, body)


@router.post("/payments/connection/oauth/start", response_model=OAuthStartOut)
def start_payments_oauth(ctx: Configurer, db: TenantDB) -> OAuthStartOut:
    """The Razorpay sign-in URL with a single-use ``state`` (10 minutes) bound to the member
    (``payments.configure``, step-up). 409 ``oauth_not_available`` before partner approval."""
    return online.oauth_start(db, ctx)


@router.post("/payments/connection/oauth/callback", response_model=PaymentConnectionOut)
def finish_payments_oauth(
    ctx: Configurer, db: TenantDB, body: OAuthCallbackIn
) -> PaymentConnectionOut:
    """Finish the sign-in: the BFF's callback page posts Razorpay's ``code`` and ``state`` here
    (body, never a URL the API logs). 403 ``oauth_state_invalid``."""
    return online.oauth_callback(db, ctx, body)


@router.post("/payments/connection/test", response_model=ConnectionTestOut)
def test_payment_connection(ctx: Configurer, db: TenantDB) -> ConnectionTestOut:
    """Check the connection with a read call; a refusal marks it revoked (FR-PAY-012)."""
    return online.test_connection(db, ctx)


@router.post("/payments/connection/rotate-webhook-secret", response_model=WebhookSetupOut)
def rotate_payment_webhook_secret(
    ctx: Configurer, db: TenantDB, response: Response
) -> WebhookSetupOut:
    """A new webhook secret, shown once; the previous one verifies retries for 7 more days
    (FR-PAY-014)."""
    response.headers.update(_NO_STORE)
    return online.rotate_webhook_secret(db, ctx)


@router.delete("/payments/connection", response_model=PaymentConnectionOut)
def disconnect_payments(ctx: Configurer, db: TenantDB) -> PaymentConnectionOut:
    """Disconnect: open links cancelled, webhook route revoked, secrets erased, history kept
    (FR-PAY-013)."""
    return online.disconnect(db, ctx)


# --- payment links (FR-PAY-005, FR-PAY-006, FR-PAY-015) -------------------------------------------


@router.post(
    "/fees/accounts/{account_id}/payment-intents",
    response_model=PaymentIntentOut,
    status_code=201,
)
def create_payment_intent(
    ctx: Collector,
    db: TenantDB,
    account_id: uuid.UUID,
    body: PaymentIntentCreate,
    idem: IdempotencyDep,
) -> Response:
    """A payment link for chosen instalments (``fee.collect``; send ``Idempotency-Key``): the
    amount is what is due on them now; an open link for the same instalments is reused
    (``reused: true``). Errors (409): ``payments_not_connected``, ``nothing_due``,
    ``account_closed``, ``too_many_links``; (502) ``payment_link_failed``."""
    return idem.run(
        db,
        body,
        lambda: online.create_intent_for_staff(db, ctx, account_id, body),
        headers=lambda o: {"Location": f"/api/v1/fees/payment-intents/{o.id}"},
    )


@router.get("/fees/accounts/{account_id}/payment-intents", response_model=list[PaymentIntentOut])
def list_payment_intents(
    ctx: LinkReader, db: TenantDB, account_id: uuid.UUID
) -> list[PaymentIntentOut]:
    """The account's payment links with their status (``fee.collect`` or ``finance.read``)."""
    return online.list_intents(db, ctx, account_id)


@router.get("/fees/payment-intents/{intent_id}", response_model=PaymentIntentOut)
def get_payment_intent(
    ctx: LinkReader, db: TenantDB, intent_id: uuid.UUID, response: Response
) -> PaymentIntentOut:
    out = online.get_intent(db, ctx, intent_id)
    _etag(response, out.version)
    return out


@router.post("/fees/payment-intents/{intent_id}/cancel", response_model=PaymentIntentOut)
def cancel_payment_intent(ctx: Collector, db: TenantDB, intent_id: uuid.UUID) -> PaymentIntentOut:
    """Cancel an unpaid link at Razorpay (``fee.collect``). 409 ``intent_closed``,
    ``intent_paid``."""
    return online.cancel_intent(db, ctx, intent_id)


# --- online reconciliation (FR-PAY-009, FR-PAY-010) -----------------------------------------------


@router.get("/fees/reconciliation/online", response_model=ReconciliationPage)
def list_online_reconciliation(
    *,
    ctx: ReconReader,
    db: TenantDB,
    status: ItemStatus | None = None,
    kind: ItemKind | None = None,
    limit: Limit = 50,
    cursor: Cursor = None,
) -> ReconciliationPage:
    """Online payment exceptions, newest first (``finance.read`` or ``fee.manage``)."""
    return online.list_items(db, ctx, status=status, kind=kind, limit=limit, cursor=cursor)


@router.get("/fees/reconciliation/online/summary", response_model=ReconciliationSummaryOut)
def online_reconciliation_summary(ctx: ReconReader, db: TenantDB) -> ReconciliationSummaryOut:
    """Open exceptions, webhook events waiting or failed, and the last 14 daily runs."""
    return online.summary(db, ctx)


@router.post(
    "/fees/reconciliation/online/items/{recon_item_id}/explain",
    response_model=ReconciliationItemOut,
)
def explain_online_item(
    *,
    ctx: Manager,
    db: TenantDB,
    recon_item_id: uuid.UUID,
    body: ExplainIn,
    expected: OptionalIfMatch,
    response: Response,
) -> ReconciliationItemOut:
    """Mark an exception explained with a note of 10-500 characters (``fee.manage``; audited
    without the note). 409 ``item_closed``."""
    out = online.explain_item(db, ctx, recon_item_id, body, expected_version=expected)
    _etag(response, out.version)
    return out


# --- Razorpay webhook (FR-PAY-007; ADR-0047) ------------------------------------------------------


@webhook_router.post(
    "/webhooks/razorpay/{route}",
    status_code=200,
    responses={
        401: {"description": "Bad or missing X-Razorpay-Signature (no detail)."},
        404: {"description": "Unknown or disconnected route."},
        413: {"description": "Larger than 256 KB."},
    },
)
def razorpay_webhook(route: RouteValue, delivery: RazorpayDelivery) -> Response:
    """One school's Razorpay events. The signature (the school's secret over the raw body) was
    checked by the guard; the minimised event is stored encrypted, deduplicated by Razorpay's
    event id, and the worker re-fetches the payment before any receipt. A replayed event is
    acknowledged without effect."""
    del route
    if delivery.duplicate or delivery.event_id is None:
        return Response(status_code=200)
    try:
        for handle in delivery.handles:
            online.store_event(handle, delivery.event_id, delivery.body)
    except Exception:
        # Not stored: let Razorpay's retry through the replay check again.
        forget_event("razorpay", delivery.event_id)
        raise
    return Response(status_code=200)


__all__ = ["router", "webhook_router"]
