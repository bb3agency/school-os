"""Deterministic offline payment links for SchoolOS invoices (FA-13; local and CI only).

The settings refuse ``SOS_PLATFORM_PAYMENTS_PROVIDER_MODE=fake`` in staging and production and
:func:`app.platform.link_providers.get_provider` refuses it there too. State lives in this process
(links and payments by id), so a test, or a developer with ``make dev``, can "pay" a link with
:meth:`FakeInvoiceLinks.pay` and then post the matching webhook (:func:`webhook_body`). Short URLs
use ``rzp.example.test`` (``.test`` never resolves on the internet). No network, no secrets.
"""

from __future__ import annotations

import datetime as dt
import itertools
import json
import secrets
import threading
from dataclasses import dataclass, replace
from decimal import Decimal

from app.platform.link_providers.base import (
    InvoiceLinkRequest,
    LinkProviderError,
    PaymentStatus,
    ProviderLink,
    ProviderPayment,
    StoredMethod,
)

_lock = threading.Lock()
_links: dict[str, ProviderLink] = {}
_payments: dict[str, ProviderPayment] = {}
_counter = itertools.count(1)


@dataclass
class _Failure:
    code: str
    transient: bool


_next_failure: list[_Failure] = []


def _letters(n: int) -> str:
    return "".join(
        secrets.choice("abcdefghijkmnopqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ") for _ in range(n)
    )


def _new_id(prefix: str) -> str:
    return f"{prefix}{_letters(10)}{next(_counter)}"


class FakeInvoiceLinks:
    """``InvoiceLinkProvider`` without a network (see the module docstring)."""

    name = "fake"

    def _maybe_fail(self) -> None:
        with _lock:
            failure = _next_failure.pop(0) if _next_failure else None
        if failure is not None:
            raise LinkProviderError(failure.code, transient=failure.transient)

    def create_link(self, request: InvoiceLinkRequest) -> ProviderLink:
        self._maybe_fail()
        if request.amount <= 0 or request.amount != request.amount.quantize(Decimal("0.01")):
            raise LinkProviderError("invalid_request", status=400)
        link_id = _new_id("plink_")
        link = ProviderLink(
            id=link_id,
            status="created",
            amount=request.amount,
            amount_paid=Decimal("0.00"),
            currency="INR",
            short_url=f"https://rzp.example.test/schoolos/{link_id}",
            reference_id=request.reference_id,
            order_id=_new_id("order_"),
        )
        with _lock:
            _links[link_id] = link
        return link

    def fetch_link(self, link_id: str) -> ProviderLink:
        self._maybe_fail()
        with _lock:
            link = _links.get(link_id)
        if link is None:
            raise LinkProviderError("not_found", status=404)
        return link

    def cancel_link(self, link_id: str) -> ProviderLink:
        self._maybe_fail()
        with _lock:
            link = _links.get(link_id)
            if link is None:
                raise LinkProviderError("not_found", status=404)
            if link.status != "created":
                raise LinkProviderError("invalid_request", status=400)
            link = replace(link, status="cancelled")
            _links[link_id] = link
        return link

    def fetch_payment(self, payment_id: str) -> ProviderPayment:
        self._maybe_fail()
        with _lock:
            payment = _payments.get(payment_id)
        if payment is None:
            raise LinkProviderError("not_found", status=404)
        return payment

    # --- test and local-dev helpers ---------------------------------------------------------

    @staticmethod
    def pay(
        link_id: str,
        *,
        method: StoredMethod = "upi",
        status: PaymentStatus = "captured",
        amount: Decimal | None = None,
        currency: str = "INR",
    ) -> str:
        """The payer pays the link on the hosted page; returns the payment id."""
        with _lock:
            link = _links[link_id]
            payment = ProviderPayment(
                id=_new_id("pay_"),
                status=status,
                amount=link.amount if amount is None else amount,
                currency=currency,
                order_id=link.order_id,
                method=method,
                captured=status == "captured",
                created_at=dt.datetime.now(dt.UTC),
            )
            _payments[payment.id] = payment
            if status == "captured":
                _links[link_id] = replace(
                    link,
                    status="paid",
                    amount_paid=payment.amount,
                    payment_ids=(*link.payment_ids, payment.id),
                )
        return payment.id

    @staticmethod
    def fail_next(code: str = "unavailable", *, transient: bool = True) -> None:
        """Make the next provider call fail with ``code``."""
        with _lock:
            _next_failure.append(_Failure(code, transient))

    @staticmethod
    def reset() -> None:
        with _lock:
            _links.clear()
            _payments.clear()
            _next_failure.clear()


def webhook_body(link_id: str, payment_id: str, *, event: str = "payment_link.paid") -> bytes:
    """A minimal Razorpay-shaped ``payment_link.paid`` body (ids only; amounts are re-fetched)."""
    payload = {
        "entity": "event",
        "event": event,
        "contains": ["payment_link", "order", "payment"],
        "payload": {
            "payment_link": {"entity": {"id": link_id, "status": "paid"}},
            "payment": {"entity": {"id": payment_id, "status": "captured"}},
        },
    }
    return json.dumps(payload, separators=(",", ":")).encode()


__all__ = ["FakeInvoiceLinks", "webhook_body"]
