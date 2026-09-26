"""/me, active school choice, login events, MFA and suspended schools (US-101, FR-IAM-002,
FR-IAM-013, FR-IAM-005)."""

from __future__ import annotations

import sys
import uuid
from typing import Any

import pytest
from sqlalchemy import Engine, text

pytestmark = pytest.mark.db
W = sys.modules["sos_test_api_world"]


def test_FR_IAM_001_me_returns_context(world: Any, api: Any) -> None:
    owner = world.person("owner")
    res = api.call(owner, "GET", "/api/v1/me")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["tenant_id"] == str(world.a.tenant_id)
    assert body["roles"] == ["owner"]
    assert "tenant.settings.manage" in body["permissions"]
    assert "session.authenticated" in body["permissions"]
    assert body["tenant_ids"] == [str(world.a.tenant_id)]


def test_FR_IAM_012_me_shows_scopes(world: Any, api: Any) -> None:
    ct = world.person("class_teacher")
    body = api.call(ct, "GET", "/api/v1/me").json()
    assert body["scopes"] == [{"type": "section", "ref": str(world.a.ids["section_9a"])}]


def test_FR_IAM_001_unknown_subject_is_403_no_membership(api: Any) -> None:
    ghost = W.Person(None, uuid.uuid4(), uuid.uuid4(), "sub-nobody", "x")
    res = api.call(ghost, "GET", "/api/v1/me")
    assert res.status_code == 403
    assert res.json()["code"] == "no_membership"


def test_FR_IAM_001_missing_credentials_is_401(api: Any) -> None:
    res = api.client.get("/api/v1/me")
    assert res.status_code == 401


@pytest.mark.parametrize("role", ["owner", "principal", "office_admin"])
def test_FR_IAM_002_privileged_roles_without_mfa_are_403(world: Any, api: Any, role: str) -> None:
    res = api.call(world.person(role), "GET", "/api/v1/me", mfa=False)
    assert res.status_code == 403
    assert res.json()["code"] == "mfa_required"


@pytest.mark.parametrize("role", ["office_staff", "accountant", "teacher", "auditor_readonly"])
def test_FR_IAM_002_other_roles_work_without_mfa(world: Any, api: Any, role: str) -> None:
    res = api.call(world.person(role), "GET", "/api/v1/me", mfa=False)
    assert res.status_code == 200


def test_FR_IAM_013_multiple_memberships_need_active_tenant(
    world: Any, api: Any, admin_engine: Engine
) -> None:
    person = W.add_member(admin_engine, world.a.tenant_id, ["office_staff"])
    with admin_engine.begin() as c:
        c.execute(
            text(
                "INSERT INTO core.memberships (id, tenant_id, user_id, status) "
                "VALUES (gen_random_uuid(), :t, :u, 'active')"
            ),
            {"t": world.b.tenant_id, "u": person.user_id},
        )
    res = api.call(person, "GET", "/api/v1/me")
    assert res.status_code == 409
    assert res.json()["code"] == "active_tenant_required"
    res = api.call(person, "GET", "/api/v1/me", tenant=world.b.tenant_id)
    assert res.status_code == 200
    assert res.json()["tenant_id"] == str(world.b.tenant_id)
    assert len(res.json()["tenant_ids"]) == 2
    switched = api.call(
        person, "POST", "/api/v1/me/active-tenant", json={"tenant_id": str(world.a.tenant_id)}
    )
    assert switched.status_code == 200, switched.text
    assert switched.json()["tenant_id"] == str(world.a.tenant_id)


def test_FR_IAM_013_foreign_active_tenant_is_403_without_revealing(world: Any, api: Any) -> None:
    owner = world.person("owner")
    for tenant in (world.b.tenant_id, uuid.uuid4()):
        res = api.call(owner, "GET", "/api/v1/me", tenant=tenant)
        assert res.status_code == 403
        assert res.json()["code"] == "no_membership"
        res = api.call(owner, "POST", "/api/v1/me/active-tenant", json={"tenant_id": str(tenant)})
        assert res.status_code == 403


def test_FR_IAM_013_malformed_active_tenant_header_is_400(world: Any, api: Any) -> None:
    res = api.call(world.person("owner"), "GET", "/api/v1/me", headers={"X-Active-Tenant": "nope"})
    assert res.status_code == 400


def test_FR_TEN_002_suspended_school_is_403_tenant_suspended(
    api: Any, admin_engine: Engine
) -> None:
    tid = W.provision_school()
    person = W.add_member(admin_engine, tid, ["owner"])
    assert api.call(person, "GET", "/api/v1/me").status_code == 200
    with admin_engine.begin() as c:
        c.execute(text("UPDATE core.tenants SET status = 'suspended' WHERE id = :t"), {"t": tid})
    res = api.call(person, "GET", "/api/v1/me")
    assert res.status_code == 403
    assert res.json()["code"] == "tenant_suspended"


@pytest.mark.parametrize("status", ["invited", "suspended", "removed"])
def test_FR_IAM_014_inactive_membership_is_refused(
    world: Any, api: Any, admin_engine: Engine, status: str
) -> None:
    person = W.add_member(admin_engine, world.a.tenant_id, ["office_staff"], status=status)
    assert api.call(person, "GET", "/api/v1/me").status_code == 403


def test_FR_IAM_010_expired_auditor_membership_is_refused(
    world: Any, api: Any, admin_engine: Engine
) -> None:
    import datetime as dt

    person = W.add_member(admin_engine, world.a.tenant_id, ["auditor_readonly"])
    assert api.call(person, "GET", "/api/v1/me").status_code == 200
    with admin_engine.begin() as c:
        c.execute(
            text("UPDATE core.memberships SET created_at = :c, expires_at = :x WHERE id = :m"),
            {
                "c": dt.datetime.now(dt.UTC) - dt.timedelta(days=20),
                "x": dt.datetime.now(dt.UTC) - dt.timedelta(days=6),
                "m": person.membership_id,
            },
        )
    assert api.call(person, "GET", "/api/v1/me").status_code == 403


# --- login events (US-101 AC4, invariant 7) -----------------------------------------------------


def test_US_101_login_event_audits_success_once(world: Any, api: Any, admin_engine: Engine) -> None:
    person = W.add_member(admin_engine, world.a.tenant_id, ["office_staff"])
    res = api.call(person, "POST", "/api/v1/me/login-event")
    assert res.status_code == 200, res.text
    events = [
        e
        for e in W.audit_events(admin_engine, world.a.tenant_id, "auth.login.succeeded")
        if e["actor_id"] == person.user_id
    ]
    assert len(events) == 1
    assert events[0]["resource_id"] == person.membership_id
    with admin_engine.connect() as c:
        stamped = c.execute(
            text("SELECT last_login_at FROM core.users WHERE id = :u"), {"u": person.user_id}
        ).scalar()
    assert stamped is not None


def test_US_101_login_event_audits_denied_mfa(world: Any, api: Any, admin_engine: Engine) -> None:
    person = W.add_member(admin_engine, world.a.tenant_id, ["principal"])
    res = api.call(person, "POST", "/api/v1/me/login-event", mfa=False)
    assert res.status_code == 403
    denied = [
        e
        for e in W.audit_events(admin_engine, world.a.tenant_id, "auth.login.denied")
        if e["actor_id"] == person.user_id
    ]
    assert len(denied) == 1
    assert denied[0]["summary"]["reason"] == "mfa_required"


def test_FR_IAM_005_login_event_is_rate_limited(world: Any, api: Any, admin_engine: Engine) -> None:
    person = W.add_member(admin_engine, world.a.tenant_id, ["office_staff"])
    codes = [api.call(person, "POST", "/api/v1/me/login-event").status_code for _ in range(11)]
    assert codes[:10] == [200] * 10
    assert codes[10] == 429


def test_FR_IAM_013_schools_listed_without_active_tenant(
    world: Any, api: Any, admin_engine: Engine
) -> None:
    """A user with several memberships can list them (for the picker) before choosing one."""
    person = W.add_member(admin_engine, world.a.tenant_id, ["office_staff"])
    with admin_engine.begin() as c:
        c.execute(
            text(
                "INSERT INTO core.memberships (id, tenant_id, user_id, status) "
                "VALUES (gen_random_uuid(), :t, :u, 'active')"
            ),
            {"t": world.b.tenant_id, "u": person.user_id},
        )
    res = api.call(person, "GET", "/api/v1/me/schools")
    assert res.status_code == 200, res.text
    schools = {s["tenant_id"]: s for s in res.json()["data"]}
    assert set(schools) == {str(world.a.tenant_id), str(world.b.tenant_id)}
    for school in schools.values():
        assert school["name"]
        assert school["status"] == "active"
        assert set(school) == {"tenant_id", "code", "name", "status"}


def test_FR_IAM_013_schools_list_is_empty_for_unknown_subject(api: Any) -> None:
    ghost = W.Person(None, uuid.uuid4(), uuid.uuid4(), "sub-nobody-schools", "x")
    res = api.call(ghost, "GET", "/api/v1/me/schools")
    assert res.status_code == 200
    assert res.json() == {"data": []}
