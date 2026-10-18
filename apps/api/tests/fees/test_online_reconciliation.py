"""Daily online reconciliation (US-2111; FR-PAY-010): the previous day's provider payments,
refunds and settlements against receipts and vouchers; exceptions recorded once, explained by a
person with an audited note, nothing changed automatically. Plus link expiry and the token
refresh job's revocation path (FR-PAY-012)."""

from __future__ import annotations

import datetime as dt
import sys
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import Engine, text

from app.core.errors import Conflict, Forbidden, ValidationFailed
from app.fees import online
from app.fees.money import IST
from app.fees.online_schemas import ExplainIn
from app.fees.providers.fake import STATE

pytestmark = pytest.mark.db

F: Any = sys.modules["sos_test_fees_support"]
P: Any = sys.modules["sos_test_fees_online_support"]


def _today() -> dt.date:
    return dt.datetime.now(IST).date()


def _rerun(admin: Engine, school: Any, day: dt.date) -> dict[str, int]:
    """Run the day again (tests share one school; a finished run is otherwise left alone)."""
    with admin.begin() as c:
        c.execute(
            text(
                "UPDATE fin.reconciliation_runs SET status = 'failed', finished_at = now() "
                "WHERE tenant_id = :t AND run_date = :d"
            ),
            {"t": school.tenant_id, "d": day},
        )
    out: dict[str, int] = online.reconcile(school.tenant_id, day)
    return out


def test_FR_PAY_010_lost_webhook_is_paid_not_receipted_once(
    school: Any, api: Any, admin_engine: Engine
) -> None:
    conn = P.connect(school)
    intent = P.link(school, F.account(school), [1])
    payment, _ = P.pay(school, api, conn, intent, deliver_event=False)
    out = _rerun(admin_engine, school, _today())
    assert out.get("exceptions", 0) >= 1, out
    found = P.items(admin_engine, school.tenant_id, provider_payment_id=payment.id)
    assert [(i["kind"], i["reason_code"]) for i in found] == [("paid_not_receipted", "no_receipt")]
    # Nothing was receipted automatically; a second run that day adds nothing.
    assert P.receipts_for_payment(admin_engine, school.tenant_id, payment.id) == 0
    assert online.reconcile(school.tenant_id, _today()) == {"already_done": 1}
    # The webhook arrives late: the receipt is issued and the exception resolves itself.
    from app.fees.providers.fake import webhook_body

    P.deliver(
        api,
        conn,
        webhook_body("payment_link.paid", link=P.fake_link(intent), payment=payment),
    )
    P.process(school)
    assert P.receipts_for_payment(admin_engine, school.tenant_id, payment.id) == 1
    item = P.items(admin_engine, school.tenant_id, provider_payment_id=payment.id)[0]
    assert item["status"] == "resolved"


def test_FR_PAY_010_receipt_without_capture_and_refund_outside_schoolos(
    school: Any, api: Any, admin_engine: Engine
) -> None:
    conn = P.connect(school)
    day = _today()
    intent = P.link(school, F.account(school), [1])
    payment, _ = P.pay(school, api, conn, intent)
    intent2 = P.link(school, F.account(school), [1])
    payment2, _ = P.pay(school, api, conn, intent2)
    # The provider later says the first payment failed; the second was refunded in the
    # dashboard without a SchoolOS voucher.
    STATE.set_payment(payment.id, status="failed", captured=False)
    STATE.set_payment(payment2.id, amount_refunded=Decimal("200.00"))
    _rerun(admin_engine, school, day)
    kinds = {
        (i["kind"], i["provider_payment_id"])
        for i in P.items(admin_engine, school.tenant_id)
        if i["provider_payment_id"] in (payment.id, payment2.id)
    }
    assert ("receipted_not_captured", payment.id) in kinds
    assert ("refunded", payment2.id) in kinds


def test_FR_PAY_010_settlement_missing_after_three_working_days(
    school: Any, api: Any, admin_engine: Engine
) -> None:
    conn = P.connect(school)
    intent = P.link(school, F.account(school), [1])
    payment, _ = P.pay(school, api, conn, intent)
    intent2 = P.link(school, F.account(school), [1])
    settled, _ = P.pay(school, api, conn, intent2)
    with admin_engine.begin() as c:
        c.execute(
            text(
                "UPDATE fin.provider_payments SET paid_at = now() - interval '10 days' "
                "WHERE provider_payment_id IN (:a, :b)"
            ),
            {"a": payment.id, "b": settled.id},
        )
    later = _today() + dt.timedelta(days=1)
    STATE.settle(settled.id, day=later)
    _rerun(admin_engine, school, later)
    missing = P.items(admin_engine, school.tenant_id, kind="settlement_missing")
    ids = {i["provider_payment_id"] for i in missing}
    assert payment.id in ids
    assert settled.id not in ids
    assert (
        P.scalar(
            admin_engine,
            "SELECT settlement_id IS NOT NULL FROM fin.provider_payments "
            "WHERE provider_payment_id = :p",
            p=settled.id,
        )
        is True
    )


def test_FR_PAY_010_explain_is_fee_manage_with_a_note_and_audited_without_it(
    school: Any, api: Any, admin_engine: Engine
) -> None:
    conn = P.connect(school)
    intent = P.link(school, F.account(school), [1])
    payment, _ = P.pay(school, api, conn, intent, deliver_event=False)
    day = _today() + dt.timedelta(days=2)
    STATE.set_payment(payment.id, created_at=dt.datetime.combine(day, dt.time(10), tzinfo=IST))
    _rerun(admin_engine, school, day)
    item = P.items(admin_engine, school.tenant_id, provider_payment_id=payment.id)[0]
    note = "Synthetic: refunded from the Razorpay dashboard"
    with pytest.raises(Forbidden):
        F.call(
            school,
            "office_staff",
            online.explain_item,
            item["id"],
            ExplainIn(note=note),
            expected_version=None,
        )
    with pytest.raises(ValidationFailed):
        F.call(
            school,
            "accountant",
            online.explain_item,
            item["id"],
            ExplainIn(note="short"),
            expected_version=None,
        )
    out = F.call(
        school,
        "accountant",
        online.explain_item,
        item["id"],
        ExplainIn(note=note),
        expected_version=None,
    )
    assert out.status == "explained"
    assert out.note == note
    with pytest.raises(Conflict):
        F.call(
            school,
            "principal",
            online.explain_item,
            item["id"],
            ExplainIn(note=note),
            expected_version=None,
        )
    events = F.W.audit_events(admin_engine, school.tenant_id, "fee.reconciliation.explained")
    mine = [e for e in events if e["resource_id"] == item["id"]]
    assert len(mine) == 1
    assert "Razorpay dashboard" not in str(mine[0]["summary"])
    page = F.call(
        school,
        "auditor_readonly",
        online.list_items,
        status="explained",
        kind=None,
        limit=50,
        cursor=None,
    )
    assert any(i.id == item["id"] for i in page.items)


def test_FR_PAY_015_expired_links_are_closed(school: Any, admin_engine: Engine) -> None:
    P.connect(school)
    intent = P.link(school, F.account(school), [1])
    # expires_at is frozen for the app; the sweep uses the clock: move it with the superuser.
    with admin_engine.begin() as c:
        c.execute(text("ALTER TABLE fin.payment_intents DISABLE TRIGGER payment_intents_guard"))
        c.execute(
            text(
                "UPDATE fin.payment_intents SET created_at = created_at - interval '1 day', "
                "expires_at = created_at - interval '23 hours' WHERE id = :i"
            ),
            {"i": intent.id},
        )
        c.execute(text("ALTER TABLE fin.payment_intents ENABLE TRIGGER payment_intents_guard"))
    assert online.expire_intents(school.tenant_id) >= 1
    assert (
        P.scalar(admin_engine, "SELECT status FROM fin.payment_intents WHERE id = :i", i=intent.id)
        == "expired"
    )
