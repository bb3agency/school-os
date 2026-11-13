"""Razorpay over httpx for SchoolOS's own invoices, no SDK (FA-13; ADR-0016; FR-PAY-025).

Calls go only to ``payment_links.api_base_url`` of ``billing.yaml`` and only to the endpoints in
:data:`ALLOWED_ENDPOINTS` (a test pins the list: payment links and a payment read; no refund,
payout, mandate or subscription). Authentication is SchoolOS's own key id and secret
(``SOS_PLATFORM_RAZORPAY_KEY_ID`` / ``_KEY_SECRET``, HTTP Basic). Path ids are shape-checked
before they are put into a URL. Sources (checked 2026-11-13, ``verified: false``):

- Payment Links: https://razorpay.com/docs/api/payments/payment-links/create-standard/
- Payments: https://razorpay.com/docs/api/payments/fetch-with-id/
- Webhook validation: https://razorpay.com/docs/webhooks/validate-test/

Money: Razorpay counts INR in paise. :func:`to_paise` and :func:`from_paise` are exact (a value
that is not a whole number of paise is refused, never rounded; no float). Nothing here logs a key,
a body or a payer detail.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import re
from decimal import Decimal
from typing import Any, Final

import httpx

from app.core.config import Settings, get_settings
from app.platform.common import config
from app.platform.link_providers.base import (
    InvoiceLinkRequest,
    LinkProviderError,
    LinkStatus,
    PaymentStatus,
    ProviderLink,
    ProviderPayment,
)

HUNDRED: Final = Decimal(100)
CENT: Final = Decimal("0.01")
_ID = r"[A-Za-z0-9]{1,30}"
ALLOWED_ENDPOINTS: Final[tuple[tuple[str, str], ...]] = (
    ("POST", r"^/v1/payment_links$"),
    ("GET", rf"^/v1/payment_links/plink_{_ID}$"),
    ("POST", rf"^/v1/payment_links/plink_{_ID}/cancel$"),
    ("GET", rf"^/v1/payments/pay_{_ID}$"),
)
_PATTERNS: Final = tuple((m, re.compile(p)) for m, p in ALLOWED_ENDPOINTS)
_LINK_STATUSES: Final = frozenset({"created", "partially_paid", "paid", "expired", "cancelled"})
_PAYMENT_STATUSES: Final = frozenset({"created", "authorized", "captured", "refunded", "failed"})
SIGNATURE_HEADER: Final = "x-razorpay-signature"
EVENT_ID_HEADER: Final = "x-razorpay-event-id"


def to_paise(amount: Decimal) -> int:
    """Exact rupees -> paise. Refuses floats, more than two decimals and non-finite values."""
    if not isinstance(amount, Decimal) or not amount.is_finite():
        raise ValueError("amounts are exact decimals")
    paise = amount * HUNDRED
    if paise != paise.to_integral_value():
        raise ValueError("not a whole number of paise")
    return int(paise)


def from_paise(value: object) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, int):
        raise LinkProviderError("bad_response")
    return (Decimal(value) / HUNDRED).quantize(CENT)


def signature(secret: bytes, body: bytes) -> str:
    """Razorpay's webhook signature: hex HMAC-SHA256 of the raw body under the secret."""
    return hmac.new(secret, body, hashlib.sha256).hexdigest()


def signature_matches(secret: bytes, body: bytes, given: str) -> bool:
    return hmac.compare_digest(given.strip().lower().encode(), signature(secret, body).encode())


def _id(value: str, prefix: str) -> str:
    if not re.fullmatch(rf"{prefix}{_ID}", value or ""):
        raise LinkProviderError("invalid_request")
    return value


def _str(body: dict[str, Any], key: str) -> str | None:
    value = body.get(key)
    return value if isinstance(value, str) and value else None


def _need(body: dict[str, Any], key: str) -> str:
    value = _str(body, key)
    if value is None:
        raise LinkProviderError("bad_response")
    return value


def parse_link(body: dict[str, Any]) -> ProviderLink:
    status = _need(body, "status")
    if status not in _LINK_STATUSES:
        raise LinkProviderError("bad_response")
    link_status: LinkStatus = status  # type: ignore[assignment]
    raw_payments = body.get("payments")
    payment_ids: list[str] = []
    if isinstance(raw_payments, list):
        for item in raw_payments:
            if isinstance(item, dict) and isinstance(item.get("payment_id"), str):
                payment_ids.append(item["payment_id"])
    return ProviderLink(
        id=_need(body, "id"),
        status=link_status,
        amount=from_paise(body.get("amount")),
        amount_paid=from_paise(body.get("amount_paid") or 0),
        currency=_need(body, "currency"),
        short_url=_need(body, "short_url"),
        reference_id=_str(body, "reference_id"),
        order_id=_str(body, "order_id"),
        payment_ids=tuple(payment_ids),
    )


def parse_payment(body: dict[str, Any]) -> ProviderPayment:
    status = _need(body, "status")
    if status not in _PAYMENT_STATUSES:
        raise LinkProviderError("bad_response")
    payment_status: PaymentStatus = status  # type: ignore[assignment]
    created = body.get("created_at")
    if isinstance(created, bool) or not isinstance(created, int) or created <= 0:
        raise LinkProviderError("bad_response")
    return ProviderPayment(
        id=_need(body, "id"),
        status=payment_status,
        amount=from_paise(body.get("amount")),
        currency=_need(body, "currency"),
        order_id=_str(body, "order_id"),
        method="upi" if body.get("method") == "upi" else "online",
        captured=body.get("captured") is True,
        created_at=dt.datetime.fromtimestamp(created, tz=dt.UTC),
    )


def _error(status: int) -> LinkProviderError:
    if status == 401:
        return LinkProviderError("auth_failed", status=status)
    if status == 404:
        return LinkProviderError("not_found", status=status)
    if status == 429:
        return LinkProviderError("rate_limited", status=status, transient=True)
    if status >= 500:
        return LinkProviderError("unavailable", status=status, transient=True)
    return LinkProviderError("invalid_request", status=status)


class RazorpayInvoiceLinks:
    """``InvoiceLinkProvider`` for SchoolOS's own Razorpay account (staging and production)."""

    name = "razorpay"

    def __init__(
        self, settings: Settings | None = None, *, client: httpx.Client | None = None
    ) -> None:
        self._settings = settings or get_settings()
        cfg = config()["payment_links"]
        self._base = str(cfg["api_base_url"]).rstrip("/")
        self._client = client or httpx.Client(
            timeout=float(cfg["timeout_s"]), follow_redirects=False
        )

    def _auth(self) -> tuple[str, str]:
        key_id = self._settings.platform_razorpay_key_id
        secret = self._settings.platform_razorpay_key_secret
        if not key_id or secret is None:
            raise LinkProviderError("not_configured")
        return key_id, secret.get_secret_value()

    def _call(self, method: str, path: str, *, json: Any = None) -> dict[str, Any]:
        if not any(m == method and p.match(path) for m, p in _PATTERNS):
            raise LinkProviderError("endpoint_not_allowed")
        auth = self._auth()
        try:
            response = self._client.request(method, f"{self._base}{path}", json=json, auth=auth)
        except httpx.HTTPError:
            raise LinkProviderError("unavailable", transient=True) from None
        if response.status_code >= 400:
            raise _error(response.status_code)
        try:
            body: Any = response.json()
        except ValueError:
            raise LinkProviderError("bad_response") from None
        if not isinstance(body, dict):
            raise LinkProviderError("bad_response")
        return body

    def create_link(self, request: InvoiceLinkRequest) -> ProviderLink:
        body: dict[str, Any] = {
            "amount": to_paise(request.amount),
            "currency": "INR",
            "accept_partial": False,
            "reference_id": request.reference_id,
            "description": request.description,
            "expire_by": int(request.expire_by.timestamp()),
            # No customer object and no provider messages: the school pays from its billing
            # page; Razorpay never receives a school contact from us (FR-PAY-018).
            "notify": {"sms": False, "email": False},
            "reminder_enable": False,
            "notes": dict(request.notes),
        }
        return parse_link(self._call("POST", "/v1/payment_links", json=body))

    def fetch_link(self, link_id: str) -> ProviderLink:
        return parse_link(self._call("GET", f"/v1/payment_links/{_id(link_id, 'plink_')}"))

    def cancel_link(self, link_id: str) -> ProviderLink:
        path = f"/v1/payment_links/{_id(link_id, 'plink_')}/cancel"
        return parse_link(self._call("POST", path))

    def fetch_payment(self, payment_id: str) -> ProviderPayment:
        return parse_payment(self._call("GET", f"/v1/payments/{_id(payment_id, 'pay_')}"))


__all__ = [
    "ALLOWED_ENDPOINTS",
    "EVENT_ID_HEADER",
    "SIGNATURE_HEADER",
    "RazorpayInvoiceLinks",
    "from_paise",
    "parse_link",
    "parse_payment",
    "signature",
    "signature_matches",
    "to_paise",
]
