"""The Razorpay webhook of SchoolOS's own account (FA-13; ADR-0016, ADR-0047 decision 4;
FR-PAY-019, FR-PAY-022, FR-PAY-028; SEC-034).

``POST /api/v1/webhooks/schoolos-billing`` takes no member: ``require_webhook_signature
("razorpay_schoolos")`` (public allowlist ``app/public/public_routes.yaml``) limits the body to
256 KB, runs :class:`SchoolOSRazorpayVerifier` and drops replays by Razorpay's event id. Mounted
with the control plane only (shared tier). The verifier:

1. answers 404 (``WebhookUnknownRoute``) unless online payment of SchoolOS invoices is enabled
   and ``SOS_PLATFORM_RAZORPAY_WEBHOOK_SECRET`` is set (fail closed);
2. requires ``X-Razorpay-Signature`` = hex HMAC-SHA256 of the raw body under that secret,
   compared in constant time; otherwise 401 without detail (``WebhookRejected``);
3. requires a well-formed ``x-razorpay-event-id`` (the replay key).

The handler reads ids only and re-fetches the link and the payment from Razorpay before
anything is recorded (:func:`app.platform.payment_links.handle_webhook`). Nothing here logs a
secret, a signature, a header value or the body.
"""

from __future__ import annotations

import re
from typing import Annotated

from fastapi import APIRouter, Depends, Response

from app.core.config import get_settings
from app.platform import payment_links
from app.platform.common import config
from app.platform.link_providers.razorpay import (
    EVENT_ID_HEADER,
    SIGNATURE_HEADER,
    signature_matches,
)
from app.public.config import WebhookProvider
from app.public.guards import WebhookCaller, forget_event, require_webhook_signature
from app.public.webhooks import (
    VerifiedWebhook,
    WebhookRejected,
    WebhookRequest,
    WebhookUnknownRoute,
    register_webhook_verifier,
    webhook_verifier,
)

PROVIDER: WebhookProvider = "razorpay_schoolos"
PATH = "/api/v1/webhooks/schoolos-billing"

router = APIRouter(tags=["webhooks"])
Delivery = Annotated[WebhookCaller, Depends(require_webhook_signature(PROVIDER))]


def _header(request: WebhookRequest, name: str) -> str:
    return request.headers.get(name) or request.headers.get(name.title()) or ""


class SchoolOSRazorpayVerifier:
    """The ``razorpay_schoolos`` verifier (see the module docstring)."""

    provider: WebhookProvider = PROVIDER

    def verify(self, request: WebhookRequest) -> VerifiedWebhook:
        secret = get_settings().platform_razorpay_webhook_secret
        if not payment_links.enabled() or secret is None:
            raise WebhookUnknownRoute()
        given = _header(request, SIGNATURE_HEADER).strip()
        if not given:
            raise WebhookRejected("signature_missing")
        if not re.fullmatch(r"[0-9a-fA-F]{64}", given):
            raise WebhookRejected("signature_malformed")
        if not signature_matches(secret.get_secret_value().encode(), request.body, given):
            raise WebhookRejected("signature_mismatch")
        event_id = _header(request, EVENT_ID_HEADER).strip()
        if not re.fullmatch(str(config()["payment_links"]["event_id_pattern"]), event_id):
            raise WebhookRejected("event_id_invalid")
        return VerifiedWebhook(event_id=event_id)


def install() -> None:
    """Register :class:`SchoolOSRazorpayVerifier` once per process (the API factory runs it with
    the control plane; tests may install their own first)."""
    if webhook_verifier(PROVIDER) is None:
        register_webhook_verifier(SchoolOSRazorpayVerifier())


@router.post(
    PATH,
    status_code=200,
    responses={
        401: {"description": "Bad or missing X-Razorpay-Signature (no detail)."},
        404: {"description": "Online payment of SchoolOS invoices is not set up."},
        413: {"description": "Larger than 256 KB."},
        503: {"description": "Razorpay could not be asked; Razorpay retries the event."},
    },
)
def schoolos_billing_webhook(delivery: Delivery) -> Response:
    """Razorpay events of SchoolOS's own account (payment links of SchoolOS invoices). Only
    ``payment_link.paid`` is acted on; the link and payment are re-fetched before the invoice
    is marked paid. A replayed event is acknowledged without effect."""
    if delivery.duplicate or delivery.event_id is None:
        return Response(status_code=200)
    try:
        payment_links.handle_webhook(delivery.body)
    except Exception:
        # Not processed: let Razorpay's retry through the replay check again.
        forget_event(PROVIDER, delivery.event_id)
        raise
    return Response(status_code=200)


__all__ = ["PATH", "PROVIDER", "SchoolOSRazorpayVerifier", "install", "router"]
