"""The Razorpay webhook verifier (ADR-0042 decision 7, ADR-0047 decision 3; FR-PAY-007, FR-PAY-014;
SEC-034, SEC-040).

:class:`RazorpayWebhookVerifier` is the ``razorpay`` verifier of the R2-0 registry
(``app.public.webhooks``), registered by :func:`install` from the API factory:

1. the path's route value must resolve to a live ``razorpay_route`` handle (a school whose
   connection is live); otherwise 404 (``WebhookUnknownRoute``), before anything else is read;
2. the school's webhook secrets are read in its own ``tenant_session``: the current one and,
   for 7 days after a rotation, the previous one (Razorpay retries older events with the old
   signature);
3. ``X-Razorpay-Signature`` must be the hex HMAC-SHA256 of the raw body under one of them,
   compared in constant time; otherwise 401 without detail (``WebhookRejected``);
4. ``x-razorpay-event-id`` must be present and well formed; it is the replay key of the guard
   and the unique key of the school's inbox.

Nothing here logs a secret, a signature, a header value or the body.
"""

from __future__ import annotations

import re

from app.fees import online
from app.fees.payments_config import load_payments_config
from app.fees.providers.razorpay import EVENT_ID_HEADER, SIGNATURE_HEADER, signature_matches
from app.public.config import WebhookProvider
from app.public.webhooks import (
    VerifiedWebhook,
    WebhookRejected,
    WebhookRequest,
    WebhookUnknownRoute,
    register_webhook_verifier,
    webhook_verifier,
)

ROUTE_PARAM = "route"


def _header(request: WebhookRequest, name: str) -> str:
    return request.headers.get(name) or request.headers.get(name.title()) or ""


class RazorpayWebhookVerifier:
    """The real Razorpay verifier: per-school secret, current or previous (see the module)."""

    provider: WebhookProvider = "razorpay"

    def verify(self, request: WebhookRequest) -> VerifiedWebhook:
        found = request.resolve("razorpay_route", request.path_params.get(ROUTE_PARAM, ""))
        if found is None:
            raise WebhookUnknownRoute()
        keys = online.webhook_secrets(found)
        if not keys:
            raise WebhookUnknownRoute()
        given = _header(request, SIGNATURE_HEADER).strip()
        if not given:
            raise WebhookRejected("signature_missing")
        if not re.fullmatch(r"[0-9a-fA-F]{64}", given):
            raise WebhookRejected("signature_malformed")
        if not any(signature_matches(key, request.body, given) for key in keys):
            raise WebhookRejected("signature_mismatch")
        event_id = _header(request, EVENT_ID_HEADER).strip()
        if not re.fullmatch(load_payments_config().webhook.event_id_pattern, event_id):
            raise WebhookRejected("event_id_invalid")
        return VerifiedWebhook(event_id=event_id, handles=(found,))


def install() -> None:
    """Register :class:`RazorpayWebhookVerifier` unless a ``razorpay`` verifier is already
    registered (the API factory runs it; tests may install their own first)."""
    if webhook_verifier("razorpay") is None:
        register_webhook_verifier(RazorpayWebhookVerifier())


__all__ = ["ROUTE_PARAM", "RazorpayWebhookVerifier", "install"]
