"""Break-glass support sign-in: token confusion matrix and settings guards (ADR-0023 option C;
SEC-004, SEC-005, SEC-021, FR-IAM-001, FR-IAM-002; threats T1/T2).

Two pools: the staff pool (staff app client) and the operator pool with TWO app clients (the
operator admin client and the dedicated support client). Tenant routes accept staff tokens and
support-client tokens; the operator admin client is never accepted there, and the support client
is never accepted on operator routes. Real RSA keys, JWKS through ``httpx.MockTransport``, all
synthetic.
"""

from __future__ import annotations

import json
import time
from collections.abc import Iterator
from typing import Annotated, Any

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from jwt.algorithms import RSAAlgorithm

from app.core.config import DeploymentMode, Environment, KeyWrapperKind, Settings
from app.core.errors import install_error_handlers
from app.identity.principal import Principal, get_operator_principal, get_principal
from app.identity.service_token import (
    SERVICE_TOKEN_HEADER,
    InMemoryReplayStore,
    ServiceTokenVerifier,
    get_service_token_verifier,
)
from app.identity.tokens import (
    TokenVerifier,
    build_platform_verifier,
    build_support_verifier,
    build_tenant_verifier,
    get_platform_token_verifier,
    get_support_token_verifier,
    get_tenant_token_verifier,
    unverified_issuer,
)

STAFF_ISSUER = "https://cognito-idp.ap-south-1.amazonaws.com/ap-south-1_SYNTHSTAFF"
OPS_ISSUER = "https://cognito-idp.ap-south-1.amazonaws.com/ap-south-1_SYNTHOPS"
STAFF_CLIENT = "synthstaffclient"
ADMIN_CLIENT = "synthadminclient"
SUPPORT_CLIENT = "synthsupportclient"
SERVICE_KEY = "synthetic-service-token-key-0123456789abcdef"
SUBJECT = "00000000-0000-4000-8000-0000000051a1"


class Idp:
    def __init__(self, issuer: str, kid: str) -> None:
        self.issuer = issuer
        self.kid = kid
        self.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        jwk = json.loads(RSAAlgorithm.to_jwk(self.key.public_key()))
        jwk.update({"kid": kid, "alg": "RS256", "use": "sig"})
        self.jwk = jwk

    def handler(self, request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/.well-known/openid-configuration"):
            return httpx.Response(
                200, json={"issuer": self.issuer, "jwks_uri": self.issuer + "/jwks"}
            )
        return httpx.Response(200, json={"keys": [self.jwk]})

    def verifier(self, audience: str, **kwargs: Any) -> TokenVerifier:
        return TokenVerifier(
            issuer=self.issuer,
            audience=audience,
            production_like=False,
            http_client=httpx.Client(transport=httpx.MockTransport(self.handler)),
            **kwargs,
        )

    def mint(self, client: str | None, **overrides: Any) -> str:
        now = int(time.time())
        claims: dict[str, Any] = {
            "iss": self.issuer,
            "sub": SUBJECT,
            "client_id": client,
            "token_use": "access",
            "iat": now,
            "exp": now + 600,
            "auth_time": now - 30,
            "origin_jti": "synthetic-support-session",
            "sos:mfa": "true",
        }
        for name, value in overrides.items():
            if value is None:
                claims.pop(name, None)
            else:
                claims[name] = value
        claims = {k: v for k, v in claims.items() if v is not None}
        return jwt.encode(claims, self.key, algorithm="RS256", headers={"kid": self.kid})


@pytest.fixture(scope="module")
def staff_idp() -> Idp:
    return Idp(STAFF_ISSUER, "staff-1")


@pytest.fixture(scope="module")
def ops_idp() -> Idp:
    return Idp(OPS_ISSUER, "ops-1")


@pytest.fixture
def service() -> ServiceTokenVerifier:
    return ServiceTokenVerifier(SERVICE_KEY, replay_store=InMemoryReplayStore())


def _app(
    staff_idp: Idp, ops_idp: Idp, service: ServiceTokenVerifier, *, support_enabled: bool
) -> FastAPI:
    app = FastAPI()
    install_error_handlers(app)

    @app.get("/tenant")
    def tenant(p: Annotated[Principal, Depends(get_principal)]) -> dict[str, Any]:
        return {"kind": p.kind, "issuer": p.issuer, "mfa": p.mfa}

    @app.get("/platform")
    def platform(p: Annotated[Principal, Depends(get_operator_principal)]) -> dict[str, Any]:
        return {"kind": p.kind}

    everyone = frozenset({STAFF_CLIENT, ADMIN_CLIENT, SUPPORT_CLIENT})
    # Mirrors build_*_verifier: each verifier refuses the other clients' IDs.
    staff_v = staff_idp.verifier(STAFF_CLIENT, forbidden_audiences=everyone)
    admin_v = ops_idp.verifier(ADMIN_CLIENT, forbidden_audiences=everyone)
    support_v = ops_idp.verifier(
        SUPPORT_CLIENT, forbidden_audiences=everyone, require_client_id=True
    )
    app.dependency_overrides[get_tenant_token_verifier] = lambda: staff_v
    app.dependency_overrides[get_platform_token_verifier] = lambda: admin_v
    app.dependency_overrides[get_support_token_verifier] = lambda: (
        support_v if support_enabled else None
    )
    app.dependency_overrides[get_service_token_verifier] = lambda: service
    return app


@pytest.fixture
def client(staff_idp: Idp, ops_idp: Idp, service: ServiceTokenVerifier) -> Iterator[TestClient]:
    with TestClient(_app(staff_idp, ops_idp, service, support_enabled=True)) as c:
        yield c


def _h(service: ServiceTokenVerifier, token: str) -> dict[str, str]:
    return {SERVICE_TOKEN_HEADER: service.issue(), "Authorization": f"Bearer {token}"}


def _code(res: httpx.Response) -> str | None:
    if res.status_code == 200:
        return None
    body = res.json()
    return str(body.get("code"))


# --- the matrix: issuer x client x route family ---------------------------------------------


# (pool, client_id, status on tenant routes, principal kind there, status on operator routes)
Case = tuple[str, str, int, str | None, int]


@pytest.mark.parametrize(
    "case",
    [
        ("staff", STAFF_CLIENT, 200, "user", 401),
        ("ops", ADMIN_CLIENT, 401, None, 200),
        ("ops", SUPPORT_CLIENT, 200, "support", 401),
        ("staff", SUPPORT_CLIENT, 401, None, 401),  # support client ID in the wrong pool
        ("staff", ADMIN_CLIENT, 401, None, 401),
        ("ops", STAFF_CLIENT, 401, None, 401),
        ("ops", "someotherclient", 401, None, 401),
    ],
)
def test_ADR_0023_token_confusion_matrix(
    client: TestClient, service: ServiceTokenVerifier, staff_idp: Idp, ops_idp: Idp, case: Case
) -> None:
    pool, client_id, tenant_status, tenant_kind, platform_status = case
    idp = staff_idp if pool == "staff" else ops_idp
    token = idp.mint(client_id)
    res = client.get("/tenant", headers=_h(service, token))
    assert res.status_code == tenant_status, res.text
    if tenant_kind is not None:
        assert res.json()["kind"] == tenant_kind
    assert client.get("/platform", headers=_h(service, token)).status_code == platform_status


def test_ADR_0023_support_token_needs_mfa(
    client: TestClient, service: ServiceTokenVerifier, ops_idp: Idp
) -> None:
    token = ops_idp.mint(SUPPORT_CLIENT, **{"sos:mfa": None})
    res = client.get("/tenant", headers=_h(service, token))
    assert res.status_code == 403
    assert _code(res) == "mfa_required"


@pytest.mark.parametrize(
    "overrides",
    [
        {"token_use": "id"},  # a Cognito ID token of the support client
        {"token_use": None},  # not a Cognito access token
        {"client_id": None, "aud": SUPPORT_CLIENT},  # audience only, no client_id
        {"aud": [SUPPORT_CLIENT, ADMIN_CLIENT]},  # also names the admin client
        {"aud": [SUPPORT_CLIENT, STAFF_CLIENT]},
    ],
)
def test_ADR_0023_support_token_shape_is_strict(
    client: TestClient, service: ServiceTokenVerifier, ops_idp: Idp, overrides: dict[str, Any]
) -> None:
    token = ops_idp.mint(SUPPORT_CLIENT, **overrides)
    assert client.get("/tenant", headers=_h(service, token)).status_code == 401


def test_ADR_0023_admin_token_naming_support_audience_is_refused_everywhere(
    client: TestClient, service: ServiceTokenVerifier, ops_idp: Idp
) -> None:
    token = ops_idp.mint(ADMIN_CLIENT, aud=[ADMIN_CLIENT, SUPPORT_CLIENT])
    assert client.get("/platform", headers=_h(service, token)).status_code == 401
    assert client.get("/tenant", headers=_h(service, token)).status_code == 401


def test_ADR_0023_forged_issuer_only_selects_a_verifier_that_refuses(
    client: TestClient, service: ServiceTokenVerifier, staff_idp: Idp
) -> None:
    # Signed by the STAFF pool key but claiming the operator issuer and the support client.
    token = staff_idp.mint(SUPPORT_CLIENT, iss=OPS_ISSUER)
    assert unverified_issuer(token) == OPS_ISSUER
    assert client.get("/tenant", headers=_h(service, token)).status_code == 401


def test_ADR_0023_support_sign_in_off_fails_closed(
    staff_idp: Idp, ops_idp: Idp, service: ServiceTokenVerifier
) -> None:
    with TestClient(_app(staff_idp, ops_idp, service, support_enabled=False)) as c:
        token = ops_idp.mint(SUPPORT_CLIENT)
        assert c.get("/tenant", headers=_h(service, token)).status_code == 401
        staff = staff_idp.mint(STAFF_CLIENT)
        assert c.get("/tenant", headers=_h(service, staff)).json()["kind"] == "user"


def test_SEC_002_the_unverified_issuer_never_replaces_signature_checks(
    client: TestClient, service: ServiceTokenVerifier, ops_idp: Idp
) -> None:
    """``unverified_issuer`` decodes without the signature (semgrep ``unverified-jwt-decode``,
    suppressed with a reason in tokens.py): the verifier it selects must verify the token again,
    so a real support token with a broken signature, or the same claims unsigned, is refused."""
    token = ops_idp.mint(SUPPORT_CLIENT)
    assert client.get("/tenant", headers=_h(service, token)).status_code == 200
    head, body, signature = token.split(".")
    flipped = ("A" if signature[0] != "A" else "B") + signature[1:]
    for forged in (f"{head}.{body}.{flipped}", f"{head}.{body}."):
        assert unverified_issuer(forged) == OPS_ISSUER
        assert client.get("/tenant", headers=_h(service, forged)).status_code == 401


@pytest.mark.parametrize("junk", ["", "a.b", "not-a-jwt", "x" * 9000])
def test_ADR_0023_unverified_issuer_of_junk_is_none(junk: str) -> None:
    assert unverified_issuer(junk) is None


# --- settings and verifier factories ---------------------------------------------------------


def _settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "env": Environment.LOCAL,
        "oidc_issuer": STAFF_ISSUER,
        "oidc_audience": STAFF_CLIENT,
        "platform_oidc_issuer": OPS_ISSUER,
        "platform_oidc_audience": ADMIN_CLIENT,
    }
    values.update(overrides)
    return Settings(**values)


def test_ADR_0023_support_client_is_off_by_default() -> None:
    settings = _settings()
    assert settings.support_enabled is False
    assert build_support_verifier(settings) is None


def test_ADR_0023_support_verifier_uses_operator_pool_and_support_client() -> None:
    settings = _settings(support_oidc_audience=SUPPORT_CLIENT)
    verifier = build_support_verifier(settings)
    assert verifier is not None
    assert (verifier.issuer, verifier.audience) == (OPS_ISSUER, SUPPORT_CLIENT)
    # The other clients are refused by every verifier.
    assert SUPPORT_CLIENT in build_platform_verifier(settings)._forbidden
    assert SUPPORT_CLIENT in build_tenant_verifier(settings)._forbidden
    assert {STAFF_CLIENT, ADMIN_CLIENT} <= verifier._forbidden


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"support_oidc_audience": ADMIN_CLIENT}, "dedicated app client"),
        ({"support_oidc_audience": STAFF_CLIENT}, "dedicated app client"),
        (
            {"support_oidc_audience": SUPPORT_CLIENT, "support_oidc_issuer": STAFF_ISSUER},
            "must not be the staff issuer",
        ),
        (
            {
                "support_oidc_audience": SUPPORT_CLIENT,
                "support_oidc_issuer": "https://idp.synthetic.test/elsewhere",
            },
            "operator pool issuer",
        ),
    ],
)
def test_ADR_0023_settings_refuse_confusable_support_client(
    overrides: dict[str, Any], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        _settings(**overrides)


def test_ADR_0023_dedicated_host_may_name_its_support_issuer() -> None:
    settings = _settings(
        deployment_mode=DeploymentMode.DEDICATED,
        support_oidc_audience=SUPPORT_CLIENT,
        support_oidc_issuer="https://idp.synthetic.test/operator-pool",
    )
    assert settings.resolved_support_issuer == "https://idp.synthetic.test/operator-pool"


@pytest.mark.parametrize(
    "issuer", ["http://localhost:8080/platform", "http://oidc.localhost:8080/platform"]
)
def test_ADR_0023_staging_refuses_dev_support_issuer(issuer: str) -> None:
    with pytest.raises(ValueError, match="SOS_SUPPORT_OIDC_ISSUER"):
        Settings(
            env=Environment.STAGING,
            deployment_mode=DeploymentMode.DEDICATED,
            key_wrapper=KeyWrapperKind.KMS,
            database_url="postgresql+psycopg://sos_app:x@db:5432/schoolos?sslmode=verify-full",
            platform_database_url="postgresql+psycopg://sos_platform:y@db:5432/schoolos?sslmode=verify-full",
            service_token_key="k" * 48,
            oidc_issuer=STAFF_ISSUER,
            oidc_audience=STAFF_CLIENT,
            platform_oidc_issuer=OPS_ISSUER,
            platform_oidc_audience=ADMIN_CLIENT,
            support_oidc_audience=SUPPORT_CLIENT,
            support_oidc_issuer=issuer,
        )
