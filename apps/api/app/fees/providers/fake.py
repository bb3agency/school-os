"""Deterministic offline payment provider for local development and CI (FR-PAY-001; refused in
staging and production by the settings guard and :func:`app.fees.providers.get_provider`).

It behaves like Razorpay closely enough for the service and the tests: key secrets and OAuth
codes starting with ``bad`` are refused (401), links are ``plink_FAKE…``, a test "pays" a link
with :meth:`FakeState.pay` (which creates the order whose ``receipt`` is the link's
``reference_id``, and a captured payment), refunds stay ``pending`` until
:meth:`FakeState.settle_refund`, and :func:`webhook_body` builds a Razorpay-shaped webhook body
from the recorded synthetic fixtures in ``fixtures/`` (no real payer data: contacts, emails and
VPAs in the fixtures are synthetic placeholders and are dropped by the parser anyway). Everything
it was asked to do is kept in :data:`STATE` (per process) so tests can inspect it. No network.
"""

from __future__ import annotations

import copy
import datetime as dt
import itertools
import json
import threading
from dataclasses import dataclass, field, replace
from decimal import Decimal
from pathlib import Path
from typing import Any, Final

from app.fees.providers.base import (
    Credentials,
    LinkRequest,
    Order,
    Payment,
    PaymentLink,
    PaymentMethod,
    ProviderError,
    Refund,
    SettlementItem,
    Tokens,
)
from app.fees.providers.razorpay import to_paise

FIXTURES: Final = Path(__file__).with_name("fixtures")
_ids = itertools.count(1)


def _letters(n: int) -> str:
    out = ""
    for _ in range(8):
        n, rest = divmod(n, 26)
        out = chr(ord("A") + rest) + out
    return out


def _next(prefix: str) -> str:
    """``plink_FAKEAAAAAAAB``: letters only, so ids never look like long numbers."""
    return f"{prefix}FAKE{_letters(next(_ids))}"


def _now() -> dt.datetime:
    return dt.datetime.now(dt.UTC).replace(microsecond=0)


@dataclass
class FakeState:
    """What the fake provider holds and was asked to do (tests read and set it)."""

    links: dict[str, PaymentLink] = field(default_factory=dict)
    link_owner: dict[str, str] = field(default_factory=dict)
    link_requests: dict[str, LinkRequest] = field(default_factory=dict)
    payments: dict[str, Payment] = field(default_factory=dict)
    payment_owner: dict[str, str] = field(default_factory=dict)
    orders: dict[str, Order] = field(default_factory=dict)
    refunds: dict[str, Refund] = field(default_factory=dict)
    refund_receipts: dict[tuple[str, str], str] = field(default_factory=dict)
    settlements: dict[str, list[SettlementItem]] = field(default_factory=dict)
    webhooks: dict[str, dict[str, Any]] = field(default_factory=dict)
    revoked: list[str] = field(default_factory=list)
    calls: list[tuple[str, str]] = field(default_factory=list)
    # Error code to raise on the next call of a method (``method -> ProviderError``).
    fail_next: dict[str, ProviderError] = field(default_factory=dict)
    # Fingerprints whose credentials a test revoked (every call then answers 401).
    dead: set[str] = field(default_factory=set)
    lock: threading.RLock = field(default_factory=threading.RLock)

    def reset(self) -> None:
        with self.lock:
            for value in (
                self.links,
                self.link_owner,
                self.link_requests,
                self.payments,
                self.payment_owner,
                self.orders,
                self.refunds,
                self.refund_receipts,
                self.settlements,
                self.webhooks,
                self.fail_next,
            ):
                value.clear()
            self.revoked.clear()
            self.calls.clear()
            self.dead.clear()

    # --- test helpers -----------------------------------------------------------------------------

    def pay(
        self,
        link_id: str,
        *,
        amount: Decimal | None = None,
        method: PaymentMethod = "upi",
        captured: bool = True,
        currency: str = "INR",
        at: dt.datetime | None = None,
    ) -> Payment:
        """A parent pays the link: an order (``receipt`` = the link's ``reference_id``) and a
        payment (captured unless ``captured=False``); the link becomes ``paid``."""
        with self.lock:
            link = self.links[link_id]
            order = Order(
                id=link.order_id or _next("order_"),
                receipt=link.reference_id,
                amount=link.amount,
                currency=link.currency,
                status="paid" if captured else "attempted",
            )
            self.orders[order.id] = order
            payment = Payment(
                id=_next("pay_"),
                status="captured" if captured else "authorized",
                amount=link.amount if amount is None else amount,
                currency=currency,
                order_id=order.id,
                method=method,
                captured=captured,
                amount_refunded=Decimal("0.00"),
                fee=Decimal("2.00"),
                tax=Decimal("0.36"),
                created_at=at or _now(),
            )
            self.payments[payment.id] = payment
            self.payment_owner[payment.id] = self.link_owner[link_id]
            if captured:
                self.links[link_id] = replace(
                    link, status="paid", amount_paid=payment.amount, order_id=order.id
                )
            else:
                self.links[link_id] = replace(link, order_id=order.id)
            return payment

    def set_payment(self, payment_id: str, **changes: Any) -> Payment:
        with self.lock:
            self.payments[payment_id] = replace(self.payments[payment_id], **changes)
            return self.payments[payment_id]

    def set_link(self, link_id: str, **changes: Any) -> PaymentLink:
        with self.lock:
            self.links[link_id] = replace(self.links[link_id], **changes)
            return self.links[link_id]

    def settle_refund(self, refund_id: str, status: str = "processed") -> Refund:
        with self.lock:
            refund = replace(self.refunds[refund_id], status=status)  # type: ignore[arg-type]
            self.refunds[refund_id] = refund
            if status == "processed":
                payment = self.payments[refund.payment_id]
                self.payments[payment.id] = replace(
                    payment, amount_refunded=payment.amount_refunded + refund.amount
                )
            return refund

    def settle(self, payment_id: str, *, day: dt.date, settlement_id: str | None = None) -> None:
        with self.lock:
            payment = self.payments[payment_id]
            owner = self.payment_owner[payment_id]
            item = SettlementItem(
                entity_id=payment.id,
                type="payment",
                payment_id=None,
                settlement_id=settlement_id or _next("setl_"),
                settled=True,
                settled_at=dt.datetime.combine(day, dt.time(12), tzinfo=dt.UTC),
                amount=payment.amount,
            )
            self.settlements.setdefault(f"{owner}|{day.isoformat()}", []).append(item)

    def link_for_reference(self, reference_id: str) -> PaymentLink | None:
        with self.lock:
            return next((v for v in self.links.values() if v.reference_id == reference_id), None)


STATE = FakeState()


class FakePaymentProvider:
    """``PaymentProvider`` that never leaves the process."""

    name = "fake"

    def __init__(self, state: FakeState | None = None) -> None:
        self.state = state or STATE

    def _enter(self, method: str, creds: Credentials | None = None) -> None:
        with self.state.lock:
            self.state.calls.append((method, creds.fingerprint if creds else ""))
            failure = self.state.fail_next.pop(method, None)
        if failure is not None:
            raise failure
        if creds is not None:
            secret = creds.key_secret if creds.key_secret is not None else creds.access_token
            if not secret or secret.startswith("bad") or creds.fingerprint in self.state.dead:
                raise ProviderError("auth_failed", status=401)

    def _owned(self, creds: Credentials, owner: dict[str, str], key: str) -> None:
        if owner.get(key) != creds.fingerprint:
            raise ProviderError("not_found", status=404)

    # --- the school's account ---------------------------------------------------------------------

    def check_credentials(self, creds: Credentials) -> None:
        self._enter("check_credentials", creds)

    def create_link(self, creds: Credentials, request: LinkRequest) -> PaymentLink:
        self._enter("create_link", creds)
        to_paise(request.amount)  # the same exactness check as Razorpay's client
        if request.amount < Decimal("1.00"):
            raise ProviderError("invalid_request", status=400)
        with self.state.lock:
            if any(v.reference_id == request.reference_id for v in self.state.links.values()):
                raise ProviderError("invalid_request", status=400)
            link = PaymentLink(
                id=_next("plink_"),
                status="created",
                amount=request.amount,
                amount_paid=Decimal("0.00"),
                currency="INR",
                reference_id=request.reference_id,
                short_url=f"https://rzp.example.test/fake/{_letters(next(_ids))}",
                order_id=None,
                expire_by=request.expire_by,
            )
            self.state.links[link.id] = link
            self.state.link_owner[link.id] = creds.fingerprint
            self.state.link_requests[link.id] = request
            return link

    def fetch_link(self, creds: Credentials, link_id: str) -> PaymentLink:
        self._enter("fetch_link", creds)
        with self.state.lock:
            self._owned(creds, self.state.link_owner, link_id)
            return self.state.links[link_id]

    def cancel_link(self, creds: Credentials, link_id: str) -> PaymentLink:
        self._enter("cancel_link", creds)
        with self.state.lock:
            self._owned(creds, self.state.link_owner, link_id)
            link = self.state.links[link_id]
            if link.status in ("paid", "expired"):
                raise ProviderError("invalid_request", status=400)
            link = replace(link, status="cancelled")
            self.state.links[link_id] = link
            return link

    def fetch_payment(self, creds: Credentials, payment_id: str) -> Payment:
        self._enter("fetch_payment", creds)
        with self.state.lock:
            self._owned(creds, self.state.payment_owner, payment_id)
            return self.state.payments[payment_id]

    def fetch_order(self, creds: Credentials, order_id: str) -> Order:
        self._enter("fetch_order", creds)
        with self.state.lock:
            order = self.state.orders.get(order_id)
            if order is None:
                raise ProviderError("not_found", status=404)
            return order

    def list_payments(
        self, creds: Credentials, start: dt.datetime, end: dt.datetime, *, skip: int, count: int
    ) -> list[Payment]:
        self._enter("list_payments", creds)
        with self.state.lock:
            rows = sorted(
                (
                    p
                    for p in self.state.payments.values()
                    if self.state.payment_owner.get(p.id) == creds.fingerprint
                    and start <= p.created_at <= end
                ),
                key=lambda p: (p.created_at, p.id),
            )
            return rows[skip : skip + count]

    def create_refund(
        self,
        creds: Credentials,
        payment_id: str,
        amount: Decimal,
        *,
        receipt: str,
        notes: dict[str, str],
    ) -> Refund:
        self._enter("create_refund", creds)
        to_paise(amount)
        with self.state.lock:
            self._owned(creds, self.state.payment_owner, payment_id)
            payment = self.state.payments[payment_id]
            if payment.status not in {"captured", "refunded"}:
                raise ProviderError("invalid_request", status=400)
            known = self.state.refund_receipts.get((payment_id, receipt))
            if known is not None:  # the receipt is the idempotency reference
                return self.state.refunds[known]
            refund = Refund(
                id=_next("rfnd_"),
                payment_id=payment_id,
                status="pending",
                amount=amount,
                currency="INR",
                receipt=receipt,
                created_at=_now(),
            )
            self.state.refunds[refund.id] = refund
            self.state.refund_receipts[(payment_id, receipt)] = refund.id
            return refund

    def fetch_refund(self, creds: Credentials, payment_id: str, refund_id: str) -> Refund:
        self._enter("fetch_refund", creds)
        with self.state.lock:
            self._owned(creds, self.state.payment_owner, payment_id)
            refund = self.state.refunds.get(refund_id)
            if refund is None or refund.payment_id != payment_id:
                raise ProviderError("not_found", status=404)
            return refund

    def settlement_items(
        self, creds: Credentials, day: dt.date, *, skip: int, count: int
    ) -> list[SettlementItem]:
        self._enter("settlement_items", creds)
        with self.state.lock:
            rows = self.state.settlements.get(f"{creds.fingerprint}|{day.isoformat()}", [])
            return list(rows[skip : skip + count])

    # --- connection (OAuth) -----------------------------------------------------------------------

    def authorize_url(self, state: str) -> str:
        self._enter("authorize_url")
        return f"https://auth.razorpay.example.test/authorize?state={state}"

    def exchange_code(self, code: str) -> Tokens:
        self._enter("exchange_code")
        if not code or code.startswith("bad"):
            raise ProviderError("auth_failed", status=401)
        suffix = code[-8:].ljust(8, "0")
        return Tokens(
            access_token=f"fake-access-{suffix}",
            refresh_token=f"fake-refresh-{suffix}-{next(_ids)}",
            expires_in=90 * 86400,
            account_id=f"acc_FAKE{''.join(ch for ch in suffix if ch.isalnum())[:8]}",
            live=False,
        )

    def refresh(self, refresh_token: str) -> Tokens:
        self._enter("refresh")
        if (
            not refresh_token
            or refresh_token.startswith("bad")
            or refresh_token in self.state.revoked
        ):
            raise ProviderError("auth_failed", status=401)
        with self.state.lock:
            self.state.revoked.append(refresh_token)  # single use
        suffix = refresh_token.split("-")[2] if refresh_token.count("-") >= 2 else "00000000"
        return Tokens(
            access_token=f"fake-access-{suffix}-{next(_ids)}",
            refresh_token=f"fake-refresh-{suffix}-{next(_ids)}",
            expires_in=90 * 86400,
            account_id=f"acc_FAKE{''.join(ch for ch in suffix if ch.isalnum())[:8]}",
            live=False,
        )

    def revoke(self, token: str) -> None:
        self._enter("revoke")
        with self.state.lock:
            self.state.revoked.append(token)

    def create_webhook(
        self, creds: Credentials, account_id: str, url: str, secret: str, events: tuple[str, ...]
    ) -> str:
        self._enter("create_webhook", creds)
        webhook_id = _next("wh")
        with self.state.lock:
            self.state.webhooks[webhook_id] = {
                "account_id": account_id,
                "url": url,
                "secret": secret,
                "events": events,
            }
        return webhook_id

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
        self._enter("update_webhook", creds)
        with self.state.lock:
            if webhook_id not in self.state.webhooks:
                raise ProviderError("not_found", status=404)
            self.state.webhooks[webhook_id].update({"url": url, "secret": secret, "events": events})

    def delete_webhook(self, creds: Credentials, account_id: str, webhook_id: str) -> None:
        self._enter("delete_webhook", creds)
        with self.state.lock:
            self.state.webhooks.pop(webhook_id, None)


# --- recorded synthetic webhook fixtures --------------------------------------------------------


def fixture(event: str) -> dict[str, Any]:
    """The recorded synthetic body for ``event`` (``fixtures/<event>.json``)."""
    with (FIXTURES / f"{event}.json").open(encoding="utf-8") as fh:
        loaded: dict[str, Any] = json.load(fh)
    return loaded


def webhook_body(
    event: str,
    *,
    link: PaymentLink | None = None,
    payment: Payment | None = None,
    refund: Refund | None = None,
    account_id: str | None = None,
    created_at: dt.datetime | None = None,
) -> bytes:
    """A Razorpay-shaped webhook body for ``event`` filled from the given objects (amounts in
    paise, as Razorpay sends them)."""
    body = copy.deepcopy(fixture(event))
    body["event"] = event
    body["created_at"] = int((created_at or _now()).timestamp())
    if account_id is not None:
        body["account_id"] = account_id
    payload = body.setdefault("payload", {})
    if link is not None and "payment_link" in payload:
        entity = payload["payment_link"]["entity"]
        entity.update(
            {
                "id": link.id,
                "status": link.status,
                "amount": to_paise(link.amount),
                "amount_paid": to_paise(link.amount_paid),
                "reference_id": link.reference_id,
                "order_id": link.order_id,
                "short_url": link.short_url,
            }
        )
    if payment is not None and "payment" in payload:
        entity = payload["payment"]["entity"]
        entity.update(
            {
                "id": payment.id,
                "status": payment.status,
                "amount": to_paise(payment.amount),
                "currency": payment.currency,
                "order_id": payment.order_id,
                "method": payment.method,
                "captured": payment.captured,
            }
        )
    if payment is not None and "order" in payload:
        entity = payload["order"]["entity"]
        entity.update(
            {
                "id": payment.order_id,
                "receipt": link.reference_id if link is not None else entity.get("receipt"),
            }
        )
    if refund is not None and "refund" in payload:
        entity = payload["refund"]["entity"]
        entity.update(
            {
                "id": refund.id,
                "payment_id": refund.payment_id,
                "status": refund.status,
                "amount": to_paise(refund.amount),
                "receipt": refund.receipt,
            }
        )
    return json.dumps(body, separators=(",", ":")).encode()


__all__ = ["STATE", "FakePaymentProvider", "FakeState", "fixture", "webhook_body"]
