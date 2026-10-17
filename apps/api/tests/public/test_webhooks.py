"""The webhook guard registry and the Meta verify-token guard (ADR-0047 decision 4; SEC-034,
SEC-040; docs/07 §11.2, docs/09 §4 "Webhooks"). Synthetic data only.

No real provider is wired in R2-0: a small app mounts webhook routes with the real guards and
the fake verifier (HMAC-SHA256 of the raw body under a test secret), routed through a real
``razorpay_route`` handle and the resolver. Covers valid, invalid, missing and wrong-school
signatures, unknown routes, replayed event ids, body limits, an unregistered provider (404),
the fake verifier refused in staging/prod, and Meta's subscription challenge.
"""

from __future__ import annotations

import hashlib
import hmac
import sys
import uuid
from collections.abc import Iterator
from typing import Annotated, Any

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import Engine

from app.authz.kv import InMemoryKV, set_kv_store
from app.core.config import Environment, Settings
from app.core.db import tenant_session
from app.core.errors import install_error_handlers
from app.core.middleware import install_middleware
from app.public import guards, service, webhooks
from app.public.config import rules
from app.public.guards import WebhookCaller, require_meta_verify_token, require_webhook_signature
from app.public.webhooks import (
    FAKE_EVENT_HEADER,
    FAKE_SIGNATURE_HEADER,
    FakeWebhookVerifier,
    RegistryError,
)

pytestmark = pytest.mark.db
P = sys.modules["sos_test_public_support"]
SECRET = b"synthetic-webhook-secret-0123456789"
OTHER_SECRET = b"another-schools-synthetic-secret-987"
BODY = b'{"event":"payment.captured","payload":{"id":"pay_synthetic"}}'


def _app() -> FastAPI:
    app = FastAPI()
    install_error_handlers(app)
    install_middleware(app, Settings(env=Environment.CI))

    @app.post("/api/v1/webhooks/razorpay/{route}")
    def razorpay(
        caller: Annotated[WebhookCaller, Depends(require_webhook_signature("razorpay"))],
    ) -> dict[str, Any]:
        return {
            "duplicate": caller.duplicate,
            "event_id": caller.event_id,
            "tenants": [str(h.tenant_id) for h in caller.handles],
            "bytes": len(caller.body),
        }

    @app.get("/api/v1/webhooks/meta")
    def challenge(value: Annotated[str, Depends(require_meta_verify_token())]) -> int:
        return int(value)

    return app


@pytest.fixture
def client(app_engine: Engine) -> Iterator[TestClient]:
    set_kv_store(InMemoryKV())
    webhooks.register_webhook_verifier(
        FakeWebhookVerifier("razorpay", SECRET, route_kind="razorpay_route")
    )
    try:
        with TestClient(_app()) as c:
            yield c
    finally:
        webhooks.unregister_webhook_verifier("razorpay")
        set_kv_store(None)


def _sign(body: bytes, secret: bytes = SECRET) -> str:
    return hmac.new(secret, body, hashlib.sha256).hexdigest()


def _post(
    client: TestClient,
    route: str,
    *,
    body: bytes = BODY,
    signature: str | None = None,
    event: str | None = None,
) -> Any:
    headers = {"Content-Type": "application/json"}
    if signature is not None:
        headers[FAKE_SIGNATURE_HEADER] = signature
    if event is not None:
        headers[FAKE_EVENT_HEADER] = event
    return client.post(f"/api/v1/webhooks/razorpay/{route}", content=body, headers=headers)


@pytest.fixture
def route(world: Any) -> str:
    issued = P.issue(world.a, "razorpay_route", target_type="payment_connection")
    assert issued.key is not None
    return str(issued.key)


def test_SEC_034_valid_signature_routes_to_the_school(
    world: Any, client: TestClient, route: str
) -> None:
    res = _post(client, route, signature=_sign(BODY), event=f"evt_{uuid.uuid4().hex}")
    assert res.status_code == 200, res.text
    assert res.json()["tenants"] == [str(world.a.tenant_id)]
    assert res.json()["duplicate"] is False
    assert res.json()["bytes"] == len(BODY)


@pytest.mark.parametrize(
    "signature",
    [None, "", "00" * 32, _sign(BODY, OTHER_SECRET), _sign(BODY + b" ")],
    ids=["missing", "empty", "zeros", "other_school_secret", "other_body"],
)
def test_SEC_034_bad_signatures_get_401_without_detail(
    client: TestClient, route: str, signature: str | None
) -> None:
    res = _post(client, route, signature=signature, event="evt_x")
    assert res.status_code == 401
    assert "detail" not in res.json()
    assert res.json()["code"] == "unauthenticated"


def test_SEC_034_unknown_or_revoked_route_is_404(world: Any, client: TestClient) -> None:
    assert _post(client, "A" * 24, signature=_sign(BODY)).status_code == 404
    assert _post(client, "not-a-route", signature=_sign(BODY)).status_code == 404
    issued = P.issue(world.a, "razorpay_route", target_type="payment_connection")
    with tenant_session(world.a.tenant_id) as db:
        service.revoke(db, issued.handle_id, reason="payments_disconnected", by_user=False)
    assert _post(client, str(issued.key), signature=_sign(BODY)).status_code == 404


def test_SEC_034_replayed_event_ids_are_marked_duplicate(client: TestClient, route: str) -> None:
    event = f"evt_{uuid.uuid4().hex}"
    first = _post(client, route, signature=_sign(BODY), event=event)
    again = _post(client, route, signature=_sign(BODY), event=event)
    assert (first.status_code, again.status_code) == (200, 200)
    assert first.json()["duplicate"] is False
    assert again.json()["duplicate"] is True
    other = _post(client, route, signature=_sign(BODY), event=f"evt_{uuid.uuid4().hex}")
    assert other.json()["duplicate"] is False


def test_SEC_034_body_limit_applies_before_the_signature(client: TestClient, route: str) -> None:
    limit = rules().webhook_providers["razorpay"].max_body_bytes
    big = b"{" + b" " * limit + b"}"
    res = _post(client, route, body=big, signature=_sign(big))
    assert res.status_code == 413


def test_SEC_034_provider_without_a_verifier_answers_404(client: TestClient, route: str) -> None:
    webhooks.unregister_webhook_verifier("razorpay")
    try:
        assert _post(client, route, signature=_sign(BODY)).status_code == 404
    finally:
        webhooks.register_webhook_verifier(
            FakeWebhookVerifier("razorpay", SECRET, route_kind="razorpay_route")
        )


def test_SEC_040_registry_refuses_fakes_in_production_and_duplicates() -> None:
    prod = Settings.model_construct(env=Environment.PROD)
    with pytest.raises(RegistryError, match="refused"):
        webhooks.register_webhook_verifier(FakeWebhookVerifier("meta", SECRET), settings=prod)
    # The API factory registers the real Meta verifier (R2-1C); set it aside for this check.
    real_meta = webhooks.webhook_verifier("meta")
    webhooks.unregister_webhook_verifier("meta")
    webhooks.register_webhook_verifier(FakeWebhookVerifier("meta", SECRET))
    try:
        with pytest.raises(RegistryError, match="already registered"):
            webhooks.register_webhook_verifier(FakeWebhookVerifier("meta", SECRET))
    finally:
        webhooks.unregister_webhook_verifier("meta")
        if real_meta is not None:
            webhooks.register_webhook_verifier(real_meta)
    with pytest.raises(RegistryError, match="unknown webhook provider"):
        webhooks.register_webhook_verifier(FakeWebhookVerifier("paypal", SECRET))  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="unknown webhook provider"):
        require_webhook_signature("paypal")  # type: ignore[arg-type]


# --- Meta subscription challenge ---------------------------------------------------------------


def test_SEC_034_meta_verify_token(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    token = "synthetic-verify-token-0123456789abcdef"
    query = {"hub.mode": "subscribe", "hub.verify_token": token, "hub.challenge": "1158201444"}
    # Unset: always 403.
    monkeypatch.setattr(guards, "get_settings", lambda: Settings(env=Environment.CI))
    assert client.get("/api/v1/webhooks/meta", params=query).status_code == 403
    configured = Settings(env=Environment.CI, meta_webhook_verify_token=SecretStr(token))
    monkeypatch.setattr(guards, "get_settings", lambda: configured)
    ok = client.get("/api/v1/webhooks/meta", params=query)
    assert ok.status_code == 200
    assert ok.json() == 1158201444
    for bad in (
        {**query, "hub.verify_token": token + "x"},
        {**query, "hub.mode": "unsubscribe"},
        {**query, "hub.challenge": "<script>"},
        {"hub.mode": "subscribe", "hub.challenge": "1"},
    ):
        assert client.get("/api/v1/webhooks/meta", params=bad).status_code == 403
