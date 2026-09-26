"""FastAPI authentication dependencies (FR-IAM-001, FR-IAM-003, FR-IAM-002, SEC-004, SEC-005).

End-to-end through a tiny app: real RSA keys, JWKS served by ``httpx.MockTransport``, real
service-token verifier with an in-memory replay store. Everything is synthetic.
"""

from __future__ import annotations

import json
import time
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any
from uuid import UUID

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from jwt.algorithms import RSAAlgorithm

from app.core.errors import StepUpRequired, install_error_handlers
from app.identity.principal import (
    Principal,
    PrincipalResolver,
    get_operator_principal,
    get_principal,
    recent_auth,
    require_recent_auth,
)
from app.identity.service_token import (
    SERVICE_TOKEN_HEADER,
    InMemoryReplayStore,
    ServiceTokenVerifier,
    get_service_token_verifier,
)
from app.identity.tokens import (
    TokenVerifier,
    get_platform_token_verifier,
    get_tenant_token_verifier,
)

TENANT_ISSUER = "https://cognito-idp.ap-south-1.amazonaws.com/ap-south-1_SYNTHUSERS"
PLATFORM_ISSUER = "https://cognito-idp.ap-south-1.amazonaws.com/ap-south-1_SYNTHOPS"
TENANT_CLIENT = "synthwebclient"
PLATFORM_CLIENT = "synthopsclient"
SERVICE_KEY = "synthetic-service-token-key-0123456789abcdef"
SUBJECT = "00000000-0000-4000-8000-00000000abcd"


class Idp:
    def __init__(self, issuer: str, kid: str) -> None:
        self.issuer = issuer
        self.kid = kid
        self.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        jwk = json.loads(RSAAlgorithm.to_jwk(self.key.public_key()))
        jwk.update({"kid": kid, "alg": "RS256", "use": "sig"})
        self.jwk = jwk
        self.down = False

    def handler(self, request: httpx.Request) -> httpx.Response:
        if self.down:
            return httpx.Response(503, text="synthetic idp outage at idp-internal.example")
        if request.url.path.endswith("/.well-known/openid-configuration"):
            return httpx.Response(
                200, json={"issuer": self.issuer, "jwks_uri": self.issuer + "/jwks"}
            )
        return httpx.Response(200, json={"keys": [self.jwk]})

    def verifier(self, audience: str) -> TokenVerifier:
        return TokenVerifier(
            issuer=self.issuer,
            audience=audience,
            production_like=False,
            http_client=httpx.Client(transport=httpx.MockTransport(self.handler)),
        )

    def mint(self, client_id: str, **overrides: Any) -> str:
        now = int(time.time())
        claims: dict[str, Any] = {
            "iss": self.issuer,
            "sub": SUBJECT,
            "client_id": client_id,
            "token_use": "access",
            "iat": now,
            "exp": now + 600,
            "auth_time": now - 30,
            "origin_jti": "synthetic-origin",
            "sos:mfa": "true",
        }
        for name, value in overrides.items():
            if value is None:
                claims.pop(name, None)
            else:
                claims[name] = value
        return jwt.encode(claims, self.key, algorithm="RS256", headers={"kid": self.kid})


@pytest.fixture(scope="module")
def tenant_idp() -> Idp:
    return Idp(TENANT_ISSUER, "users-1")


@pytest.fixture(scope="module")
def platform_idp() -> Idp:
    return Idp(PLATFORM_ISSUER, "ops-1")


@pytest.fixture
def service() -> ServiceTokenVerifier:
    return ServiceTokenVerifier(SERVICE_KEY, replay_store=InMemoryReplayStore())


@pytest.fixture
def client(
    tenant_idp: Idp, platform_idp: Idp, service: ServiceTokenVerifier
) -> Iterator[TestClient]:
    tenant_idp.down = False
    app = FastAPI()
    install_error_handlers(app)

    @app.get("/who")
    def who(p: Annotated[Principal, Depends(get_principal)]) -> dict[str, Any]:
        return {"subject": p.subject, "kind": p.kind, "mfa": p.mfa, "session": p.session_id}

    @app.get("/ops")
    def ops(p: Annotated[Principal, Depends(get_operator_principal)]) -> dict[str, Any]:
        return {"subject": p.subject, "kind": p.kind}

    @app.post("/sensitive")
    def sensitive(p: Annotated[Principal, Depends(recent_auth())]) -> dict[str, Any]:
        return {"subject": p.subject}

    tenant_verifier = tenant_idp.verifier(TENANT_CLIENT)
    platform_verifier = platform_idp.verifier(PLATFORM_CLIENT)
    app.dependency_overrides[get_tenant_token_verifier] = lambda: tenant_verifier
    app.dependency_overrides[get_platform_token_verifier] = lambda: platform_verifier
    app.dependency_overrides[get_service_token_verifier] = lambda: service
    with TestClient(app) as c:
        yield c


def headers(service: ServiceTokenVerifier, access: str | None) -> dict[str, str]:
    h = {SERVICE_TOKEN_HEADER: service.issue()}
    if access is not None:
        h["Authorization"] = f"Bearer {access}"
    return h


def assert_problem(res: httpx.Response, status: int, code: str) -> None:
    assert res.status_code == status, res.text
    assert res.headers["content-type"].startswith("application/problem+json")
    assert res.json()["code"] == code


# --------------------------------------------------------------------------- user principal
def test_FR_IAM_001_valid_headers_yield_user_principal(
    client: TestClient, service: ServiceTokenVerifier, tenant_idp: Idp
) -> None:
    res = client.get("/who", headers=headers(service, tenant_idp.mint(TENANT_CLIENT)))
    assert res.status_code == 200, res.text
    assert res.json() == {
        "subject": SUBJECT,
        "kind": "user",
        "mfa": True,
        "session": "synthetic-origin",
    }


def test_SEC_004_missing_service_token_is_401_problem(client: TestClient, tenant_idp: Idp) -> None:
    res = client.get("/who", headers={"Authorization": f"Bearer {tenant_idp.mint(TENANT_CLIENT)}"})
    assert_problem(res, 401, "unauthenticated")


def test_FR_IAM_001_missing_bearer_token_is_401_problem(
    client: TestClient, service: ServiceTokenVerifier
) -> None:
    assert_problem(client.get("/who", headers=headers(service, None)), 401, "unauthenticated")


@pytest.mark.parametrize(
    "authorization",
    ["Basic dXNlcjpwYXNz", "Bearer", "Bearer ", "Token abc", "Bearer a b", "bearer"],
)
def test_FR_IAM_001_malformed_authorization_header_is_401(
    client: TestClient, service: ServiceTokenVerifier, authorization: str
) -> None:
    h = {SERVICE_TOKEN_HEADER: service.issue(), "Authorization": authorization}
    assert_problem(client.get("/who", headers=h), 401, "unauthenticated")


def test_FR_IAM_001_bearer_scheme_is_case_insensitive(
    client: TestClient, service: ServiceTokenVerifier, tenant_idp: Idp
) -> None:
    h = {
        SERVICE_TOKEN_HEADER: service.issue(),
        "Authorization": f"bearer {tenant_idp.mint(TENANT_CLIENT)}",
    }
    assert client.get("/who", headers=h).status_code == 200


def test_SEC_004_invalid_service_token_is_401(client: TestClient, tenant_idp: Idp) -> None:
    other = ServiceTokenVerifier("x" * 40)
    h = {
        SERVICE_TOKEN_HEADER: other.issue(),
        "Authorization": f"Bearer {tenant_idp.mint(TENANT_CLIENT)}",
    }
    assert_problem(client.get("/who", headers=h), 401, "unauthenticated")


def test_SEC_004_replayed_service_token_is_401(
    client: TestClient, service: ServiceTokenVerifier, tenant_idp: Idp
) -> None:
    h = headers(service, tenant_idp.mint(TENANT_CLIENT))
    assert client.get("/who", headers=h).status_code == 200
    assert_problem(client.get("/who", headers=h), 401, "unauthenticated")


def test_FR_IAM_004_expired_access_token_is_401_token_expired(
    client: TestClient, service: ServiceTokenVerifier, tenant_idp: Idp
) -> None:
    now = int(time.time())
    token = tenant_idp.mint(TENANT_CLIENT, iat=now - 700, exp=now - 100)
    assert_problem(client.get("/who", headers=headers(service, token)), 401, "token_expired")


def test_SEC_004_operator_token_is_rejected_on_tenant_routes(
    client: TestClient, service: ServiceTokenVerifier, platform_idp: Idp
) -> None:
    token = platform_idp.mint(PLATFORM_CLIENT)
    assert_problem(client.get("/who", headers=headers(service, token)), 401, "unauthenticated")


def test_SEC_004_idp_outage_is_503_without_details(
    client: TestClient, service: ServiceTokenVerifier, tenant_idp: Idp
) -> None:
    tenant_idp.down = True
    try:
        res = client.get("/who", headers=headers(service, tenant_idp.mint(TENANT_CLIENT)))
    finally:
        tenant_idp.down = False
    assert_problem(res, 503, "service_unavailable")
    assert "idp-internal" not in res.text
    assert "cognito" not in res.text.lower()


def test_SEC_004_error_body_never_echoes_the_token(
    client: TestClient, service: ServiceTokenVerifier, tenant_idp: Idp
) -> None:
    token = tenant_idp.mint("wrongclient")
    res = client.get("/who", headers=headers(service, token))
    assert res.status_code == 401
    assert token not in res.text
    assert token.split(".")[1] not in res.text


# --------------------------------------------------------------------------- operator principal
def test_FR_IAM_002_operator_with_mfa_yields_operator_principal(
    client: TestClient, service: ServiceTokenVerifier, platform_idp: Idp
) -> None:
    res = client.get("/ops", headers=headers(service, platform_idp.mint(PLATFORM_CLIENT)))
    assert res.status_code == 200, res.text
    assert res.json() == {"subject": SUBJECT, "kind": "operator"}


def test_FR_IAM_002_operator_without_mfa_is_403(
    client: TestClient, service: ServiceTokenVerifier, platform_idp: Idp
) -> None:
    token = platform_idp.mint(PLATFORM_CLIENT, **{"sos:mfa": None})
    assert_problem(client.get("/ops", headers=headers(service, token)), 403, "mfa_required")


def test_SEC_004_user_token_is_rejected_on_operator_routes(
    client: TestClient, service: ServiceTokenVerifier, tenant_idp: Idp
) -> None:
    token = tenant_idp.mint(TENANT_CLIENT)
    assert_problem(client.get("/ops", headers=headers(service, token)), 401, "unauthenticated")


def test_SEC_004_operator_route_also_requires_service_token(
    client: TestClient, platform_idp: Idp
) -> None:
    h = {"Authorization": f"Bearer {platform_idp.mint(PLATFORM_CLIENT)}"}
    assert_problem(client.get("/ops", headers=h), 401, "unauthenticated")


# --------------------------------------------------------------------------- step-up
def principal(*, auth_time: datetime | None, mfa: bool = True, kind: str = "user") -> Principal:
    return Principal(
        subject=SUBJECT,
        issuer=TENANT_ISSUER,
        kind=kind,  # type: ignore[arg-type]
        auth_time=auth_time,
        mfa=mfa,
        session_id=None,
        expires_at=datetime.now(UTC) + timedelta(minutes=5),
    )


NOW = datetime(2026, 9, 26, 10, 0, tzinfo=UTC)


def test_SEC_005_recent_mfa_auth_passes_step_up() -> None:
    require_recent_auth(principal(auth_time=NOW - timedelta(minutes=4)), now=NOW)


def test_SEC_005_auth_older_than_5_minutes_requires_step_up() -> None:
    with pytest.raises(StepUpRequired):
        require_recent_auth(principal(auth_time=NOW - timedelta(minutes=5, seconds=1)), now=NOW)


def test_SEC_005_missing_auth_time_requires_step_up() -> None:
    with pytest.raises(StepUpRequired):
        require_recent_auth(principal(auth_time=None), now=NOW)


def test_SEC_005_recent_auth_without_mfa_requires_step_up() -> None:
    with pytest.raises(StepUpRequired):
        require_recent_auth(principal(auth_time=NOW, mfa=False), now=NOW)


def test_SEC_005_future_auth_time_requires_step_up() -> None:
    with pytest.raises(StepUpRequired):
        require_recent_auth(principal(auth_time=NOW + timedelta(minutes=2)), now=NOW)


def test_SEC_005_custom_max_age_is_honoured() -> None:
    p = principal(auth_time=NOW - timedelta(minutes=8))
    require_recent_auth(p, max_age=timedelta(minutes=10), now=NOW)
    with pytest.raises(StepUpRequired):
        require_recent_auth(p, max_age=timedelta(minutes=1), now=NOW)


def test_SEC_005_step_up_dependency_returns_428(
    client: TestClient, service: ServiceTokenVerifier, tenant_idp: Idp
) -> None:
    now = int(time.time())
    stale = tenant_idp.mint(TENANT_CLIENT, auth_time=now - 600)
    assert_problem(
        client.post("/sensitive", headers=headers(service, stale)), 428, "step_up_required"
    )
    no_mfa = tenant_idp.mint(TENANT_CLIENT, **{"sos:mfa": None})
    assert_problem(
        client.post("/sensitive", headers=headers(service, no_mfa)), 428, "step_up_required"
    )
    fresh = tenant_idp.mint(TENANT_CLIENT, auth_time=now - 10)
    assert client.post("/sensitive", headers=headers(service, fresh)).status_code == 200


def test_SEC_005_step_up_dependency_still_requires_authentication(client: TestClient) -> None:
    assert_problem(client.post("/sensitive"), 401, "unauthenticated")


# --------------------------------------------------------------------------- resolver protocol
def test_FR_IAM_001_principal_resolver_protocol_is_structural() -> None:
    class Resolver:
        def resolve(self, principal: Principal, *, tenant_hint: UUID | None) -> str:
            return f"{principal.kind}:{tenant_hint}"

    resolver: PrincipalResolver[str] = Resolver()
    assert resolver.resolve(principal(auth_time=NOW), tenant_hint=None) == "user:None"


def test_SEC_004_principal_repr_is_safe() -> None:
    text = repr(principal(auth_time=NOW))
    assert SUBJECT in text  # opaque IdP subject is an ID, not personal data
