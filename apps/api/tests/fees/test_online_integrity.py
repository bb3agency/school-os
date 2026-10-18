"""Money integrity of online payments (docs/14 R2 test plan "Money integrity"; FR-FEE-006,
FR-FEE-010, FR-PAY-005, FR-PAY-006, FR-PAY-008): the same dues paid online and at the counter at
the same time give one receipt and one exception (never two receipts, numbers gap-free), the same
payment processed concurrently gives one receipt, counter receipts close open links, the link
amount comes from the ledger and is reused, and the database refuses edits to what a link asks
for or to a closed exception."""

from __future__ import annotations

import sys
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError

from app.core.db import tenant_session
from app.core.errors import Conflict, DomainError
from app.fees import online
from app.fees import online_repository as orepo
from app.fees.providers.fake import STATE, webhook_body
from app.fees.schemas import AllocationIn

pytestmark = pytest.mark.db

F: Any = sys.modules["sos_test_fees_support"]
P: Any = sys.modules["sos_test_fees_online_support"]


def _numbers(admin: Engine, tenant_id: uuid.UUID, fy: str) -> list[int]:
    with admin.connect() as c:
        return [
            int(r[0])
            for r in c.execute(
                text(
                    "SELECT receipt_no FROM fin.receipts WHERE tenant_id = :t "
                    "AND financial_year = :f ORDER BY receipt_no"
                ),
                {"t": tenant_id, "f": fy},
            )
        ]


def test_FR_PAY_005_amount_comes_from_the_ledger_and_is_reused(
    school: Any, admin_engine: Engine
) -> None:
    P.connect(school)
    account_id = F.account(school)
    first = P.link(school, account_id, [1])
    # Term 1 collectable: tuition 3000 + exam 500 (transport is taxable, never collected).
    assert first.amount == Decimal("3500.00")
    assert first.reused is False
    again = P.link(school, account_id, [1])
    assert again.id == first.id
    assert again.reused is True
    both = P.link(school, account_id, [1, 2])
    assert both.amount == Decimal("7000.00")
    # The overlapping Term 1 link was closed (FR-PAY-006).
    assert (
        P.scalar(admin_engine, "SELECT status FROM fin.payment_intents WHERE id = :i", i=first.id)
        == "cancelled"
    )
    sent = STATE.link_requests[P.fake_link(both).id]
    assert sent.reference_id == uuid.UUID(str(both.id)).hex
    assert sent.notes == {"intent": uuid.UUID(str(both.id)).hex}
    assert both.intent_code in sent.description
    assert "Synthetica" not in sent.description  # no student name ever goes to Razorpay


def test_FR_PAY_005_nothing_due_and_unknown_instalments(school: Any) -> None:
    P.connect(school)
    account_id = F.account(school)
    F.issue(school, account_id, "3500.00")
    with pytest.raises(Conflict) as caught:
        P.link(school, account_id, [1])
    assert caught.value.code == "nothing_due"
    from app.core.errors import ValidationFailed

    with pytest.raises(ValidationFailed):
        P.link(school, account_id, [9])


def test_FR_PAY_006_counter_receipt_closes_open_links(school: Any, admin_engine: Engine) -> None:
    P.connect(school)
    account_id = F.account(school)
    intent = P.link(school, account_id, [1])
    F.issue(school, account_id, "100.00")
    assert (
        P.scalar(admin_engine, "SELECT status FROM fin.payment_intents WHERE id = :i", i=intent.id)
        == "cancelled"
    )
    queued = P.scalar(
        admin_engine,
        "SELECT count(*) FROM ops.outbox WHERE tenant_id = :t "
        "AND event_type = 'fees.payment_links.cancel'",
        t=school.tenant_id,
    )
    assert queued >= 1
    # The worker cancels it at the provider.
    online.cancel_links_at_provider(school.tenant_id, [intent.id])
    assert P.fake_link(intent).status == "cancelled"


def _race(school: Any, account_id: uuid.UUID, term1: list[AllocationIn]) -> list[str]:
    """The inbox worker and a counter clerk at the same moment, for the same dues."""
    start = threading.Barrier(2)
    outcomes: list[str] = []

    def online_side() -> None:
        start.wait()
        P.process(school)
        outcomes.append("online")

    def counter_side() -> None:
        start.wait()
        try:
            F.issue(school, account_id, "3500.00", allocations=term1)
            outcomes.append("counter_receipt")
        except DomainError as exc:
            outcomes.append(exc.code)

    with ThreadPoolExecutor(max_workers=2) as pool:
        for future in [pool.submit(online_side), pool.submit(counter_side)]:
            future.result()
    return outcomes


def test_FR_FEE_010_online_and_counter_at_once_never_double_receipt(
    school: Any, api: Any, admin_engine: Engine
) -> None:
    """The parent pays the link while the clerk takes cash for the same dues: exactly one
    receipt covers the dues; the other money is an exception for a person (refund it in the
    dashboard or explain it), and the receipt numbers stay gap-free."""
    conn = P.connect(school)
    setup = F.ensure_setup(school)
    term1 = [
        AllocationIn(instalment_no=1, head_id=setup.tui, amount=Decimal("3000.00")),
        AllocationIn(instalment_no=1, head_id=setup.exm, amount=Decimal("500.00")),
    ]
    for _ in range(3):
        account_id = F.account(school)
        intent = P.link(school, account_id, [1])
        plink = P.fake_link(intent)
        payment = STATE.pay(plink.id)
        P.deliver(
            api,
            conn,
            webhook_body("payment_link.paid", link=STATE.links[plink.id], payment=payment),
        )
        outcomes = _race(school, account_id, term1)
        receipts = P.scalar(
            admin_engine,
            "SELECT count(*) FROM fin.receipts WHERE account_id = :a AND status = 'issued'",
            a=account_id,
        )
        assert receipts == 1, outcomes
        paid_term1 = P.scalar(
            admin_engine,
            "SELECT -sum(amount) FROM fin.ledger_entries WHERE account_id = :a "
            "AND entry_kind = 'payment' AND instalment_no = 1",
            a=account_id,
        )
        assert paid_term1 == Decimal("3500.00")
        online_receipts = P.receipts_for_payment(admin_engine, school.tenant_id, payment.id)
        if online_receipts == 0:
            found = P.items(admin_engine, school.tenant_id, provider_payment_id=payment.id)
            assert [i["reason_code"] for i in found] == ["dues_already_paid"]
        else:
            assert "counter_receipt" not in outcomes
    fy = P.scalar(
        admin_engine,
        "SELECT financial_year FROM fin.receipts WHERE tenant_id = :t ORDER BY issued_at DESC "
        "LIMIT 1",
        t=school.tenant_id,
    )
    numbers = _numbers(admin_engine, school.tenant_id, fy)
    assert numbers == list(range(1, len(numbers) + 1))


def test_FR_PAY_008_same_payment_processed_concurrently_gives_one_receipt(
    school: Any, api: Any, admin_engine: Engine
) -> None:
    conn = P.connect(school)
    intent = P.link(school, F.account(school), [1])
    plink = P.fake_link(intent)
    payment = STATE.pay(plink.id)
    # Two different events about the same payment, processed by two workers at once.
    P.deliver(api, conn, webhook_body("payment.captured", payment=payment))
    P.deliver(
        api, conn, webhook_body("payment_link.paid", link=STATE.links[plink.id], payment=payment)
    )
    start = threading.Barrier(3)

    def worker() -> None:
        start.wait()
        P.process(school)

    with ThreadPoolExecutor(max_workers=3) as pool:
        for f in [pool.submit(worker) for _ in range(3)]:
            f.result()
    assert P.receipts_for_payment(admin_engine, school.tenant_id, payment.id) == 1
    with tenant_session(school.tenant_id) as s:
        live = orepo.live_connection(s)
        assert live is not None
        again = online.post_payment(s, live, payment.id)
    assert again == "duplicate"


def test_FR_PAY_005_link_amount_and_instalments_never_change(
    school: Any, admin_engine: Engine
) -> None:
    P.connect(school)
    intent = P.link(school, F.account(school), [1])
    # sos_app has no column grant on what a link asks for at all.
    for sql in (
        "UPDATE fin.payment_intents SET instalments = '{1,2}' WHERE id = :i",
        "UPDATE fin.payment_intents SET amount = 1 WHERE id = :i",
    ):
        with (
            pytest.raises(DBAPIError, match="permission denied"),
            tenant_session(school.tenant_id) as s,
        ):
            s.execute(text(sql), {"i": intent.id})
    # And the trigger refuses it for any role.
    with (
        pytest.raises(DBAPIError, match="never changes what it asks for"),
        admin_engine.begin() as c,
    ):
        c.execute(
            text("UPDATE fin.payment_intents SET amount = amount + 1 WHERE id = :i"),
            {"i": intent.id},
        )


def test_FR_PAY_009_paid_link_stays_paid(school: Any, api: Any, admin_engine: Engine) -> None:
    conn = P.connect(school)
    intent = P.link(school, F.account(school), [1])
    P.pay(school, api, conn, intent)
    with (
        pytest.raises(DBAPIError, match=r"cannot become|stays paid"),
        tenant_session(school.tenant_id) as s,
    ):
        s.execute(
            text("UPDATE fin.payment_intents SET status = 'cancelled' WHERE id = :i"),
            {"i": intent.id},
        )


def test_FR_PAY_016_no_delete_on_payment_tables(school: Any, admin_engine: Engine) -> None:
    P.connect(school)
    intent = P.link(school, F.account(school), [1])
    for sql in (
        "DELETE FROM fin.payment_intents WHERE id = :i",
        "DELETE FROM fin.payment_connections",
        "DELETE FROM fin.reconciliation_items",
    ):
        with pytest.raises(DBAPIError), tenant_session(school.tenant_id) as s:
            s.execute(text(sql), {"i": intent.id})


def test_SEC_035_disconnect_erases_every_secret(school: Any, admin_engine: Engine) -> None:
    conn = P.connect(school)
    F.call(school, "owner", online.disconnect)
    row = P.rows(
        admin_engine,
        "SELECT status, key_secret_ciphertext, webhook_secret_ciphertext, route_ciphertext, "
        "previous_webhook_secret_ciphertext FROM fin.payment_connections WHERE id = :i",
        i=conn.connection_id,
    )[0]
    assert row["status"] == "disconnected"
    assert row["key_secret_ciphertext"] is None
    assert row["webhook_secret_ciphertext"] is None
    assert row["route_ciphertext"] is None
    # The database refuses a disconnected row that keeps a secret.
    with (
        pytest.raises(DBAPIError, match="payment_connections_secrets"),
        admin_engine.begin() as c,
    ):
        c.execute(
            text(
                "UPDATE fin.payment_connections SET key_secret_ciphertext = '\\x00' WHERE id = :i"
            ),
            {"i": conn.connection_id},
        )
