"""The Razorpay webhook and its inbox (docs/14 R2 test plan "Webhooks"; FR-PAY-007..009,
FR-PAY-014; ADR-0042 decision 7, ADR-0047; SEC-034): valid, invalid, missing, wrong-school and
rotated-secret signatures, unknown routes, replayed event ids, out-of-order events, re-fetch
mismatches (an exception, never a receipt), payload size, minimised encrypted storage, and the
receipt itself (same gap-free series, mode ``razorpay``). Synthetic recorded fixtures through the
fake provider."""

from __future__ import annotations

import json
import sys
import uuid
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import Engine, text

from app.core.db import tenant_session
from app.fees import _common as common
from app.fees import online
from app.fees.providers.fake import STATE, webhook_body
from app.fees.providers.razorpay import signature
from app.students import crypto as dek

pytestmark = pytest.mark.db

F: Any = sys.modules["sos_test_fees_support"]
P: Any = sys.modules["sos_test_fees_online_support"]


def _events(admin: Engine, tenant_id: uuid.UUID) -> int:
    return int(
        P.scalar(
            admin, "SELECT count(*) FROM fin.provider_events WHERE tenant_id = :t", t=tenant_id
        )
    )


def _paid_body(intent: Any, **kw: Any) -> tuple[bytes, Any]:
    plink = P.fake_link(intent)
    payment = STATE.pay(plink.id, **kw)
    return webhook_body("payment_link.paid", link=STATE.links[plink.id], payment=payment), payment


def test_FR_PAY_007_valid_signature_stores_then_receipt(
    school: Any, api: Any, admin_engine: Engine
) -> None:
    conn = P.connect(school)
    account_id = F.account(school)
    intent = P.link(school, account_id, [1])
    before = _events(admin_engine, school.tenant_id)
    body, payment = _paid_body(intent)
    res = P.deliver(api, conn, body)
    assert res.status_code == 200, res.text
    assert _events(admin_engine, school.tenant_id) == before + 1
    P.process(school)
    rows = P.rows(
        admin_engine,
        "SELECT receipt_number, mode, amount, collected_by, payment_intent_id, status "
        "FROM fin.receipts WHERE provider_payment_id = :p",
        p=payment.id,
    )
    assert len(rows) == 1
    assert rows[0]["mode"] == "razorpay"
    assert rows[0]["amount"] == intent.amount
    assert rows[0]["collected_by"] is None
    assert rows[0]["payment_intent_id"] == intent.id
    assert rows[0]["receipt_number"].startswith("RCT/")
    status = P.scalar(
        admin_engine, "SELECT status FROM fin.payment_intents WHERE id = :i", i=intent.id
    )
    assert status == "paid"
    # Audit in the same transaction as the receipt, by the system, amounts and ids only.
    events = F.W.audit_events(admin_engine, school.tenant_id, "fee.receipt.issued")
    mine = [e for e in events if e["summary"].get("intent_id") == str(intent.id)]
    assert len(mine) == 1
    assert mine[0]["summary"]["mode"] == "razorpay"


def test_SEC_034_invalid_missing_and_malformed_signatures_are_401(
    school: Any, api: Any, admin_engine: Engine
) -> None:
    conn = P.connect(school)
    intent = P.link(school, F.account(school), [1])
    body, _ = _paid_body(intent)
    before = _events(admin_engine, school.tenant_id)
    assert P.deliver(api, conn, body, secret="not-the-school-secret").status_code == 401
    assert P.deliver(api, conn, body, sig="").status_code == 401
    assert P.deliver(api, conn, body, sig="zz" * 32).status_code == 401
    tampered = body.replace(b'"amount":', b'"amount" :', 1)
    good = signature(conn.secret.encode(), body)
    assert P.deliver(api, conn, tampered, sig=good).status_code == 401
    assert P.deliver(api, conn, body, event="bad event id!").status_code == 401
    assert _events(admin_engine, school.tenant_id) == before


def test_SEC_034_unknown_route_is_404_before_any_signature_check(school: Any, api: Any) -> None:
    conn = P.connect(school)
    body = webhook_body("payment.captured")
    res = P.deliver(api, conn, body, route="A" * 24)
    assert res.status_code == 404
    assert P.deliver(api, conn, body, route="short").status_code == 404


def test_SEC_034_wrong_school_signature_is_refused(
    school: Any, other_school: Any, api: Any, admin_engine: Engine
) -> None:
    mine = P.connect(school)
    theirs = P.connect(other_school)
    intent = P.link(school, F.account(school), [1])
    body, _ = _paid_body(intent)
    before = _events(admin_engine, school.tenant_id)
    # Signed with the other school's secret, sent to this school's route.
    assert P.deliver(api, mine, body, secret=theirs.secret).status_code == 401
    # This school's valid delivery sent to the other school's route.
    assert P.deliver(api, theirs, body, secret=mine.secret).status_code == 401
    assert _events(admin_engine, school.tenant_id) == before


def test_FR_PAY_014_rotated_secret_old_one_verifies_for_seven_days_only(
    school: Any, api: Any, admin_engine: Engine
) -> None:
    conn = P.connect(school)
    setup = F.call(school, "owner", online.rotate_webhook_secret)
    assert setup.secret != conn.secret
    assert setup.previous_valid_until is not None
    body = webhook_body("payment.failed")
    assert P.deliver(api, conn, body).status_code == 200  # old secret: a retried event
    assert P.deliver(api, conn, body, secret=setup.secret).status_code == 200
    P.age_previous_secret(admin_engine, school.tenant_id)
    assert P.deliver(api, conn, body).status_code == 401
    assert P.deliver(api, conn, body, secret=setup.secret).status_code == 200


def test_FR_PAY_007_replayed_event_id_is_stored_once(
    school: Any, api: Any, admin_engine: Engine
) -> None:
    conn = P.connect(school)
    intent = P.link(school, F.account(school), [1])
    body, payment = _paid_body(intent)
    event = P.event_id()
    before = _events(admin_engine, school.tenant_id)
    for _ in range(3):
        assert P.deliver(api, conn, body, event=event).status_code == 200
    assert _events(admin_engine, school.tenant_id) == before + 1
    P.process(school)
    P.process(school)
    assert P.receipts_for_payment(admin_engine, school.tenant_id, payment.id) == 1


def test_FR_PAY_007_payload_over_256_kb_is_413(school: Any, api: Any) -> None:
    conn = P.connect(school)
    payload = json.loads(webhook_body("payment.failed"))
    payload["padding"] = "a" * (256 * 1024)
    body = json.dumps(payload).encode()
    assert P.deliver(api, conn, body).status_code == 413


def test_PRV_024_inbox_payload_is_encrypted_and_minimised(
    school: Any, api: Any, admin_engine: Engine
) -> None:
    conn = P.connect(school)
    intent = P.link(school, F.account(school), [1])
    body, payment = _paid_body(intent)
    event = P.event_id()
    assert P.deliver(api, conn, body, event=event).status_code == 200
    raw = P.scalar(
        admin_engine,
        "SELECT payload_ciphertext FROM fin.provider_events WHERE event_id = :e",
        e=event,
    )
    assert b"synthetic.payer" not in bytes(raw)
    assert payment.id.encode() not in bytes(raw)
    with tenant_session(school.tenant_id) as s:
        row = s.execute(
            text("SELECT id, payload_ciphertext FROM fin.provider_events WHERE event_id = :e"),
            {"e": event},
        ).one()
        plain = dek.decrypt_value(
            s,
            bytes(row.payload_ciphertext),
            table="fin.provider_events",
            column="payload_ciphertext",
            row_id=row.id,
        )
    for leaked in ("vpa", "email", "contact", "synthetic.payer"):
        assert leaked not in plain
    assert payment.id in plain


def test_FR_PAY_009_out_of_order_events_give_one_receipt(
    school: Any, api: Any, admin_engine: Engine
) -> None:
    conn = P.connect(school)
    intent = P.link(school, F.account(school), [1])
    plink = P.fake_link(intent)
    payment = STATE.pay(plink.id)
    # captured first, then a stale "failed", then the link's paid event, then a late "expired".
    P.deliver(api, conn, webhook_body("payment.captured", payment=payment))
    failed = STATE.payments[payment.id]
    P.deliver(api, conn, webhook_body("payment.failed", payment=failed))
    P.deliver(
        api, conn, webhook_body("payment_link.paid", link=STATE.links[plink.id], payment=payment)
    )
    P.deliver(api, conn, webhook_body("payment_link.expired", link=STATE.links[plink.id]))
    P.process(school)
    assert P.receipts_for_payment(admin_engine, school.tenant_id, payment.id) == 1
    status = P.scalar(
        admin_engine, "SELECT status FROM fin.payment_intents WHERE id = :i", i=intent.id
    )
    assert status == "paid"


def test_FR_PAY_008_refetch_amount_mismatch_is_an_exception_never_a_receipt(
    school: Any, api: Any, admin_engine: Engine
) -> None:
    conn = P.connect(school)
    intent = P.link(school, F.account(school), [1])
    body, payment = _paid_body(intent)
    # The webhook says one thing; the provider (re-fetched) says another amount.
    STATE.set_payment(payment.id, amount=Decimal("1.00"))
    P.deliver(api, conn, body)
    P.process(school)
    assert P.receipts_for_payment(admin_engine, school.tenant_id, payment.id) == 0
    found = P.items(admin_engine, school.tenant_id, provider_payment_id=payment.id)
    assert [(i["kind"], i["reason_code"]) for i in found] == [("amount_mismatch", "amount")]


def test_FR_PAY_008_refetch_currency_and_status_are_checked(
    school: Any, api: Any, admin_engine: Engine
) -> None:
    conn = P.connect(school)
    intent = P.link(school, F.account(school), [1])
    body, payment = _paid_body(intent)
    STATE.set_payment(payment.id, currency="USD")
    P.deliver(api, conn, body)
    P.process(school)
    assert P.receipts_for_payment(admin_engine, school.tenant_id, payment.id) == 0
    assert (
        P.items(admin_engine, school.tenant_id, provider_payment_id=payment.id)[0]["reason_code"]
        == "currency"
    )
    # A payment the webhook calls captured but the provider says is only authorized.
    intent2 = P.link(school, F.account(school), [1])
    body2, payment2 = _paid_body(intent2)
    STATE.set_payment(payment2.id, status="authorized", captured=False)
    P.deliver(api, conn, body2)
    P.process(school)
    assert P.receipts_for_payment(admin_engine, school.tenant_id, payment2.id) == 0


def test_FR_PAY_008_forged_payment_unknown_to_the_provider_issues_nothing(
    school: Any, api: Any, admin_engine: Engine
) -> None:
    """Even a correctly signed event is only a hint: a payment the provider does not know (or
    that belongs to another account) never becomes a receipt."""
    conn = P.connect(school)
    intent = P.link(school, F.account(school), [1])
    plink = P.fake_link(intent)
    raw = json.loads(webhook_body("payment_link.paid", link=STATE.links[plink.id]))
    raw["payload"]["payment"]["entity"]["id"] = "pay_FORGEDXYZ"
    raw["payload"]["order"]["entity"]["receipt"] = uuid.UUID(str(intent.id)).hex
    P.deliver(api, conn, json.dumps(raw).encode())
    P.process(school)
    assert P.receipts_for_payment(admin_engine, school.tenant_id, "pay_FORGEDXYZ") == 0
    assert (
        P.scalar(admin_engine, "SELECT status FROM fin.payment_intents WHERE id = :i", i=intent.id)
        == "created"
    )


def test_FR_PAY_009_payment_for_unknown_intent_is_an_exception(
    school: Any, api: Any, admin_engine: Engine
) -> None:
    conn = P.connect(school)
    intent = P.link(school, F.account(school), [1])
    plink = P.fake_link(intent)
    payment = STATE.pay(plink.id)
    # The order's receipt names an intent this school does not have.
    order = STATE.orders[payment.order_id or ""]
    STATE.orders[order.id] = order.__class__(
        id=order.id, receipt="f" * 32, amount=order.amount, currency="INR", status="paid"
    )
    P.deliver(api, conn, webhook_body("payment.captured", payment=payment))
    P.process(school)
    assert P.receipts_for_payment(admin_engine, school.tenant_id, payment.id) == 0
    kinds = [
        i["kind"] for i in P.items(admin_engine, school.tenant_id, provider_payment_id=payment.id)
    ]
    assert kinds == ["unknown_intent"]


def test_FR_FEE_022_day_closed_waits_for_the_next_day(
    school: Any, api: Any, admin_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    conn = P.connect(school)
    intent = P.link(school, F.account(school), [1])
    body, payment = _paid_body(intent)
    P.deliver(api, conn, body)

    def closed(*_: Any, **__: Any) -> None:
        raise common.DayClosed()

    monkeypatch.setattr(common, "ensure_day_open", closed)
    P.process(school)
    assert P.receipts_for_payment(admin_engine, school.tenant_id, payment.id) == 0
    row = P.rows(
        admin_engine,
        "SELECT status, error_code, attempts FROM fin.provider_events "
        "WHERE tenant_id = :t AND status = 'received' AND error_code = 'day_closed'",
        t=school.tenant_id,
    )
    assert row
    assert row[0]["attempts"] == 1
    monkeypatch.undo()
    with admin_engine.begin() as c:
        c.execute(
            text(
                "UPDATE fin.provider_events SET next_attempt_at = now() WHERE tenant_id = :t "
                "AND status = 'received'"
            ),
            {"t": school.tenant_id},
        )
    P.process(school)
    assert P.receipts_for_payment(admin_engine, school.tenant_id, payment.id) == 1


def test_FR_PAY_012_authorization_revoked_is_confirmed_before_acting(
    school: Any, api: Any, admin_engine: Engine
) -> None:
    conn = P.connect(school)
    body = webhook_body("account.app.authorization_revoked")
    P.deliver(api, conn, body)
    P.process(school)
    # Credentials still work at the provider: nothing changes.
    assert (
        P.scalar(
            admin_engine,
            "SELECT status FROM fin.payment_connections WHERE id = :i",
            i=conn.connection_id,
        )
        == "active"
    )
    STATE.dead.add(conn.key_id)
    try:
        P.deliver(api, conn, body)
        P.process(school)
    finally:
        STATE.dead.discard(conn.key_id)
    assert (
        P.scalar(
            admin_engine,
            "SELECT status FROM fin.payment_connections WHERE id = :i",
            i=conn.connection_id,
        )
        == "revoked"
    )
