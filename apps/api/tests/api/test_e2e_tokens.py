"""End to end through real token checks: BFF service token + OIDC access token (JWKS over
httpx.MockTransport) -> principal -> membership resolution -> require() (SEC-003..005, FR-IAM-001,
FR-IAM-002). Synthetic keys and identities only."""

from __future__ import annotations

import json
import sys
import time
import uuid
from collections.abc import Iterator
from typing import Any

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from jwt.algorithms import RSAAlgorithm
from sqlalchemy import Engine

from app.authz.kv import InMemoryKV, set_kv_store
from app.identity.service_token import (
    SERVICE_TOKEN_HEADER,
    InMemoryReplayStore,
    ServiceTokenVerifier,
    get_service_token_verifier,
)
from app.identity.tokens import TokenVerifier, get_tenant_token_verifier
from app.main import create_app

pytestmark = pytest.mark.db
W = sys.modules["sos_test_api_world"]

ISSUER = "https://cognito-idp.ap-south-1.amazonaws.com/ap-south-1_SYNTHE2E"
CLIENT = "synthe2eclient"
KID = "e2e-1"
SERVICE_KEY = "synthetic-e2e-service-token-key-0123456789"


class Idp:
    def __init__(self) -> None:
        self.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        jwk = json.loads(RSAAlgorithm.to_jwk(self.key.public_key()))
        jwk.update({"kid": KID, "alg": "RS256", "use": "sig"})
        self.jwk = jwk

    def handler(self, request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/.well-known/openid-configuration"):
            return httpx.Response(200, json={"issuer": ISSUER, "jwks_uri": ISSUER + "/jwks"})
        return httpx.Response(200, json={"keys": [self.jwk]})

    def mint(self, subject: str, *, auth_age: int = 30, mfa: bool = True) -> str:
        now = int(time.time())
        claims: dict[str, Any] = {
            "iss": ISSUER,
            "sub": subject,
            "client_id": CLIENT,
            "token_use": "access",
            "iat": now,
            "exp": now + 600,
            "auth_time": now - auth_age,
            "origin_jti": "synthetic-e2e-session",
        }
        if mfa:
            claims["sos:mfa"] = "true"
        return jwt.encode(claims, self.key, algorithm="RS256", headers={"kid": KID})


@pytest.fixture
def e2e(app_engine: Engine, platform_engine: Engine) -> Iterator[tuple[TestClient, Idp, Any]]:
    idp = Idp()
    service = ServiceTokenVerifier(SERVICE_KEY, replay_store=InMemoryReplayStore())
    verifier = TokenVerifier(
        issuer=ISSUER,
        audience=CLIENT,
        production_like=False,
        http_client=httpx.Client(transport=httpx.MockTransport(idp.handler)),
    )
    set_kv_store(InMemoryKV())
    app = create_app()
    app.dependency_overrides[get_tenant_token_verifier] = lambda: verifier
    app.dependency_overrides[get_service_token_verifier] = lambda: service
    with TestClient(app) as client:
        yield client, idp, service
    set_kv_store(None)


def _headers(service: ServiceTokenVerifier, token: str) -> dict[str, str]:
    return {SERVICE_TOKEN_HEADER: service.issue(), "Authorization": f"Bearer {token}"}


def test_SEC_003_real_tokens_end_to_end(
    world: Any, e2e: tuple[TestClient, Idp, Any], admin_engine: Engine
) -> None:
    client, idp, service = e2e
    owner = world.person("owner")
    res = client.get("/api/v1/me", headers=_headers(service, idp.mint(owner.subject)))
    assert res.status_code == 200, res.text
    assert res.json()["user_id"] == str(owner.user_id)

    # Step-up (SEC-005): stale auth_time -> 428, fresh -> 201.
    body = {
        "idp_subject": f"sub-{uuid.uuid4().hex}",
        "display_name": "Synthetic E2E Invitee",
        "roles": ["teacher"],
    }
    stale = client.post(
        "/api/v1/users", json=body, headers=_headers(service, idp.mint(owner.subject, auth_age=900))
    )
    assert stale.status_code == 428
    assert stale.json()["code"] == "step_up_required"
    fresh = client.post(
        "/api/v1/users", json=body, headers=_headers(service, idp.mint(owner.subject))
    )
    assert fresh.status_code == 201, fresh.text

    # FR-IAM-002: a privileged role without the MFA claim is refused.
    no_mfa = client.get("/api/v1/me", headers=_headers(service, idp.mint(owner.subject, mfa=False)))
    assert no_mfa.status_code == 403
    assert no_mfa.json()["code"] == "mfa_required"

    # Permission denial with real tokens.
    staff = world.person("office_staff")
    denied = client.get("/api/v1/users", headers=_headers(service, idp.mint(staff.subject)))
    assert denied.status_code == 403

    # Missing service token -> 401 before any membership lookup.
    anon = client.get("/api/v1/me", headers={"Authorization": f"Bearer {idp.mint(owner.subject)}"})
    assert anon.status_code == 401
