"""The Meta webhook: signatures, routing to the right school, replay, size, the subscription
challenge and payload minimisation (R2-1C; ADR-0043 decision 6, ADR-0047; FR-MSG-018, FR-MSG-020;
SEC-034). Synthetic Meta-shaped fixtures only, through the real Meta verifier and through the
R2-0 fake verifier.
"""

from __future__ import annotations

import json
import sys
from typing import Any

import pytest
from pydantic import SecretStr
from sqlalchemy import Engine

from app.core.config import Environment, Settings
from app.messaging import service
from app.messaging.webhook import MetaWebhookVerifier, parse_entries, signature
from app.public import guards, webhooks
from app.public.webhooks import FAKE_SIGNATURE_HEADER, FakeWebhookVerifier

pytestmark = pytest.mark.db
M = sys.modules["sos_test_messaging_support"]


def _events(admin: Engine, tenant_id: Any) -> int:
    rows: list[dict[str, Any]] = M.rows(
        admin, "SELECT count(*) AS n FROM msg.provider_events WHERE tenant_id = :t", t=tenant_id
    )
    return int(rows[0]["n"])


def test_SEC_034_meta_signature_valid_invalid_missing(
    world: Any, api: Any, admin_engine: Engine
) -> None:
    _, waba = M.connect(world.a)
    before = _events(admin_engine, world.a.tenant_id)
    payload = M.inbound(waba, "91" + M.synthetic_mobile(), text_body="Hello")
    assert M.post(api.client, payload, sign_with=b"not-the-app-secret").status_code == 401
    assert M.post(api.client, payload, header="").status_code == 401
    assert M.post(api.client, payload, header="sha1=abcdef").status_code == 401
    tampered = json.dumps(payload).encode()
    good_sig = signature(M.secret(), tampered)
    assert (
        M.post(api.client, tampered.replace(b"Hello", b"Hullo"), header=good_sig).status_code == 401
    )
    assert _events(admin_engine, world.a.tenant_id) == before
    res = M.post(api.client, payload)
    assert res.status_code == 200
    assert _events(admin_engine, world.a.tenant_id) == before + 1


def test_SEC_034_events_reach_only_the_school_that_owns_the_waba(
    world: Any, api: Any, admin_engine: Engine
) -> None:
    _, waba_a = M.connect(world.a)
    _, waba_b = M.connect(world.b)
    a_before = _events(admin_engine, world.a.tenant_id)
    b_before = _events(admin_engine, world.b.tenant_id)
    res = M.post(api.client, M.inbound(waba_b, "91" + M.synthetic_mobile(), text_body="Hi"))
    assert res.status_code == 200
    assert _events(admin_engine, world.a.tenant_id) == a_before
    assert _events(admin_engine, world.b.tenant_id) == b_before + 1
    # An unknown WABA is acknowledged (Meta sends one callback for every school) and dropped.
    unknown = M.inbound("9" + M.digits(14), "91" + M.synthetic_mobile(), text_body="Hi")
    assert M.post(api.client, unknown).status_code == 200
    assert _events(admin_engine, world.a.tenant_id) == a_before
    del waba_a


def test_SEC_034_replayed_delivery_is_acknowledged_once(
    world: Any, api: Any, admin_engine: Engine
) -> None:
    _, waba = M.connect(world.a)
    before = _events(admin_engine, world.a.tenant_id)
    payload = M.inbound(waba, "91" + M.synthetic_mobile(), text_body="Synthetic")
    for _ in range(3):
        assert M.post(api.client, payload).status_code == 200
    assert _events(admin_engine, world.a.tenant_id) == before + 1


def test_SEC_034_through_the_fake_verifier(world: Any, api: Any, admin_engine: Engine) -> None:
    """The same route with the R2-0 fake verifier (local/CI only) in place of Meta's."""
    _, waba = M.connect(world.a)
    secret = b"synthetic-fake-verifier-secret-0123"
    fake = FakeWebhookVerifier("meta", secret)
    webhooks.unregister_webhook_verifier("meta")
    webhooks.register_webhook_verifier(fake)
    try:
        before = _events(admin_engine, world.a.tenant_id)
        body = json.dumps(M.inbound(waba, "91" + M.synthetic_mobile(), text_body="x")).encode()
        bad = api.client.post(M.WEBHOOK, content=body, headers={FAKE_SIGNATURE_HEADER: "00"})
        assert bad.status_code == 401
        ok = api.client.post(
            M.WEBHOOK, content=body, headers={FAKE_SIGNATURE_HEADER: fake.sign(body)}
        )
        assert ok.status_code == 200
        assert _events(admin_engine, world.a.tenant_id) == before + 1
    finally:
        webhooks.unregister_webhook_verifier("meta")
        webhooks.register_webhook_verifier(MetaWebhookVerifier())


def test_SEC_034_body_limit_is_metas_three_megabytes(world: Any, api: Any) -> None:
    _, waba = M.connect(world.a)
    payload = M.inbound(waba, "91" + M.synthetic_mobile(), text_body="x")
    payload["padding"] = "a" * (2 * 1024 * 1024)  # above the API's usual 1 MiB, below 3 MB
    assert M.post(api.client, payload).status_code == 200
    payload["padding"] = "a" * (3 * 1024 * 1024 + 10)
    assert M.post(api.client, payload).status_code == 413


def test_FR_MSG_020_subscription_challenge(
    world: Any, api: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    token = "synthetic-verify-token-0123456789abcdef"
    configured = Settings(env=Environment.CI, meta_webhook_verify_token=SecretStr(token))
    monkeypatch.setattr(guards, "get_settings", lambda: configured)
    query = {"hub.mode": "subscribe", "hub.verify_token": token, "hub.challenge": "8812345"}
    ok = api.client.get(M.WEBHOOK, params=query)
    assert ok.status_code == 200
    assert ok.text == "8812345"
    assert api.client.get(M.WEBHOOK, params={**query, "hub.verify_token": "x"}).status_code == 403
    del world


def test_FR_MSG_018_inbound_text_never_leaves_the_parser() -> None:
    payload = M.inbound("1" + M.digits(14), "919000012345", text_body="My child is unwell today")
    events = parse_entries(json.dumps(payload).encode())
    (event,) = next(iter(events.values()))
    assert event.event_type == "inbound_message"
    assert event.payload == {"wa_id": "919000012345", "kind": "other", "code": None}
    assert "unwell" not in json.dumps(event.payload)
    status = M.status("1", "wamid.X", "delivered", code=None)
    (st,) = parse_entries(json.dumps(status).encode())["1"]
    assert st.event_key.startswith("status:wamid.X:delivered:")
    assert "recipient_id" not in st.payload


def test_FR_MSG_020_store_events_rereads_the_handle(world: Any, admin_engine: Engine) -> None:
    account_id, waba = M.connect(world.a)
    payload = M.inbound(waba, "91" + M.synthetic_mobile(), text_body="x")
    (events,) = parse_entries(json.dumps(payload).encode()).values()
    import uuid

    assert service.store_events(world.a.tenant_id, uuid.uuid4(), waba, events) == 0
    del account_id, admin_engine
