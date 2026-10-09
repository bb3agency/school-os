"""Break-glass fixtures: a fresh synthetic school, synthetic operators and helpers that raise
requests through the real control-plane service (synthetic data only)."""

from __future__ import annotations

import importlib.util
import sys
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from sqlalchemy import Engine, text

from app.core.db import platform_session


def load_world() -> ModuleType:
    name = "sos_test_api_world"
    module = sys.modules.get(name)
    if module is None:
        path = Path(__file__).resolve().parents[1] / "api" / "world.py"
        spec = importlib.util.spec_from_file_location(name, path)
        assert spec is not None
        assert spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return module


W = load_world()
world = W.world
api = W.api

OPERATOR_NAME = "Synthetic Support Operator Kalyani"
OPERATOR_EMAIL_DOMAIN = "ops.example.test"


@dataclass
class Operator:
    id: uuid.UUID
    subject: str
    email: str

    @property
    def actor(self) -> Any:
        from app.platform.common import Actor

        return Actor(self.id)


MakeOperator = Callable[..., Operator]


def make_operator_row(*roles: str, subject: str | None = None, status: str = "active") -> Operator:
    oid = uuid.uuid4()
    sub = subject or f"op-sub-{oid}"
    email = f"op-{oid.hex[:10]}@{OPERATOR_EMAIL_DOMAIN}"
    with platform_session() as s:
        s.execute(
            text(
                "INSERT INTO platform.operators (id, idp_subject, email, display_name, status, "
                "mfa_enrolled, deactivated_at) VALUES (:i, :s, :e, :n, :st, true, "
                "CASE WHEN :st = 'deactivated' THEN now() END)"
            ),
            {"i": oid, "s": sub, "e": email, "n": OPERATOR_NAME, "st": status},
        )
        for role in roles:
            s.execute(
                # Long-standing roles: a two-person second step needs a role older than
                # two_person_min_role_age_days (audit 2026-10-05 A-14).
                text(
                    "INSERT INTO platform.operator_roles (operator_id, role_key, granted_at) "
                    "VALUES (:o, :r, now() - interval '30 days')"
                ),
                {"o": oid, "r": role},
            )
    return Operator(oid, sub, email)


@pytest.fixture
def make_operator(platform_engine: Engine, app_engine: Engine) -> MakeOperator:
    return make_operator_row


@dataclass
class Campus:
    """A fresh school with structure, staff and a control-plane deployment record."""

    school: Any

    @property
    def tenant_id(self) -> uuid.UUID:
        value: uuid.UUID = self.school.tenant_id
        return value

    def person(self, role: str) -> Any:
        return self.school.people[role]

    def id(self, key: str) -> uuid.UUID:
        value: uuid.UUID = self.school.ids[key]
        return value


def register_deployment(tenant_id: uuid.UUID) -> None:
    with platform_session() as s:
        s.execute(
            text(
                "INSERT INTO platform.deployments (id, tenant_id, tenant_code, school_name, mode, "
                "tenant_status, status) VALUES (:i, :t, :c, 'Synthetic Model School', 'shared', "
                "'active', 'healthy')"
            ),
            {"i": uuid.uuid4(), "t": tenant_id, "c": f"bg-{uuid.uuid4().hex[:12]}"},
        )


def build_campus(admin: Engine) -> Campus:
    school = W.School(W.provision_school())
    owner = W.add_member(admin, school.tenant_id, ["owner"])
    school.people["owner"] = owner
    W.build_structure(school, owner)
    for role in ("principal", "office_admin", "office_staff", "teacher"):
        school.people[role] = W.add_member(admin, school.tenant_id, [role])
    register_deployment(school.tenant_id)
    return Campus(school)


@pytest.fixture
def campus(admin_engine: Engine, app_engine: Engine, platform_engine: Engine) -> Campus:
    return build_campus(admin_engine)


def raise_request(
    tenant_id: uuid.UUID,
    operator: Operator,
    *,
    minutes: int = 60,
    scope: dict[str, Any] | None = None,
    emergency: bool = False,
    reason_code: str = "support_request",
) -> uuid.UUID:
    """A request created through the real control-plane service (platform.breakglass)."""
    from app.platform import breakglass as platform_breakglass
    from app.platform.schemas import BreakGlassIn

    out = platform_breakglass.create_request(
        operator.actor,
        BreakGlassIn(
            tenant_id=tenant_id,
            reason_code=reason_code,
            reason="Import batch shows duplicate rows; need to read the batch to fix it.",
            scope=scope or {},
            duration_minutes=minutes,
            emergency=emergency,
        ),
    )
    return out.id


def grant_row(admin: Engine, request_id: uuid.UUID) -> dict[str, Any]:
    with admin.connect() as c:
        row = c.execute(
            text("SELECT * FROM ops.break_glass_grants WHERE platform_request_id = :r"),
            {"r": request_id},
        ).one()
    return dict(row._mapping)


def platform_request(admin: Engine, request_id: uuid.UUID) -> dict[str, Any]:
    with admin.connect() as c:
        row = c.execute(
            text("SELECT * FROM platform.breakglass_requests WHERE id = :r"), {"r": request_id}
        ).one()
    return dict(row._mapping)


def platform_audit(admin: Engine, request_id: uuid.UUID) -> list[str]:
    with admin.connect() as c:
        return list(
            c.execute(
                text(
                    "SELECT action FROM platform.audit_events WHERE resource_id = :r ORDER BY seq"
                ),
                {"r": request_id},
            ).scalars()
        )


def notifications_of(admin: Engine, membership_id: uuid.UUID) -> list[str]:
    with admin.connect() as c:
        return list(
            c.execute(
                text(
                    "SELECT template_key FROM ops.notifications "
                    "WHERE recipient_membership_id = :m ORDER BY created_at, id"
                ),
                {"m": membership_id},
            ).scalars()
        )
