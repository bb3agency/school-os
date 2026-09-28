"""Synthetic schools, staff and an API client for API and security tests (synthetic data only).

Loaded by ``tests/api/conftest.py`` and the security suites with :func:`load` (pytest runs with
``--import-mode=importlib``, so test helper modules are not importable by name).

Two schools are provisioned through the real path (``tenancy.provision_tenant`` -> system-role
cloning hook -> activation). School A has one active member per system role plus:

    academic year 2026-27 (current), 2025-26
    class IX: sections 9A, 9C          class X: section 10A
    class_teacher  -> section scope 9A
    teacher        -> class scope X
    a "target" member (teacher role) used as the object of user-management calls

School B has its own year, class, section and member (cross-tenant IDs).
Authentication: ``get_principal`` is overridden to read ``X-Test-Subject``, ``X-Test-Mfa``,
``X-Test-Auth-Age`` and ``X-Test-Kind`` (``user`` = staff pool, ``support`` = the break-glass
support client of the operator pool, ADR-0023) headers; a caller object with a ``kind``
attribute sets the last one. End-to-end tests elsewhere use real tokens.
"""

from __future__ import annotations

import datetime as dt
import itertools
import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

import httpx
import pytest
from fastapi import Request
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import Engine, text

import app.identity.service  # noqa: F401  (registers the system-role cloning hook)
from app.authz.catalog import system_roles
from app.authz.kv import InMemoryKV, set_kv_store
from app.core.config import Environment, KeyWrapperKind, Settings
from app.core.crypto import LocalDevKeyWrapper
from app.core.db import platform_session, tenant_session
from app.identity.principal import Principal, get_principal
from app.main import create_app
from app.tenancy import service as tenancy
from app.tenancy.schemas import AcademicYearCreate, ClassCreate, SectionCreate

ROLES = tuple(system_roles())
SUBJECT_HEADER = "X-Test-Subject"
MFA_HEADER = "X-Test-Mfa"
AUTH_AGE_HEADER = "X-Test-Auth-Age"
KIND_HEADER = "X-Test-Kind"
ISSUER = "https://idp.synthetic.test/pool"
SUPPORT_ISSUER = "https://idp.synthetic.test/operator-pool"
_counter = itertools.count(1)


def unique(prefix: str = "") -> str:
    return f"{prefix}{next(_counter):04d}{uuid.uuid4().hex[:6]}".upper()


@dataclass
class Person:
    role: str | None
    user_id: uuid.UUID
    membership_id: uuid.UUID
    subject: str
    display_name: str


@dataclass
class School:
    tenant_id: uuid.UUID
    people: dict[str, Person] = field(default_factory=dict)
    ids: dict[str, uuid.UUID] = field(default_factory=dict)


@dataclass
class World:
    a: School
    b: School

    def person(self, role: str) -> Person:
        return self.a.people[role]


def fake_principal(request: Request) -> Principal:
    subject = request.headers.get(SUBJECT_HEADER)
    if not subject:
        from app.core.errors import Unauthenticated

        raise Unauthenticated()
    age = int(request.headers.get(AUTH_AGE_HEADER, "60"))
    now = dt.datetime.now(dt.UTC)
    support = request.headers.get(KIND_HEADER) == "support"
    return Principal(
        subject=subject,
        issuer=SUPPORT_ISSUER if support else ISSUER,
        kind="support" if support else "user",
        auth_time=now - dt.timedelta(seconds=age),
        mfa=request.headers.get(MFA_HEADER, "true") == "true",
        session_id="synthetic-session",
        expires_at=now + dt.timedelta(minutes=5),
    )


def wrapper() -> LocalDevKeyWrapper:
    return LocalDevKeyWrapper(
        Settings(
            env=Environment.CI,
            key_wrapper=KeyWrapperKind.LOCAL_DEV,
            local_dev_master_key=SecretStr("synthetic-ci-master-key-0123456789abcdef"),
        )
    )


def provision_school() -> uuid.UUID:
    result = tenancy.provision_tenant(
        code=f"s-{uuid.uuid4().hex[:12]}", name="Synthetic Model School", wrapper=wrapper()
    )
    with platform_session() as pdb:
        tenancy.activate_tenant(pdb, result.tenant_id)
    return result.tenant_id


def add_member(
    admin: Engine,
    tenant_id: uuid.UUID,
    roles: list[str],
    *,
    status: str = "active",
    scopes: list[tuple[str, uuid.UUID | None]] | None = None,
    display_name: str | None = None,
    email: str | None = None,
    expires_at: dt.datetime | None = None,
) -> Person:
    uid, mid = uuid.uuid4(), uuid.uuid4()
    subject = f"sub-{uuid.uuid4().hex}"
    name = display_name or f"Synthetic Staff {unique()}"
    with admin.begin() as c:
        c.execute(
            text(
                "INSERT INTO core.users (id, idp_subject, display_name, email) "
                "VALUES (:u, :s, :n, :e)"
            ),
            {"u": uid, "s": subject, "n": name, "e": email},
        )
        c.execute(
            text(
                "INSERT INTO core.memberships (id, tenant_id, user_id, status, expires_at) "
                "VALUES (:m, :t, :u, :st, :x)"
            ),
            {"m": mid, "t": tenant_id, "u": uid, "st": status, "x": expires_at},
        )
        for role in roles:
            c.execute(
                text(
                    "INSERT INTO core.membership_roles (tenant_id, membership_id, role_id) "
                    "SELECT :t, :m, r.id FROM core.roles r WHERE r.tenant_id = :t AND r.key = :k"
                ),
                {"t": tenant_id, "m": mid, "k": role},
            )
        for scope_type, ref in scopes or []:
            c.execute(
                text(
                    "INSERT INTO core.membership_scopes "
                    "(id, tenant_id, membership_id, scope_type, scope_ref) "
                    "VALUES (gen_random_uuid(), :t, :m, :st, :r)"
                ),
                {"t": tenant_id, "m": mid, "st": scope_type, "r": ref},
            )
    return Person(roles[0] if roles else None, uid, mid, subject, name)


def build_structure(school: School, actor: Person, *, sections: bool = True) -> None:
    with tenant_session(school.tenant_id, actor.user_id) as db:
        cur = tenancy.create_academic_year(
            db,
            AcademicYearCreate(
                label="2026-27",
                starts_on=dt.date(2026, 6, 1),
                ends_on=dt.date(2027, 3, 31),
                is_current=True,
            ),
        )
        old = tenancy.create_academic_year(
            db,
            AcademicYearCreate(
                label="2025-26", starts_on=dt.date(2025, 6, 1), ends_on=dt.date(2026, 3, 31)
            ),
        )
        ix = tenancy.create_class(
            db, ClassCreate(code="IX", display_en="Class IX", display_te="9వ తరగతి", sort_order=120)
        )
        x = tenancy.create_class(
            db, ClassCreate(code="X", display_en="Class X", display_te="10వ తరగతి", sort_order=130)
        )
        school.ids.update(year=cur.id, old_year=old.id, class_ix=ix.id, class_x=x.id)
        if sections:
            for key, klass, name in (("9a", ix, "A"), ("9c", ix, "C"), ("10a", x, "A")):
                sec = tenancy.create_section(
                    db, SectionCreate(academic_year_id=cur.id, class_id=klass.id, name=name)
                )
                school.ids[f"section_{key}"] = sec.id


def build_world(admin: Engine) -> World:
    a = School(provision_school())
    owner = add_member(admin, a.tenant_id, ["owner"])
    a.people["owner"] = owner
    build_structure(a, owner)
    for role in ROLES:
        if role == "owner":
            continue
        scopes: list[tuple[str, uuid.UUID | None]] = []
        if role == "class_teacher":
            scopes = [("section", a.ids["section_9a"])]
        elif role == "teacher":
            scopes = [("class", a.ids["class_x"])]
        a.people[role] = add_member(admin, a.tenant_id, [role], scopes=scopes)
    a.people["target"] = add_member(admin, a.tenant_id, ["teacher"])

    b = School(provision_school())
    b_owner = add_member(admin, b.tenant_id, ["owner"])
    b.people["owner"] = b_owner
    build_structure(b, b_owner)
    b.people["target"] = add_member(admin, b.tenant_id, ["teacher"])
    return World(a=a, b=b)


class Api:
    """TestClient wrapper that signs requests as a synthetic person."""

    def __init__(self, client: TestClient) -> None:
        self.client = client

    def call(
        self,
        who: Person,
        method: str,
        path: str,
        *,
        mfa: bool = True,
        auth_age_s: int = 60,
        tenant: uuid.UUID | None = None,
        headers: dict[str, str] | None = None,
        **kwargs: Any,
    ) -> httpx.Response:
        h = {SUBJECT_HEADER: who.subject, MFA_HEADER: "true" if mfa else "false"}
        h[AUTH_AGE_HEADER] = str(auth_age_s)
        kind = getattr(who, "kind", None)
        if isinstance(kind, str):
            h[KIND_HEADER] = kind
        if tenant is not None:
            h["X-Active-Tenant"] = str(tenant)
        h.update(headers or {})
        res: httpx.Response = self.client.request(method, path, headers=h, **kwargs)
        return res


def make_client() -> Iterator[Api]:
    set_kv_store(InMemoryKV())
    app = create_app()
    app.dependency_overrides[get_principal] = fake_principal
    with TestClient(app) as client:
        yield Api(client)
    set_kv_store(None)


def version_of(admin: Engine, table: str, row_id: uuid.UUID) -> int:
    allowed = {"core.academic_years", "core.classes", "core.sections", "core.memberships"}
    assert table in allowed
    with admin.connect() as c:
        return int(
            c.execute(
                text(f"SELECT version FROM {table} WHERE id = :i"), {"i": row_id}
            ).scalar_one()
        )


def tenant_version(admin: Engine, tenant_id: uuid.UUID) -> int:
    with admin.connect() as c:
        return int(
            c.execute(
                text("SELECT version FROM core.tenants WHERE id = :i"), {"i": tenant_id}
            ).scalar_one()
        )


def audit_events(
    admin: Engine, tenant_id: uuid.UUID, action: str | None = None
) -> list[dict[str, Any]]:
    sql = (
        "SELECT action, resource_type, resource_id, actor_id, summary FROM audit.events "
        "WHERE tenant_id = :t"
    )
    params: dict[str, Any] = {"t": tenant_id}
    if action is not None:
        sql += " AND action = :a"
        params["a"] = action
    with admin.connect() as c:
        return [dict(r._mapping) for r in c.execute(text(sql + " ORDER BY seq"), params)]


# --- fixtures (re-exported by the conftest / test modules that load this file) ----------------


@pytest.fixture(scope="session")
def world(admin_engine: Engine, app_engine: Engine, platform_engine: Engine) -> World:
    return build_world(admin_engine)


@pytest.fixture
def api(app_engine: Engine, platform_engine: Engine) -> Iterator[Api]:
    yield from make_client()
