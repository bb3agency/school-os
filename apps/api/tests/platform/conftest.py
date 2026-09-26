"""Fixtures for control-plane API tests: synthetic IdPs, operators per role, a wired app.

Everything is synthetic. Two IdPs (operator pool, staff pool) serve JWKS via httpx.MockTransport;
the real FastAPI app is used with its token/service-token/key-wrapper/store dependencies
overridden. Operators are created directly with ``sos_platform`` (the control-plane role).
"""

from __future__ import annotations

import json
import secrets
import time
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from jwt.algorithms import RSAAlgorithm
from pydantic import SecretStr
from sqlalchemy import Engine, text

from app.authz.kv import InMemoryKV
from app.core.config import Environment, KeyWrapperKind, Settings
from app.core.crypto import LocalDevKeyWrapper
from app.core.db import platform_session
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
from app.main import create_app
from app.ops.idempotency import KVIdempotencyStore
from app.platform import api as platform_api
from app.platform import fleet
from app.platform.common import Actor
from app.platform.schemas import PlanIn

PLATFORM_ISSUER = "https://cognito-idp.ap-south-1.amazonaws.com/ap-south-1_SYNTHOPS"
TENANT_ISSUER = "https://cognito-idp.ap-south-1.amazonaws.com/ap-south-1_SYNTHUSERS"
PLATFORM_CLIENT = "synthopsclient"
TENANT_CLIENT = "synthwebclient"
SERVICE_KEY = "synthetic-service-token-key-0123456789abcdef"


def letters(n: int) -> str:
    """Random lowercase letters (audit summaries reject long digit runs in codes)."""
    return "".join(secrets.choice("abcdefghijkmnopqrstuvwxyz") for _ in range(n))


ROLES = ("platform_owner", "platform_engineer", "support_agent", "billing_admin", "platform_viewer")


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

    def verifier(self, audience: str) -> TokenVerifier:
        return TokenVerifier(
            issuer=self.issuer,
            audience=audience,
            production_like=False,
            http_client=httpx.Client(transport=httpx.MockTransport(self.handler)),
        )

    def mint(self, client_id: str, subject: str, *, auth_age: int = 30, mfa: bool = True) -> str:
        now = int(time.time())
        claims: dict[str, Any] = {
            "iss": self.issuer,
            "sub": subject,
            "client_id": client_id,
            "token_use": "access",
            "iat": now,
            "exp": now + 600,
            "auth_time": now - auth_age,
        }
        if mfa:
            claims["sos:mfa"] = "true"
        return jwt.encode(claims, self.key, algorithm="RS256", headers={"kid": self.kid})


@pytest.fixture(scope="session")
def platform_idp() -> Idp:
    return Idp(PLATFORM_ISSUER, "ops-1")


@pytest.fixture(scope="session")
def tenant_idp() -> Idp:
    return Idp(TENANT_ISSUER, "users-1")


@pytest.fixture(scope="session")
def wrapper() -> LocalDevKeyWrapper:
    return LocalDevKeyWrapper(
        Settings(
            env=Environment.CI,
            key_wrapper=KeyWrapperKind.LOCAL_DEV,
            local_dev_master_key=SecretStr("synthetic-ci-master-key-0123456789abcdef"),
        )
    )


@pytest.fixture
def fleet_stores() -> fleet.FleetStores:
    return fleet.FleetStores(InMemoryReplayStore(), InMemoryReplayStore())


@dataclass
class Operator:
    id: uuid.UUID
    subject: str
    roles: tuple[str, ...]

    @property
    def actor(self) -> Actor:
        return Actor(self.id)


MakeOperator = Callable[..., Operator]


@pytest.fixture
def make_operator(platform_engine: Engine, app_engine: Engine) -> MakeOperator:
    def _make(*roles: str, status: str = "active") -> Operator:
        oid = uuid.uuid4()
        subject = f"op-sub-{oid}"
        with platform_session() as s:
            s.execute(
                text(
                    "INSERT INTO platform.operators (id, idp_subject, email, display_name, status, "
                    "mfa_enrolled, deactivated_at) VALUES (:i, :s, :e, 'Synthetic Operator', :st, "
                    ":mfa, CASE WHEN :st = 'deactivated' THEN now() END)"
                ),
                {
                    "i": oid,
                    "s": subject,
                    "e": f"op-{oid.hex[:12]}@example.test",
                    "st": status,
                    "mfa": status != "invited",
                },
            )
            for role in roles:
                s.execute(
                    text(
                        "INSERT INTO platform.operator_roles (operator_id, role_key) "
                        "VALUES (:o, :r)"
                    ),
                    {"o": oid, "r": role},
                )
        return Operator(oid, subject, tuple(roles))

    return _make


@dataclass
class Api:
    client: TestClient
    service: ServiceTokenVerifier
    platform_idp: Idp
    tenant_idp: Idp

    def headers(
        self,
        op: Operator | None,
        *,
        fresh: bool = True,
        idem: str | None = None,
        tenant_subject: str | None = None,
        extra: dict[str, str] | None = None,
    ) -> dict[str, str]:
        h = {SERVICE_TOKEN_HEADER: self.service.issue()}
        if op is not None:
            token = self.platform_idp.mint(
                PLATFORM_CLIENT, op.subject, auth_age=30 if fresh else 900
            )
            h["Authorization"] = f"Bearer {token}"
        if tenant_subject is not None:
            h["Authorization"] = f"Bearer {self.tenant_idp.mint(TENANT_CLIENT, tenant_subject)}"
        if idem is not None:
            h["Idempotency-Key"] = idem
        if extra:
            h.update(extra)
        return h

    def call(
        self,
        method: str,
        path: str,
        op: Operator | None,
        *,
        json: Any = None,
        fresh: bool = True,
        idem: str | None = "auto",
        **kw: Any,
    ) -> httpx.Response:
        key = None
        if method.upper() == "POST" and idem == "auto":
            key = f"idem-{uuid.uuid4().hex}"
        elif idem != "auto":
            key = idem
        res: httpx.Response = self.client.request(
            method,
            "/api/v1/platform" + path,
            headers=self.headers(op, fresh=fresh, idem=key, extra=kw.pop("headers", None)),
            json=json,
            **kw,
        )
        return res


@pytest.fixture
def api(  # noqa: PLR0917 - pytest fixture
    platform_idp: Idp,
    tenant_idp: Idp,
    wrapper: LocalDevKeyWrapper,
    fleet_stores: fleet.FleetStores,
    app_engine: Engine,
    platform_engine: Engine,
) -> Iterator[Api]:
    app = create_app()
    service = ServiceTokenVerifier(SERVICE_KEY, replay_store=InMemoryReplayStore())
    platform_verifier = platform_idp.verifier(PLATFORM_CLIENT)
    tenant_verifier = tenant_idp.verifier(TENANT_CLIENT)
    store = KVIdempotencyStore(InMemoryKV())
    app.dependency_overrides[get_platform_token_verifier] = lambda: platform_verifier
    app.dependency_overrides[get_tenant_token_verifier] = lambda: tenant_verifier
    app.dependency_overrides[get_service_token_verifier] = lambda: service
    app.dependency_overrides[platform_api.get_idempotency_store] = lambda: store
    app.dependency_overrides[platform_api.get_key_wrapper] = lambda: wrapper
    app.dependency_overrides[fleet.get_fleet_key_wrapper] = lambda: wrapper
    app.dependency_overrides[fleet.get_fleet_stores] = lambda: fleet_stores
    with TestClient(app) as client:
        yield Api(client, service, platform_idp, tenant_idp)


@pytest.fixture
def owner(make_operator: MakeOperator) -> Operator:
    return make_operator("platform_owner")


def plan_payload(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "code": f"plan-{letters(10)}",
        "name": "Synthetic Standard",
        "tier": "shared",
        "billing_period": "monthly",
        "pricing_model": "flat",
        "base_price_inr": "5000.00",
        "gst_rate": "18",
        "trial_days": 30,
        "limits": {"students": 1000, "staff_users": 50, "storage_gb": 20},
    }
    body.update(overrides)
    return body


@pytest.fixture
def make_plan(owner: Operator) -> Callable[..., uuid.UUID]:
    from app.platform import billing

    def _make(**overrides: Any) -> uuid.UUID:
        plan = billing.create_plan(owner.actor, PlanIn.model_validate(plan_payload(**overrides)))
        billing.set_plan_status(owner.actor, plan.id, "published")
        return plan.id

    return _make


def billing_account_payload(state_code: str = "37", gstin: str | None = None) -> dict[str, Any]:
    return {
        "legal_name": "Synthetic Educational Society",
        "gstin": gstin,
        "billing_email": "accounts@school.example.test",
        "address_line1": "1 Synthetic Road",
        "city": "Vijayawada",
        "postal_code": "520001",
        "state_code": state_code,
    }


def provision_payload(plan_id: uuid.UUID, **overrides: Any) -> dict[str, Any]:
    code = f"s-{letters(12)}"
    body: dict[str, Any] = {
        "code": code,
        "school_name": "Synthetic Public School",
        "boards": ["SSC"],
        "tier": "shared",
        "plan_id": str(plan_id),
        "owner": {
            "display_name": "Synthetic Owner",
            "email": "owner@school.example.test",
            "idp_subject": f"owner-{uuid.uuid4()}",
            "language": "te",
        },
        "billing_account": billing_account_payload(),
    }
    body.update(overrides)
    return body


def money(value: str) -> Decimal:
    return Decimal(value)
