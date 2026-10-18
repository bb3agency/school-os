"""Razorpay over httpx, no SDK (ADR-0042 decisions 3, 6, 8; FR-PAY-001..016; FR-FEE-028).

Calls go only to ``https://api.razorpay.com`` and ``https://auth.razorpay.com``
(``payments.yaml``) and only to the endpoints in :data:`ALLOWED_ENDPOINTS` (FR-PAY-016: a test
pins the list; there is no payout, transfer, Route account or order creation). Path ids are
shape-checked before they are put into a URL.

Authentication: the school's own key id and secret (HTTP Basic; manual keys for pilots) or the
OAuth access token of the school's grant (``Authorization: Bearer``). OAuth token calls use the
partner's client id and secret from the settings. Sources (checked 2026-10-18):

- Payment Links: https://razorpay.com/docs/api/payments/payment-links/create-standard/
- Payments: https://razorpay.com/docs/api/payments/fetch-with-id/ and
  https://razorpay.com/docs/api/payments/fetch-all-payments/ (``count`` at most 100)
- Refunds: https://razorpay.com/docs/api/refunds/create-normal/ (``receipt`` is our
  idempotency reference)
- Settlement recon: https://razorpay.com/docs/api/settlements/fetch-recon/
- OAuth: https://razorpay.com/docs/partners/technology-partners/onboard-businesses/integrate-oauth/integration-steps
- Partner webhooks: https://razorpay.com/docs/api/partners/webhooks/create.md

Money: Razorpay counts INR in paise. :func:`to_paise` and :func:`from_paise` are the only
conversions in SchoolOS and are exact (a value that is not a whole number of paise is refused,
never rounded; no float anywhere). Nothing here logs a key, a token, a body or a payer detail.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import re
from decimal import Decimal, InvalidOperation
from typing import Any, Final
from urllib.parse import urlencode

import httpx

from app.core.config import Settings, get_settings
from app.fees.payments_config import load_payments_config
from app.fees.providers.base import (
    Credentials,
    LinkRequest,
    LinkStatus,
    Order,
    Payment,
    PaymentLink,
    PaymentMethod,
    PaymentStatus,
    ProviderError,
    Refund,
    RefundStatus,
    SettlementItem,
    Tokens,
)

HUNDRED: Final = Decimal(100)
CENT: Final = Decimal("0.01")
_ID = r"[A-Za-z0-9]{1,30}"
ALLOWED_ENDPOINTS: Final[tuple[tuple[str, str, str], ...]] = (
    # (host, method, path pattern). FR-PAY-016: reads, links and refunds on the school's own
    # account; OAuth tokens; the webhook of the school's account. Nothing else.
    ("api", "GET", r"^/v1/payments$"),
    ("api", "GET", rf"^/v1/payments/pay_{_ID}$"),
    ("api", "GET", rf"^/v1/orders/order_{_ID}$"),
    ("api", "POST", r"^/v1/payment_links$"),
    ("api", "GET", rf"^/v1/payment_links/plink_{_ID}$"),
    ("api", "POST", rf"^/v1/payment_links/plink_{_ID}/cancel$"),
    ("api", "POST", rf"^/v1/payments/pay_{_ID}/refund$"),
    ("api", "GET", rf"^/v1/payments/pay_{_ID}/refunds/rfnd_{_ID}$"),
    ("api", "GET", r"^/v1/settlements/recon/combined$"),
    ("api", "POST", rf"^/v2/accounts/acc_{_ID}/webhooks$"),
    ("api", "PATCH", rf"^/v2/accounts/acc_{_ID}/webhooks/[A-Za-z0-9_]{{1,40}}$"),
    ("api", "DELETE", rf"^/v2/accounts/acc_{_ID}/webhooks/[A-Za-z0-9_]{{1,40}}$"),
    ("auth", "POST", r"^/token$"),
    ("auth", "POST", r"^/revoke$"),
)
_PATTERNS: Final = tuple((h, m, re.compile(p)) for h, m, p in ALLOWED_ENDPOINTS)
_METHODS: Final[dict[str, PaymentMethod]] = {
    "upi": "upi",
    "card": "card",
    "netbanking": "netbanking",
    "wallet": "wallet",
    "emi": "emi",
    "cardless_emi": "emi",
}
_PAYMENT_STATUSES: Final = frozenset({"created", "authorized", "captured", "refunded", "failed"})
_LINK_STATUSES: Final = frozenset({"created", "partially_paid", "paid", "expired", "cancelled"})
_REFUND_STATUSES: Final = frozenset({"pending", "processed", "failed"})


# --- money (FR-FEE-028) --------------------------------------------------------------------------


def to_paise(amount: Decimal) -> int:
    """Exact rupees -> paise. Refuses floats, more than two decimals and non-finite values."""
    if not isinstance(amount, Decimal) or not amount.is_finite():
        raise ValueError("amounts are exact decimals")
    paise = amount * HUNDRED
    if paise != paise.to_integral_value():
        raise ValueError("not a whole number of paise")
    return int(paise)


def from_paise(value: object) -> Decimal:
    """Exact paise -> rupees (``numeric(14,2)``). Refuses anything that is not an integer."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise ProviderError("bad_response")
    return (Decimal(value) / HUNDRED).quantize(CENT)


def _id(value: str, prefix: str) -> str:
    if not re.fullmatch(rf"{prefix}{_ID}", value or ""):
        raise ProviderError("invalid_request")
    return value


def _ts(value: object) -> dt.datetime | None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        return None
    return dt.datetime.fromtimestamp(value, tz=dt.UTC)


def _str(body: dict[str, Any], key: str) -> str | None:
    value = body.get(key)
    return value if isinstance(value, str) and value else None


def _need(body: dict[str, Any], key: str) -> str:
    value = _str(body, key)
    if value is None:
        raise ProviderError("bad_response")
    return value


def _money(body: dict[str, Any], key: str, *, optional: bool = False) -> Decimal | None:
    value = body.get(key)
    if value is None and optional:
        return None
    return from_paise(value)


# --- parsing --------------------------------------------------------------------------------------


def parse_link(body: dict[str, Any]) -> PaymentLink:
    status = _need(body, "status")
    if status not in _LINK_STATUSES:
        raise ProviderError("bad_response")
    link_status: LinkStatus = status  # type: ignore[assignment]
    return PaymentLink(
        id=_need(body, "id"),
        status=link_status,
        amount=from_paise(body.get("amount")),
        amount_paid=from_paise(body.get("amount_paid") or 0),
        currency=_need(body, "currency"),
        reference_id=_str(body, "reference_id"),
        short_url=_need(body, "short_url"),
        order_id=_str(body, "order_id"),
        expire_by=_ts(body.get("expire_by")),
    )


def parse_payment(body: dict[str, Any]) -> Payment:
    status = _need(body, "status")
    if status not in _PAYMENT_STATUSES:
        raise ProviderError("bad_response")
    payment_status: PaymentStatus = status  # type: ignore[assignment]
    created = _ts(body.get("created_at"))
    if created is None:
        raise ProviderError("bad_response")
    return Payment(
        id=_need(body, "id"),
        status=payment_status,
        amount=from_paise(body.get("amount")),
        currency=_need(body, "currency"),
        order_id=_str(body, "order_id"),
        method=_METHODS.get(str(body.get("method") or ""), "other"),
        captured=body.get("captured") is True,
        amount_refunded=from_paise(body.get("amount_refunded") or 0),
        fee=_money(body, "fee", optional=True),
        tax=_money(body, "tax", optional=True),
        created_at=created,
    )


def parse_order(body: dict[str, Any]) -> Order:
    return Order(
        id=_need(body, "id"),
        receipt=_str(body, "receipt"),
        amount=from_paise(body.get("amount")),
        currency=_need(body, "currency"),
        status=_need(body, "status"),
    )


def parse_refund(body: dict[str, Any]) -> Refund:
    status = _need(body, "status")
    if status not in _REFUND_STATUSES:
        raise ProviderError("bad_response")
    refund_status: RefundStatus = status  # type: ignore[assignment]
    created = _ts(body.get("created_at"))
    if created is None:
        raise ProviderError("bad_response")
    return Refund(
        id=_need(body, "id"),
        payment_id=_need(body, "payment_id"),
        status=refund_status,
        amount=from_paise(body.get("amount")),
        currency=_need(body, "currency"),
        receipt=_str(body, "receipt"),
        created_at=created,
    )


def parse_settlement_item(body: dict[str, Any]) -> SettlementItem:
    raw = body.get("amount")
    if raw is None:
        raw = body.get("credit") or body.get("debit") or 0
    return SettlementItem(
        entity_id=_need(body, "entity_id"),
        type=_need(body, "type"),
        payment_id=_str(body, "payment_id"),
        settlement_id=_str(body, "settlement_id"),
        settled=body.get("settled") is True,
        settled_at=_ts(body.get("settled_at")),
        amount=from_paise(raw),
    )


def _error(status: int) -> ProviderError:
    if status == 401:
        return ProviderError("auth_failed", status=status)
    if status == 404:
        return ProviderError("not_found", status=status)
    if status == 429:
        return ProviderError("rate_limited", status=status, transient=True)
    if status >= 500:
        return ProviderError("unavailable", status=status, transient=True)
    return ProviderError("invalid_request", status=status)


class RazorpayProvider:
    """``PaymentProvider`` for Razorpay (live mode, staging and production)."""

    name = "razorpay"

    def __init__(
        self, settings: Settings | None = None, *, client: httpx.Client | None = None
    ) -> None:
        self._settings = settings or get_settings()
        cfg = load_payments_config().razorpay
        self._hosts = {
            "api": str(cfg.api_base_url).rstrip("/"),
            "auth": str(cfg.auth_base_url).rstrip("/"),
        }
        self._client = client or httpx.Client(timeout=cfg.timeout_s, follow_redirects=False)

    # --- plumbing ---------------------------------------------------------------------------------

    def _call(
        self,
        host: str,
        method: str,
        path: str,
        *,
        creds: Credentials | None = None,
        params: dict[str, str | int] | None = None,
        json: Any = None,
        form: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        if not any(h == host and m == method and p.match(path) for h, m, p in _PATTERNS):
            # FR-PAY-016: never anything outside the pinned list.
            raise ProviderError("endpoint_not_allowed")
        headers: dict[str, str] = {}
        auth: tuple[str, str] | None = None
        if creds is not None:
            if creds.access_token is not None:
                headers["Authorization"] = f"Bearer {creds.access_token}"
            else:
                auth = (creds.key_id or "", creds.key_secret or "")
        try:
            response = self._client.request(
                method,
                f"{self._hosts[host]}{path}",
                params=params,
                json=json,
                data=form,
                headers=headers,
                auth=auth,
            )
        except httpx.HTTPError:
            raise ProviderError("unavailable", transient=True) from None
        if response.status_code >= 400:
            raise _error(response.status_code)
        if response.status_code == 204 or not response.content:
            return {}
        try:
            body: Any = response.json()
        except ValueError:
            raise ProviderError("bad_response") from None
        if not isinstance(body, dict):
            raise ProviderError("bad_response")
        return body

    def _partner(self) -> tuple[str, str]:
        client_id = self._settings.razorpay_partner_client_id
        secret = self._settings.razorpay_partner_client_secret
        if not client_id or secret is None:
            raise ProviderError("not_configured")
        return client_id, secret.get_secret_value()

    # --- the school's account ---------------------------------------------------------------------

    def check_credentials(self, creds: Credentials) -> None:
        self._call("api", "GET", "/v1/payments", creds=creds, params={"count": 1})

    def create_link(self, creds: Credentials, request: LinkRequest) -> PaymentLink:
        body: dict[str, Any] = {
            "amount": to_paise(request.amount),
            "currency": "INR",
            "accept_partial": False,
            "reference_id": request.reference_id,
            "description": request.description,
            "expire_by": int(request.expire_by.timestamp()),
            # ADR-0042 decision 8: Razorpay sends nothing to the payer; no customer object.
            "notify": {"sms": False, "email": False},
            "reminder_enable": False,
            "notes": dict(request.notes),
        }
        if request.callback_url:
            body["callback_url"] = request.callback_url
            body["callback_method"] = "get"
        return parse_link(self._call("api", "POST", "/v1/payment_links", creds=creds, json=body))

    def fetch_link(self, creds: Credentials, link_id: str) -> PaymentLink:
        path = f"/v1/payment_links/{_id(link_id, 'plink_')}"
        return parse_link(self._call("api", "GET", path, creds=creds))

    def cancel_link(self, creds: Credentials, link_id: str) -> PaymentLink:
        path = f"/v1/payment_links/{_id(link_id, 'plink_')}/cancel"
        return parse_link(self._call("api", "POST", path, creds=creds))

    def fetch_payment(self, creds: Credentials, payment_id: str) -> Payment:
        path = f"/v1/payments/{_id(payment_id, 'pay_')}"
        return parse_payment(self._call("api", "GET", path, creds=creds))

    def fetch_order(self, creds: Credentials, order_id: str) -> Order:
        path = f"/v1/orders/{_id(order_id, 'order_')}"
        return parse_order(self._call("api", "GET", path, creds=creds))

    def list_payments(
        self, creds: Credentials, start: dt.datetime, end: dt.datetime, *, skip: int, count: int
    ) -> list[Payment]:
        body = self._call(
            "api",
            "GET",
            "/v1/payments",
            creds=creds,
            params={
                "from": int(start.timestamp()),
                "to": int(end.timestamp()),
                "count": max(1, min(count, 100)),
                "skip": max(0, skip),
            },
        )
        items = body.get("items")
        if not isinstance(items, list):
            raise ProviderError("bad_response")
        return [parse_payment(i) for i in items if isinstance(i, dict)]

    def create_refund(
        self,
        creds: Credentials,
        payment_id: str,
        amount: Decimal,
        *,
        receipt: str,
        notes: dict[str, str],
    ) -> Refund:
        path = f"/v1/payments/{_id(payment_id, 'pay_')}/refund"
        body = {
            "amount": to_paise(amount),
            "speed": "normal",
            "receipt": receipt,
            "notes": dict(notes),
        }
        return parse_refund(self._call("api", "POST", path, creds=creds, json=body))

    def fetch_refund(self, creds: Credentials, payment_id: str, refund_id: str) -> Refund:
        path = f"/v1/payments/{_id(payment_id, 'pay_')}/refunds/{_id(refund_id, 'rfnd_')}"
        return parse_refund(self._call("api", "GET", path, creds=creds))

    def settlement_items(
        self, creds: Credentials, day: dt.date, *, skip: int, count: int
    ) -> list[SettlementItem]:
        body = self._call(
            "api",
            "GET",
            "/v1/settlements/recon/combined",
            creds=creds,
            params={
                "year": day.year,
                "month": day.month,
                "day": day.day,
                "count": max(1, min(count, 1000)),
                "skip": max(0, skip),
            },
        )
        items = body.get("items")
        if not isinstance(items, list):
            raise ProviderError("bad_response")
        return [parse_settlement_item(i) for i in items if isinstance(i, dict)]

    # --- connection (OAuth) -----------------------------------------------------------------------

    def authorize_url(self, state: str) -> str:
        client_id, _ = self._partner()
        redirect = self._settings.razorpay_oauth_redirect_uri
        if not redirect:
            raise ProviderError("not_configured")
        query = urlencode(
            {
                "client_id": client_id,
                "response_type": "code",
                "redirect_uri": redirect,
                "scope": load_payments_config().razorpay.oauth_scope,
                "state": state,
            }
        )
        return f"{self._hosts['auth']}/authorize?{query}"

    def _tokens(self, body: dict[str, Any]) -> Tokens:
        expires = body.get("expires_in")
        if isinstance(expires, bool) or not isinstance(expires, int) or expires <= 0:
            raise ProviderError("bad_response")
        return Tokens(
            access_token=_need(body, "access_token"),
            refresh_token=_need(body, "refresh_token"),
            expires_in=expires,
            account_id=_need(body, "razorpay_account_id"),
            live=self._settings.razorpay_oauth_mode == "live",
        )

    def exchange_code(self, code: str) -> Tokens:
        client_id, secret = self._partner()
        redirect = self._settings.razorpay_oauth_redirect_uri
        if not redirect:
            raise ProviderError("not_configured")
        form = {
            "client_id": client_id,
            "client_secret": secret,
            "grant_type": "authorization_code",
            "redirect_uri": redirect,
            "code": code,
            "mode": self._settings.razorpay_oauth_mode,
        }
        return self._tokens(self._call("auth", "POST", "/token", form=form))

    def refresh(self, refresh_token: str) -> Tokens:
        client_id, secret = self._partner()
        form = {
            "client_id": client_id,
            "client_secret": secret,
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
        }
        return self._tokens(self._call("auth", "POST", "/token", form=form))

    def revoke(self, token: str) -> None:
        client_id, secret = self._partner()
        form = {
            "client_id": client_id,
            "client_secret": secret,
            "token_type_hint": "access_token",
            "token": token,
        }
        self._call("auth", "POST", "/revoke", form=form)

    def create_webhook(
        self, creds: Credentials, account_id: str, url: str, secret: str, events: tuple[str, ...]
    ) -> str:
        path = f"/v2/accounts/{_id(account_id, 'acc_')}/webhooks"
        body = self._call(
            "api",
            "POST",
            path,
            creds=creds,
            json={"url": url, "secret": secret, "events": list(events)},
        )
        return _need(body, "id")

    def update_webhook(
        self,
        creds: Credentials,
        account_id: str,
        webhook_id: str,
        *,
        url: str,
        secret: str,
        events: tuple[str, ...],
    ) -> None:
        if not re.fullmatch(r"[A-Za-z0-9_]{1,40}", webhook_id or ""):
            raise ProviderError("invalid_request")
        path = f"/v2/accounts/{_id(account_id, 'acc_')}/webhooks/{webhook_id}"
        self._call(
            "api",
            "PATCH",
            path,
            creds=creds,
            json={"url": url, "secret": secret, "events": list(events)},
        )

    def delete_webhook(self, creds: Credentials, account_id: str, webhook_id: str) -> None:
        if not re.fullmatch(r"[A-Za-z0-9_]{1,40}", webhook_id or ""):
            raise ProviderError("invalid_request")
        path = f"/v2/accounts/{_id(account_id, 'acc_')}/webhooks/{webhook_id}"
        self._call("api", "DELETE", path, creds=creds)


def parse_amount(value: object) -> Decimal:
    """Rupees from a webhook's paise (tests and the inbox); raises ``ProviderError``."""
    try:
        return from_paise(value)
    except (InvalidOperation, ValueError):
        raise ProviderError("bad_response") from None


# --- webhooks (FR-PAY-007) ------------------------------------------------------------------------

SIGNATURE_HEADER: Final = "x-razorpay-signature"
EVENT_ID_HEADER: Final = "x-razorpay-event-id"


def signature(secret: bytes, body: bytes) -> str:
    """``X-Razorpay-Signature``: hex HMAC-SHA256 of the raw body with the webhook secret
    (https://razorpay.com/docs/webhooks/validate-test/)."""
    return hmac.new(secret, body, hashlib.sha256).hexdigest()


def signature_matches(secret: bytes, body: bytes, given: str) -> bool:
    expected = signature(secret, body).encode()
    return hmac.compare_digest(given.strip().lower().encode(), expected)


def _entity(payload: dict[str, Any], name: str) -> dict[str, Any] | None:
    holder = payload.get(name)
    if not isinstance(holder, dict):
        return None
    entity = holder.get("entity")
    return entity if isinstance(entity, dict) else None


def parse_webhook(body: dict[str, Any]) -> dict[str, Any]:
    """The minimised event kept in the inbox: event type, time, account and the ids, statuses
    and amounts (rupees) of the payment link, payment, order and refund. Payer contact, email,
    VPA, card, bank and notes other than our own ``intent``/``refund`` are dropped here and never
    stored (ADR-0042 decision 8; docs/05 §15.2). Raises ``ProviderError("bad_response")``."""
    event = _str(body, "event")
    if event is None or not re.fullmatch(r"[a-z][a-z0-9_.]{0,59}", event):
        raise ProviderError("bad_response")
    payload = body.get("payload")
    payload = payload if isinstance(payload, dict) else {}
    out: dict[str, Any] = {"event": event}
    created = _ts(body.get("created_at"))
    out["created_at"] = int(created.timestamp()) if created else None
    account = _str(body, "account_id")
    if account and re.fullmatch(rf"acc_{_ID}", account):
        out["account_id"] = account
    link = _entity(payload, "payment_link")
    if link is not None:
        out["link"] = {
            "id": _str(link, "id"),
            "status": _str(link, "status"),
            "reference_id": _str(link, "reference_id"),
            "order_id": _str(link, "order_id"),
            "amount": str(parse_amount(link.get("amount"))) if "amount" in link else None,
        }
    payment = _entity(payload, "payment")
    if payment is not None:
        out["payment"] = {
            "id": _str(payment, "id"),
            "status": _str(payment, "status"),
            "order_id": _str(payment, "order_id"),
            "currency": _str(payment, "currency"),
            "amount": str(parse_amount(payment.get("amount"))) if "amount" in payment else None,
        }
    order = _entity(payload, "order")
    if order is not None:
        out["order"] = {"id": _str(order, "id"), "receipt": _str(order, "receipt")}
    refund = _entity(payload, "refund")
    if refund is not None:
        notes = refund.get("notes")
        out["refund"] = {
            "id": _str(refund, "id"),
            "payment_id": _str(refund, "payment_id"),
            "status": _str(refund, "status"),
            "receipt": _str(refund, "receipt"),
            "note": notes.get("refund") if isinstance(notes, dict) else None,
            "amount": str(parse_amount(refund.get("amount"))) if "amount" in refund else None,
        }
    settlement = _entity(payload, "settlement")
    if settlement is not None:
        out["settlement"] = {"id": _str(settlement, "id"), "status": _str(settlement, "status")}
    return out


__all__ = [
    "ALLOWED_ENDPOINTS",
    "EVENT_ID_HEADER",
    "SIGNATURE_HEADER",
    "RazorpayProvider",
    "from_paise",
    "parse_amount",
    "parse_link",
    "parse_order",
    "parse_payment",
    "parse_refund",
    "parse_settlement_item",
    "parse_webhook",
    "signature",
    "signature_matches",
    "to_paise",
]
