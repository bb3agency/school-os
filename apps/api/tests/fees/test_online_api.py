"""Online payment routes over HTTP and their logs (docs/09 "Online payments"; FR-PAY-002..015;
invariants 2, 3, 5, 7; SEC-035): the key secret goes in once and the webhook secret comes out once
(never cached, never read back), step-up for configuration, other-school links are 404, audit is
in the same transaction as the change (a provider failure leaves neither), and no key, secret,
signature, payer contact, email or VPA reaches a log line or an audit summary."""

from __future__ import annotations

import json
import sys
import uuid
from typing import Any

import pytest
from sqlalchemy import Engine, text

from app.fees.providers.base import ProviderError
from app.fees.providers.fake import STATE, fixture, webhook_body
from app.fees.providers.razorpay import signature

pytestmark = pytest.mark.db

F: Any = sys.modules["sos_test_fees_support"]
P: Any = sys.modules["sos_test_fees_online_support"]
KEY_SECRET = "synthetic-api-key-secret-9876"


def _audit(admin: Engine, tenant_id: uuid.UUID, like: str) -> str:
    with admin.connect() as c:
        return " ".join(
            str(row[0])
            for row in c.execute(
                text("SELECT summary FROM audit.events WHERE tenant_id = :t AND action LIKE :a"),
                {"t": tenant_id, "a": like},
            ).all()
        )


def test_FR_PAY_003_secret_in_once_out_once_over_http(
    school: Any, api: Any, admin_engine: Engine
) -> None:
    P.disconnect_if_connected(school)
    owner = school.people["owner"]
    key_id = P.key_id()
    body = {"key_id": key_id, "key_secret": KEY_SECRET}
    # Step-up: a stale MFA sign-in is refused before anything happens.
    stale = api.call(owner, "POST", "/api/v1/payments/connection/manual", json=body, auth_age_s=301)
    assert stale.status_code == 428
    # Only the owner configures.
    principal = api.call(
        school.people["principal"], "POST", "/api/v1/payments/connection/manual", json=body
    )
    assert principal.status_code == 403
    res = api.call(owner, "POST", "/api/v1/payments/connection/manual", json=body)
    assert res.status_code == 201, res.text
    assert res.headers["Cache-Control"] == "no-store"
    secret = res.json()["webhook"]["secret"]
    assert len(secret) >= 40
    assert KEY_SECRET not in res.text
    again = api.call(owner, "GET", "/api/v1/payments/connection")
    assert again.status_code == 200
    assert secret not in again.text
    assert KEY_SECRET not in again.text
    reader = api.call(school.people["accountant"], "GET", "/api/v1/payments/connection")
    assert reader.status_code == 200
    staff = api.call(school.people["office_staff"], "GET", "/api/v1/payments/connection")
    assert staff.status_code == 403
    assert secret not in _audit(admin_engine, school.tenant_id, "payments.%")


def test_SEC_001_other_school_links_are_404(
    school: Any, other_school: Any, api: Any, admin_engine: Engine
) -> None:
    P.connect(school)
    P.connect(other_school)
    theirs = P.link(other_school, F.account(other_school), [1], role="accountant")
    mine = school.people["office_staff"]
    for method, path, body in (
        ("GET", f"/api/v1/fees/payment-intents/{theirs.id}", None),
        ("POST", f"/api/v1/fees/payment-intents/{theirs.id}/cancel", None),
        ("GET", f"/api/v1/fees/accounts/{theirs.account_id}/payment-intents", None),
        (
            "POST",
            f"/api/v1/fees/accounts/{theirs.account_id}/payment-intents",
            {"instalments": [1]},
        ),
    ):
        res = api.call(mine, method, path, json=body)
        assert res.status_code == 404, (method, path, res.text)
    assert (
        P.scalar(admin_engine, "SELECT status FROM fin.payment_intents WHERE id = :i", i=theirs.id)
        == "created"
    )


def test_FR_PAY_005_intent_over_http_is_idempotent_and_audited(
    school: Any, api: Any, admin_engine: Engine
) -> None:
    P.connect(school)
    account_id = F.account(school)
    who = school.people["office_staff"]
    headers = {"Idempotency-Key": f"link-{uuid.uuid4().hex}"}
    path = f"/api/v1/fees/accounts/{account_id}/payment-intents"
    first = api.call(who, "POST", path, json={"instalments": [1]}, headers=headers)
    assert first.status_code == 201, first.text
    again = api.call(who, "POST", path, json={"instalments": [1]}, headers=headers)
    assert again.headers.get("Idempotent-Replayed") == "true"
    assert again.json()["id"] == first.json()["id"]
    teacher = api.call(school.people["teacher"], "GET", path)
    assert teacher.status_code == 403
    events = F.W.audit_events(admin_engine, school.tenant_id, "fee.payment_link.created")
    mine = [e for e in events if e["resource_id"] == uuid.UUID(first.json()["id"])]
    assert len(mine) == 1
    assert mine[0]["summary"]["amount"] == "3500.00"


def test_CLAUDE_6_7_provider_failure_leaves_no_intent_and_no_audit(
    school: Any, api: Any, admin_engine: Engine
) -> None:
    P.connect(school)
    account_id = F.account(school)
    STATE.fail_next["create_link"] = ProviderError("unavailable", status=503, transient=True)
    before = len(F.W.audit_events(admin_engine, school.tenant_id, "fee.payment_link.created"))
    res = api.call(
        school.people["office_staff"],
        "POST",
        f"/api/v1/fees/accounts/{account_id}/payment-intents",
        json={"instalments": [1]},
    )
    assert res.status_code == 502
    assert res.json()["code"] == "payment_link_failed"
    assert (
        P.scalar(
            admin_engine,
            "SELECT count(*) FROM fin.payment_intents WHERE account_id = :a",
            a=account_id,
        )
        == 0
    )
    after = len(F.W.audit_events(admin_engine, school.tenant_id, "fee.payment_link.created"))
    assert after == before


def test_PRV_024_no_keys_signatures_or_payer_details_in_logs(
    school: Any, api: Any, admin_engine: Engine, capsys: pytest.CaptureFixture[str]
) -> None:
    capsys.readouterr()
    conn = P.connect(school, secret=KEY_SECRET)
    intent = P.link(school, F.account(school), [1])
    plink = P.fake_link(intent)
    payment = STATE.pay(plink.id)
    raw = json.loads(webhook_body("payment_link.paid", link=STATE.links[plink.id], payment=payment))
    payer = fixture("payment_link.paid")["payload"]["payment"]["entity"]
    body = json.dumps(raw).encode()
    sig = signature(conn.secret.encode(), body)
    assert P.deliver(api, conn, body, sig=sig).status_code == 200
    assert P.deliver(api, conn, body, sig="0" * 64).status_code == 401
    P.process(school)
    out = capsys.readouterr()
    logs = out.out + out.err
    for leaked in (
        KEY_SECRET,
        conn.secret,
        conn.route,
        sig,
        payer["vpa"],
        payer["email"],
        payer["contact"],
    ):
        assert leaked not in logs, leaked
    summaries = _audit(admin_engine, school.tenant_id, "%")
    for leaked in (KEY_SECRET, conn.secret, sig, payer["vpa"], payer["contact"]):
        assert leaked not in summaries, leaked
