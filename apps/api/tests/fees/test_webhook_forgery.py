"""Razorpay webhook forgery and replay (R2-3I hardening; ADR-0042 decision 7, ADR-0047;
FR-PAY-007..009; SEC-034, SEC-037; owner decision D31). Synthetic recorded fixtures through the
fake provider.

Adds to ``test_online_webhooks.py`` (signatures, unknown routes, replay inside the window,
out-of-order events, re-fetch amount, currency and status mismatches):

- every forgery is a bare 401 problem whose ``instance`` is the route template (never the
  school's route value), ``no-store``, nothing stored, and one ``security.webhook.rejected`` log
  line that carries no route value, secret, signature, event id or payment id;
- a replay after the guard's replay window, and the same signed body re-sent under new event ids
  (the event id header is not signed), still give exactly one receipt;
- a suspended school's route answers 404 and stores nothing, and an event stored before the
  suspension waits: the sweep makes no receipt until the school is active again (D31).
"""

from __future__ import annotations

import sys
from typing import Any

import pytest
from sqlalchemy import Engine, text

from app.fees import tasks_online
from app.fees.providers.fake import STATE, webhook_body
from app.fees.providers.razorpay import signature
from app.public import guards

pytestmark = pytest.mark.db

F: Any = sys.modules["sos_test_fees_support"]
P: Any = sys.modules["sos_test_fees_online_support"]
TEMPLATE = "/api/v1/webhooks/razorpay/{route}"


def _events(admin: Engine, tenant_id: Any) -> int:
    return int(
        P.scalar(
            admin, "SELECT count(*) FROM fin.provider_events WHERE tenant_id = :t", t=tenant_id
        )
    )


def _set_status(admin: Engine, tenant_id: Any, status: str) -> None:
    with admin.begin() as c:
        c.execute(
            text("UPDATE core.tenants SET status = :s WHERE id = :t"), {"s": status, "t": tenant_id}
        )


def _paid(school: Any) -> tuple[Any, Any, bytes, Any]:
    conn = P.connect(school)
    intent = P.link(school, F.account(school), [1])
    plink = P.fake_link(intent)
    payment = STATE.pay(plink.id)
    body = webhook_body("payment_link.paid", link=STATE.links[plink.id], payment=payment)
    return conn, intent, body, payment


def _intent_status(admin: Engine, intent: Any) -> str:
    return str(P.scalar(admin, "SELECT status FROM fin.payment_intents WHERE id = :i", i=intent.id))


def test_SEC_034_forgeries_store_nothing_and_log_no_secret(
    school: Any,
    other_school: Any,
    api: Any,
    admin_engine: Engine,
    capsys: pytest.CaptureFixture[str],
) -> None:
    conn, intent, body, payment = _paid(school)
    theirs = P.connect(other_school)
    good = signature(conn.secret.encode(), body)
    before = _events(admin_engine, school.tenant_id)
    event = P.event_id()
    forgeries = {
        "wrong_secret": {"secret": "synthetic-not-the-school-secret"},
        "other_school_secret": {"secret": theirs.secret},
        "missing": {"sig": ""},
        "sha256_prefix": {"sig": "sha256=" + good},
        "truncated": {"sig": good[:-1]},
        "not_hex": {"sig": "zz" * 32},
    }
    capsys.readouterr()
    for name, kw in forgeries.items():
        res = P.deliver(api, conn, body, event=event, **kw)
        assert res.status_code == 401, (name, res.text)
        problem = res.json()
        assert problem["instance"] == TEMPLATE
        assert set(problem) <= {"type", "title", "status", "code", "instance", "request_id"}
        assert conn.route not in res.text
        assert "no-store" in res.headers["cache-control"]
    tampered = body.replace(b'"amount":', b'"amount" :', 1)
    assert P.deliver(api, conn, tampered, sig=good, event=event).status_code == 401
    unknown = P.deliver(api, conn, body, route="B" * 24, event=event)
    assert unknown.status_code == 404
    assert unknown.json()["instance"] == TEMPLATE
    out, err = capsys.readouterr()
    logged = out + err
    assert "security.webhook.rejected" in logged
    for value in (conn.route, conn.secret, theirs.secret, good, event, payment.id, "B" * 24):
        assert value not in logged
    P.process(school)
    assert _events(admin_engine, school.tenant_id) == before
    assert _intent_status(admin_engine, intent) == "created"


def test_FR_PAY_007_replays_after_the_window_and_under_new_event_ids_give_one_receipt(
    school: Any, api: Any, admin_engine: Engine
) -> None:
    conn, intent, body, payment = _paid(school)
    event = P.event_id()
    assert P.deliver(api, conn, body, event=event).status_code == 200
    guards.forget_event("razorpay", event)  # the replay window has passed
    assert P.deliver(api, conn, body, event=event).status_code == 200
    # The event id header is not signed: the same signed body under fresh ids.
    for _ in range(3):
        assert P.deliver(api, conn, body).status_code == 200
    P.process(school)
    P.process(school)
    assert P.receipts_for_payment(admin_engine, school.tenant_id, payment.id) == 1
    assert _intent_status(admin_engine, intent) == "paid"


def test_D31_a_suspended_school_stores_and_processes_nothing(
    school: Any, api: Any, admin_engine: Engine
) -> None:
    conn, _, body, payment = _paid(school)
    stored_first = P.event_id()
    assert P.deliver(api, conn, body, event=stored_first).status_code == 200
    before = _events(admin_engine, school.tenant_id)
    _set_status(admin_engine, school.tenant_id, "suspended")
    try:
        late = P.deliver(api, conn, webhook_body("payment.failed"))
        assert late.status_code == 404
        assert conn.route not in late.text
        assert _events(admin_engine, school.tenant_id) == before
        tasks_online.sweep_payment_events()
        assert P.receipts_for_payment(admin_engine, school.tenant_id, payment.id) == 0
    finally:
        _set_status(admin_engine, school.tenant_id, "active")
    tasks_online.sweep_payment_events()
    assert P.receipts_for_payment(admin_engine, school.tenant_id, payment.id) == 1
