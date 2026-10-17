"""Online refunds (US-2106 AC2; FR-FEE-013, FR-PAY-011; D17): maker-checker before anything is
sent, the provider asked once (idempotent reference), the voucher and ledger entry only on the
provider's confirmation, failures back to the requester; and the rules tying refund modes to
receipt modes. Synthetic data, the fake provider."""

from __future__ import annotations

import sys
import uuid
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import Engine

from app.core.errors import Conflict, Forbidden, ValidationFailed
from app.fees import online
from app.fees import service as fees
from app.fees.providers.fake import STATE, webhook_body
from app.fees.schemas import DecisionIn, RefundCreate

pytestmark = pytest.mark.db

F: Any = sys.modules["sos_test_fees_support"]
P: Any = sys.modules["sos_test_fees_online_support"]
REASON = "Synthetic reason: parent paid twice"


def _online_receipt(school: Any, api: Any) -> tuple[Any, Any, Any]:
    conn = P.connect(school)
    account_id = F.account(school)
    intent = P.link(school, account_id, [1])
    payment, _ = P.pay(school, api, conn, intent)
    return conn, payment, intent


def _receipt_id(admin: Engine, payment_id: str) -> uuid.UUID:
    value = P.scalar(
        admin, "SELECT id FROM fin.receipts WHERE provider_payment_id = :p", p=payment_id
    )
    return uuid.UUID(str(value))


def _request(
    school: Any, receipt_id: uuid.UUID, amount: str = "500.00", mode: str = "original_method"
) -> Any:
    return F.call(
        school,
        "accountant",
        fees.request_refund,
        receipt_id,
        RefundCreate(amount=Decimal(amount), mode=mode, reason=REASON),
    )


def test_FR_FEE_013_online_receipt_refunds_only_to_original_method(
    school: Any, api: Any, admin_engine: Engine
) -> None:
    _, payment, _ = _online_receipt(school, api)
    receipt_id = _receipt_id(admin_engine, payment.id)
    with pytest.raises(ValidationFailed):
        _request(school, receipt_id, mode="cash")
    cash = F.issue(school, F.account(school), "100.00")
    with pytest.raises(ValidationFailed):
        _request(school, cash.id, "50.00")


def test_FR_PAY_011_maker_checker_then_provider_then_voucher(
    school: Any, api: Any, admin_engine: Engine
) -> None:
    conn, payment, _ = _online_receipt(school, api)
    receipt_id = _receipt_id(admin_engine, payment.id)
    refund = _request(school, receipt_id)
    assert refund.status == "pending"
    # The requester cannot approve their own refund (D17).
    with pytest.raises(Forbidden):
        F.call(
            school,
            "accountant",
            fees.approve_refund,
            refund.id,
            DecisionIn(),
            expected_version=None,
        )
    approved = F.call(
        school,
        "accountant2",
        fees.approve_refund,
        refund.id,
        DecisionIn(),
        expected_version=None,
        person=school.people["accountant2"],
        as_ctx=F.ctx(school, school.people["accountant2"], "accountant"),
    )
    assert approved.status == "approved"
    assert approved.voucher_number is None
    queued = P.scalar(
        admin_engine,
        "SELECT count(*) FROM ops.outbox WHERE tenant_id = :t AND event_type = 'fees.refund.send' "
        "AND payload->>'refund_id' = :r",
        t=school.tenant_id,
        r=str(refund.id),
    )
    assert queued == 1
    assert online.send_refund(school.tenant_id, refund.id) == "processing"
    # Sending again (a retried task) never asks twice.
    assert online.send_refund(school.tenant_id, refund.id) == "skipped"
    provider_id = P.scalar(
        admin_engine, "SELECT provider_refund_id FROM fin.refunds WHERE id = :i", i=refund.id
    )
    assert provider_id is not None
    assert STATE.refunds[provider_id].receipt == uuid.UUID(str(refund.id)).hex
    assert STATE.refunds[provider_id].amount == Decimal("500.00")
    no_voucher = P.scalar(
        admin_engine, "SELECT count(*) FROM fin.ledger_entries WHERE refund_id = :i", i=refund.id
    )
    assert no_voucher == 0
    processed = STATE.settle_refund(provider_id)
    body = webhook_body("refund.processed", refund=processed, payment=STATE.payments[payment.id])
    assert P.deliver(api, conn, body).status_code == 200
    P.process(school)
    row = P.rows(
        admin_engine,
        "SELECT status, voucher_number FROM fin.refunds WHERE id = :i",
        i=refund.id,
    )[0]
    assert row["status"] == "processed"
    assert row["voucher_number"].startswith("RFD/")
    entries = P.scalar(
        admin_engine,
        "SELECT sum(amount) FROM fin.ledger_entries WHERE refund_id = :i AND entry_kind = 'refund'",
        i=refund.id,
    )
    assert entries == Decimal("500.00")
    # The same event again changes nothing.
    assert P.deliver(api, conn, body).status_code == 200
    P.process(school)
    assert (
        P.scalar(
            admin_engine,
            "SELECT count(*) FROM fin.ledger_entries WHERE refund_id = :i",
            i=refund.id,
        )
        >= 1
    )
    assert (
        P.scalar(
            admin_engine,
            "SELECT count(DISTINCT voucher_number) FROM fin.refunds WHERE id = :i",
            i=refund.id,
        )
        == 1
    )


def test_FR_PAY_011_failed_refund_tells_the_requester(
    school: Any, api: Any, admin_engine: Engine
) -> None:
    conn, payment, _ = _online_receipt(school, api)
    receipt_id = _receipt_id(admin_engine, payment.id)
    refund = _request(school, receipt_id, "100.00")
    F.call(
        school,
        "accountant2",
        fees.approve_refund,
        refund.id,
        DecisionIn(),
        expected_version=None,
        person=school.people["accountant2"],
        as_ctx=F.ctx(school, school.people["accountant2"], "accountant"),
    )
    online.send_refund(school.tenant_id, refund.id)
    provider_id = P.scalar(
        admin_engine, "SELECT provider_refund_id FROM fin.refunds WHERE id = :i", i=refund.id
    )
    failed = STATE.settle_refund(provider_id, "failed")
    P.deliver(
        api, conn, webhook_body("refund.failed", refund=failed, payment=STATE.payments[payment.id])
    )
    P.process(school)
    assert (
        P.scalar(admin_engine, "SELECT status FROM fin.refunds WHERE id = :i", i=refund.id)
        == "failed"
    )
    notes = P.scalar(
        admin_engine,
        "SELECT count(*) FROM ops.notifications WHERE template_key = 'payments.refund_failed' "
        "AND resource_id = :r",
        r=refund.id,
    )
    assert notes == 1
    assert [
        i["kind"] for i in P.items(admin_engine, school.tenant_id, refund_id=str(refund.id))
    ] == ["refund_failed"]


def test_FR_PAY_011_refund_needs_a_connection(school: Any, api: Any, admin_engine: Engine) -> None:
    _, payment, _ = _online_receipt(school, api)
    receipt_id = _receipt_id(admin_engine, payment.id)
    F.call(school, "owner", online.disconnect)
    with pytest.raises(Conflict) as caught:
        _request(school, receipt_id)
    assert caught.value.code == "payments_not_connected"
