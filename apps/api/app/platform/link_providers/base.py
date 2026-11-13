"""The provider interface for payment links of SchoolOS's own invoices (FA-13; ADR-0016;
FR-PAY-017, FR-PAY-025).

SchoolOS is the merchant: a provider talks to **SchoolOS's own** account (key from
``SOS_PLATFORM_RAZORPAY_*``), never a school's (that is ``app.fees.providers``, the school as
merchant, ADR-0042). Implementations: :mod:`.razorpay` (the REST API over httpx, no SDK) and
:mod:`.fake` (deterministic and offline; local and CI only, refused in staging/prod).

Amounts cross this interface as exact ``Decimal`` rupees; paise exist only inside
:mod:`.razorpay`. What a provider returns is reduced to the fields SchoolOS needs: no payer
contact, email, VPA or card details. Errors are :class:`LinkProviderError` with a short code
(``auth_failed``, ``not_found``, ``invalid_request``, ``rate_limited``, ``unavailable``,
``bad_response``, ``not_configured``, ``endpoint_not_allowed``); ``transient`` says whether a
retry may help. Nothing here logs a key, a body or a payer detail.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal, Protocol, runtime_checkable

LinkStatus = Literal["created", "partially_paid", "paid", "expired", "cancelled"]
PaymentStatus = Literal["created", "authorized", "captured", "refunded", "failed"]
# The method stored on platform.payments: UPI as itself, every other online method as "online".
StoredMethod = Literal["upi", "online"]


class LinkProviderError(Exception):
    """A provider call failed (reason code only; never a body, key or token)."""

    def __init__(self, code: str, *, status: int | None = None, transient: bool = False) -> None:
        super().__init__(code if status is None else f"{code}:{status}")
        self.code = code
        self.status = status
        self.transient = transient


@dataclass(frozen=True, slots=True)
class InvoiceLinkRequest:
    """A link for one invoice's balance: no customer object, no provider notifications, no
    partial payments (FR-PAY-018)."""

    amount: Decimal
    reference_id: str
    description: str
    expire_by: dt.datetime
    notes: dict[str, str]


@dataclass(frozen=True, slots=True)
class ProviderLink:
    id: str
    status: LinkStatus
    amount: Decimal
    amount_paid: Decimal
    currency: str
    short_url: str
    reference_id: str | None
    order_id: str | None
    payment_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ProviderPayment:
    """A payment as the provider reports it on a re-fetch (FR-PAY-020), minimised."""

    id: str
    status: PaymentStatus
    amount: Decimal
    currency: str
    order_id: str | None
    method: StoredMethod
    captured: bool
    created_at: dt.datetime


@runtime_checkable
class InvoiceLinkProvider(Protocol):
    """Every call the control plane makes to a payment provider (FR-PAY-025): create, fetch and
    cancel a payment link, fetch a payment. No refunds, payouts, mandates or subscriptions."""

    name: str

    def create_link(self, request: InvoiceLinkRequest) -> ProviderLink: ...
    def fetch_link(self, link_id: str) -> ProviderLink: ...
    def cancel_link(self, link_id: str) -> ProviderLink: ...
    def fetch_payment(self, payment_id: str) -> ProviderPayment: ...


__all__ = [
    "InvoiceLinkProvider",
    "InvoiceLinkRequest",
    "LinkProviderError",
    "LinkStatus",
    "PaymentStatus",
    "ProviderLink",
    "ProviderPayment",
    "StoredMethod",
]
