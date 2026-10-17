"""The school's Razorpay connection (US-2107; FR-PAY-002..004, FR-PAY-012..014; SEC-035; ADR-0042
decision 6, P1, D13): manual keys behind the pilot switch, checked and wrapped, never returned;
the webhook secret shown once; OAuth with a single-use state bound to the member, the account
webhook created, tokens refreshed before expiry; revocation; disconnect."""

from __future__ import annotations

import datetime as dt
import sys
from typing import Any

import pytest
from pydantic import SecretStr
from sqlalchemy import Engine, text

from app.core.errors import Conflict, Forbidden, ValidationFailed
from app.fees import online, payments_runtime
from app.fees.online_schemas import ManualKeysIn, OAuthCallbackIn
from app.fees.providers.base import ProviderError
from app.fees.providers.fake import STATE

pytestmark = pytest.mark.db

F: Any = sys.modules["sos_test_fees_support"]
P: Any = sys.modules["sos_test_fees_online_support"]
PILOT = {"razorpay_manual_keys_allowed": True, "webhook_base_url": "https://app.example.test"}
PARTNER = {
    **PILOT,
    "razorpay_partner_client_id": "SYNTHETICCLIENT",
    "razorpay_partner_client_secret": SecretStr("synthetic-partner-secret-0123456789"),
    "razorpay_oauth_redirect_uri": "https://app.example.test/bff/payments/oauth/callback",
}


@pytest.fixture
def partner() -> Any:
    payments_runtime.patch_for_tests(PARTNER)
    yield
    payments_runtime.patch_for_tests(PILOT)


def test_FR_PAY_003_manual_keys_wrapped_and_never_returned(
    school: Any, admin_engine: Engine
) -> None:
    secret = "synthetic-key-secret-0042"
    conn = P.connect(school, secret=secret)
    assert conn.url == f"https://app.example.test/api/v1/webhooks/razorpay/{conn.route}"
    out = F.call(school, "owner", online.get_connection)
    dumped = out.model_dump_json()
    assert secret not in dumped
    assert conn.secret not in dumped
    assert conn.route in dumped  # the URL is shown again; the secret is not
    assert out.key_id_hint is not None
    assert conn.key_id not in dumped
    row = P.rows(
        admin_engine,
        "SELECT key_secret_ciphertext, webhook_secret_ciphertext FROM fin.payment_connections "
        "WHERE id = :i",
        i=conn.connection_id,
    )[0]
    assert secret.encode() not in bytes(row["key_secret_ciphertext"])
    assert conn.secret.encode() not in bytes(row["webhook_secret_ciphertext"])
    events = F.W.audit_events(admin_engine, school.tenant_id, "payments.connected")
    mine = [e for e in events if e["resource_id"] == conn.connection_id]
    assert mine
    assert mine[0]["summary"] == {"mode": "manual_keys", "environment": "test"}
    # The route handle is a public handle of this school (ADR-0047 decision 3).
    assert (
        P.scalar(
            admin_engine,
            "SELECT count(*) FROM core.public_handles h JOIN fin.payment_connections p "
            "ON p.route_handle_id = h.id WHERE p.id = :i AND h.kind = 'razorpay_route' "
            "AND h.revoked_at IS NULL",
            i=conn.connection_id,
        )
        == 1
    )


def test_FR_PAY_003_bad_keys_and_switch_off(school: Any) -> None:
    P.disconnect_if_connected(school)
    with pytest.raises(ValidationFailed):
        F.call(
            school,
            "owner",
            online.connect_manual,
            ManualKeysIn.model_validate({"key_id": P.key_id(), "key_secret": "bad-secret-123"}),
        )
    payments_runtime.patch_for_tests({**PILOT, "razorpay_manual_keys_allowed": False})
    try:
        with pytest.raises(Forbidden) as caught:
            F.call(
                school,
                "owner",
                online.connect_manual,
                ManualKeysIn.model_validate({"key_id": P.key_id(), "key_secret": "secret-12345"}),
            )
        assert caught.value.code == "manual_keys_not_allowed"
    finally:
        payments_runtime.patch_for_tests(PILOT)


def test_FR_PAY_002_only_the_owner_configures(school: Any) -> None:
    P.connect(school)
    for role in ("principal", "accountant", "office_staff"):
        with pytest.raises(Forbidden):
            F.call(school, role, online.rotate_webhook_secret)
    again = ManualKeysIn.model_validate({"key_id": P.key_id(), "key_secret": "x" * 12})
    with pytest.raises(Conflict) as caught:
        F.call(school, "owner", online.connect_manual, again)
    assert caught.value.code == "payments_already_connected"


def test_FR_PAY_012_test_connection_marks_revoked_keys(school: Any, admin_engine: Engine) -> None:
    conn = P.connect(school)
    ok = F.call(school, "owner", online.test_connection)
    assert ok.ok is True
    STATE.dead.add(conn.key_id)
    out = F.call(school, "owner", online.test_connection)
    assert out.ok is False
    status = P.scalar(
        admin_engine,
        "SELECT status FROM fin.payment_connections WHERE id = :i",
        i=conn.connection_id,
    )
    assert status == "revoked"
    notes = P.scalar(
        admin_engine,
        "SELECT count(*) FROM ops.notifications WHERE template_key = "
        "'payments.connection_revoked' AND resource_id = :r",
        r=conn.connection_id,
    )
    assert notes >= 1
    # No new links while revoked.
    with pytest.raises(Conflict) as caught:
        P.link(school, F.account(school), [1])
    assert caught.value.code == "payments_not_connected"
    STATE.dead.discard(conn.key_id)
    P.connect(school)  # a revoked connection is replaced by a new one


def test_FR_PAY_013_disconnect_revokes_route_and_cancels_links(
    school: Any, api: Any, admin_engine: Engine
) -> None:
    conn = P.connect(school)
    intent = P.link(school, F.account(school), [1])
    F.call(school, "owner", online.disconnect)
    assert P.fake_link(intent).status == "cancelled"
    assert (
        P.scalar(admin_engine, "SELECT status FROM fin.payment_intents WHERE id = :i", i=intent.id)
        == "cancelled"
    )
    from app.fees.providers.fake import webhook_body

    assert P.deliver(api, conn, webhook_body("payment.failed")).status_code == 404
    out = F.call(school, "owner", online.get_connection)
    assert out.connected is False
    events = F.W.audit_events(admin_engine, school.tenant_id, "payments.disconnected")
    assert any(e["resource_id"] == conn.connection_id for e in events)


def test_FR_PAY_002_oauth_not_available_before_partner_approval(school: Any) -> None:
    P.disconnect_if_connected(school)
    with pytest.raises(Conflict) as caught:
        F.call(school, "owner", online.oauth_start)
    assert caught.value.code == "oauth_not_available"


def test_FR_PAY_002_oauth_state_is_single_use_and_bound_to_the_member(
    school: Any, api: Any, admin_engine: Engine, partner: Any
) -> None:
    P.disconnect_if_connected(school)
    start = F.call(school, "owner", online.oauth_start)
    state = start.authorize_url.split("state=")[1]
    with pytest.raises(Forbidden):
        F.call(
            school,
            "owner",
            online.oauth_callback,
            OAuthCallbackIn(code="synthetic-code-0001", state="x" * 43),
        )
    out = F.call(
        school,
        "owner",
        online.oauth_callback,
        OAuthCallbackIn(code="synthetic-code-0001", state=state),
    )
    assert out.mode == "oauth"
    assert out.status == "active"
    assert out.provider_account_id is not None
    hooks = [w for w in STATE.webhooks.values() if w["account_id"] == out.provider_account_id]
    assert hooks
    assert hooks[-1]["url"].startswith("https://app.example.test/api/v1/webhooks/")
    assert "payment_link.paid" in hooks[-1]["events"]
    # The state was used: a second callback with it fails.
    with pytest.raises(Forbidden):
        F.call(
            school,
            "owner",
            online.oauth_callback,
            OAuthCallbackIn(code="synthetic-code-0002", state=state),
        )


def test_FR_PAY_012_tokens_refreshed_before_expiry_and_refusal_revokes(
    school: Any, api: Any, admin_engine: Engine, partner: Any
) -> None:
    P.disconnect_if_connected(school)
    start = F.call(school, "owner", online.oauth_start)
    state = start.authorize_url.split("state=")[1]
    conn = F.call(
        school,
        "owner",
        online.oauth_callback,
        OAuthCallbackIn(code="synthetic-code-0003", state=state),
    )
    assert online.refresh_tokens(school.tenant_id) == "fresh"
    with admin_engine.begin() as c:
        c.execute(
            text(
                "UPDATE fin.payment_connections SET access_expires_at = now() + interval '3 days' "
                "WHERE id = :i"
            ),
            {"i": conn.id},
        )
    before = P.scalar(
        admin_engine,
        "SELECT refresh_token_ciphertext FROM fin.payment_connections WHERE id = :i",
        i=conn.id,
    )
    assert online.refresh_tokens(school.tenant_id) == "refreshed"
    after = P.rows(
        admin_engine,
        "SELECT refresh_token_ciphertext, access_expires_at FROM fin.payment_connections "
        "WHERE id = :i",
        i=conn.id,
    )[0]
    assert bytes(after["refresh_token_ciphertext"]) != bytes(before)
    assert after["access_expires_at"] > dt.datetime.now(dt.UTC) + dt.timedelta(days=60)
    with admin_engine.begin() as c:
        c.execute(
            text(
                "UPDATE fin.payment_connections SET access_expires_at = now() + interval '1 day' "
                "WHERE id = :i"
            ),
            {"i": conn.id},
        )
    STATE.fail_next["refresh"] = ProviderError("auth_failed", status=401)
    assert online.refresh_tokens(school.tenant_id) == "revoked"
    assert (
        P.scalar(
            admin_engine, "SELECT status FROM fin.payment_connections WHERE id = :i", i=conn.id
        )
        == "revoked"
    )
    P.disconnect_if_connected(school)
