"""The payment provider interface (ADR-0042 decision 3; FR-PAY-001, FR-PAY-016).

A provider talks to **one school's own** account (the school is the merchant; SchoolOS never
receives, holds or routes money, ADR-0042 decision 2). Implementations: :mod:`.razorpay` (the
Razorpay REST API over httpx, no SDK) and :mod:`.fake` (deterministic and offline; local and CI
only, refused in staging/prod). Only this package makes HTTP calls to a payment provider
(import-linter ``fees-provider-egress``).

Amounts cross this interface as exact ``Decimal`` rupees; the conversion to paise happens only
inside :mod:`.razorpay` with an exact check (FR-FEE-028). What a provider returns is reduced to
the fields SchoolOS needs: no payer contact, email, VPA or card details ever leave this package
(ADR-0042 decision 8, PRV-024).

Errors are :class:`ProviderError` with a short code (``auth_failed``, ``not_found``,
``invalid_request``, ``rate_limited``, ``unavailable``, ``bad_response``, ``not_configured``);
``transient`` says whether a retry may help. Nothing here logs a key, a token or a payload.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Literal, Protocol, runtime_checkable

PaymentMethod = Literal["upi", "card", "netbanking", "wallet", "emi", "other"]
PaymentStatus = Literal["created", "authorized", "captured", "refunded", "failed"]
LinkStatus = Literal["created", "partially_paid", "paid", "expired", "cancelled"]
RefundStatus = Literal["pending", "processed", "failed"]


class ProviderError(Exception):
    """A provider call failed (reason code only; never a body, key or token)."""

    def __init__(self, code: str, *, status: int | None = None, transient: bool = False) -> None:
        super().__init__(code if status is None else f"{code}:{status}")
        self.code = code
        self.status = status
        self.transient = transient


@dataclass(frozen=True, slots=True)
class Credentials:
    """How to call one school's account: its own key id and secret (manual keys, pilots) or an
    OAuth access token (Technology Partner). ``repr`` never shows a secret."""

    key_id: str | None = None
    key_secret: str | None = field(default=None, repr=False)
    access_token: str | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        basic = self.key_id is not None and self.key_secret is not None
        if basic == (self.access_token is not None):
            raise ValueError("credentials are either a key pair or an access token")

    @property
    def fingerprint(self) -> str:
        """A non-secret name of the account the fake keys its state by (key id or token tail)."""
        if self.key_id is not None:
            return self.key_id
        token = self.access_token or ""
        return "oauth:" + token[-6:]


@dataclass(frozen=True, slots=True)
class LinkRequest:
    """Everything a payment link carries (FR-PAY-005; ADR-0042 decision 8). No ``customer``,
    no notifications by the provider, no partial payments."""

    amount: Decimal
    reference_id: str
    description: str
    expire_by: dt.datetime
    notes: dict[str, str]
    callback_url: str | None = None


@dataclass(frozen=True, slots=True)
class PaymentLink:
    id: str
    status: LinkStatus
    amount: Decimal
    amount_paid: Decimal
    currency: str
    reference_id: str | None
    short_url: str
    order_id: str | None
    expire_by: dt.datetime | None


@dataclass(frozen=True, slots=True)
class Payment:
    """A payment as the provider reports it on a re-fetch (FR-PAY-008), minimised."""

    id: str
    status: PaymentStatus
    amount: Decimal
    currency: str
    order_id: str | None
    method: PaymentMethod
    captured: bool
    amount_refunded: Decimal
    fee: Decimal | None
    tax: Decimal | None
    created_at: dt.datetime


@dataclass(frozen=True, slots=True)
class Order:
    """The order behind a payment link: its ``receipt`` is the link's ``reference_id``."""

    id: str
    receipt: str | None
    amount: Decimal
    currency: str
    status: str


@dataclass(frozen=True, slots=True)
class Refund:
    id: str
    payment_id: str
    status: RefundStatus
    amount: Decimal
    currency: str
    receipt: str | None
    created_at: dt.datetime


@dataclass(frozen=True, slots=True)
class SettlementItem:
    """One settled entity of the settlement report (payments and refunds)."""

    entity_id: str
    type: str
    payment_id: str | None
    settlement_id: str | None
    settled: bool
    settled_at: dt.datetime | None
    amount: Decimal


@dataclass(frozen=True, slots=True)
class Tokens:
    """An OAuth grant (ADR-0042 decision 6): never logged, wrapped at rest."""

    access_token: str = field(repr=False)
    refresh_token: str = field(repr=False)
    expires_in: int
    account_id: str
    live: bool


@runtime_checkable
class PaymentProvider(Protocol):
    """Every call SchoolOS makes to a payment provider (FR-PAY-016: links, payments, orders,
    refunds and settlement reads on the school's own account; OAuth and webhooks for the
    connection). There is deliberately no payout, transfer, Route account or order creation."""

    name: str

    # --- the school's account ------------------------------------------------------------------
    def check_credentials(self, creds: Credentials) -> None: ...
    def create_link(self, creds: Credentials, request: LinkRequest) -> PaymentLink: ...
    def fetch_link(self, creds: Credentials, link_id: str) -> PaymentLink: ...
    def cancel_link(self, creds: Credentials, link_id: str) -> PaymentLink: ...
    def fetch_payment(self, creds: Credentials, payment_id: str) -> Payment: ...
    def fetch_order(self, creds: Credentials, order_id: str) -> Order: ...
    def list_payments(
        self, creds: Credentials, start: dt.datetime, end: dt.datetime, *, skip: int, count: int
    ) -> list[Payment]: ...
    def create_refund(
        self,
        creds: Credentials,
        payment_id: str,
        amount: Decimal,
        *,
        receipt: str,
        notes: dict[str, str],
    ) -> Refund: ...
    def fetch_refund(self, creds: Credentials, payment_id: str, refund_id: str) -> Refund: ...
    def settlement_items(
        self, creds: Credentials, day: dt.date, *, skip: int, count: int
    ) -> list[SettlementItem]: ...

    # --- connection (OAuth, Technology Partner) -----------------------------------------------
    def authorize_url(self, state: str) -> str: ...
    def exchange_code(self, code: str) -> Tokens: ...
    def refresh(self, refresh_token: str) -> Tokens: ...
    def revoke(self, token: str) -> None: ...
    def create_webhook(
        self, creds: Credentials, account_id: str, url: str, secret: str, events: tuple[str, ...]
    ) -> str: ...
    def update_webhook(
        self,
        creds: Credentials,
        account_id: str,
        webhook_id: str,
        *,
        url: str,
        secret: str,
        events: tuple[str, ...],
    ) -> None: ...
    def delete_webhook(self, creds: Credentials, account_id: str, webhook_id: str) -> None: ...


__all__ = [
    "Credentials",
    "LinkRequest",
    "LinkStatus",
    "Order",
    "Payment",
    "PaymentLink",
    "PaymentMethod",
    "PaymentProvider",
    "PaymentStatus",
    "ProviderError",
    "Refund",
    "RefundStatus",
    "SettlementItem",
    "Tokens",
]
