"""Suspended schools (BR-08, FR-PLT-004, FR-TEN-002; product decision 2026-09-27).

While a school is suspended the owner and principal may still use only the routes in
``app.authz.resolver.SUSPENDED_SCHOOL_ALLOWLIST`` (who am I, school choice, sign-in event and
Plan & billing and the full data export, FR-ADM-001). Every other school route answers
403 ``tenant_suspended`` for every role; the enumeration test walks every tenant route.
"""

from __future__ import annotations

import re
import sys
import uuid
from typing import Any

import pytest
from fastapi.routing import APIRoute, iter_route_contexts
from sqlalchemy import Engine, text

from app.authz.dependencies import Requirement
from app.authz.resolver import SUSPENDED_SCHOOL_ALLOWLIST
from app.core.db import platform_session
from app.main import create_app
from app.tenancy import service as tenancy

pytestmark = pytest.mark.db
W = sys.modules["sos_test_api_world"]

ALLOWED = {(e.method, e.path) for e in SUSPENDED_SCHOOL_ALLOWLIST}
_PARAM = re.compile(r"\{[^}]+\}")


@pytest.fixture(scope="module")
def suspended(admin_engine: Engine, app_engine: Engine, platform_engine: Engine) -> Any:
    """A suspended synthetic school with one member per interesting role."""
    tid = W.provision_school()
    people = {
        role: W.add_member(admin_engine, tid, [role])
        for role in ("owner", "principal", "accountant", "office_admin", "office_staff")
    }
    with platform_session() as pdb:
        tenancy.suspend_tenant(pdb, tid)
    return tid, people


def _tenant_routes() -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for rc in iter_route_contexts(create_app().routes):
        route = rc.original_route
        if not isinstance(route, APIRoute):
            continue
        guards = [d.call for d in route.dependant.dependencies if isinstance(d.call, Requirement)]
        if guards:
            out.extend((m, str(rc.path)) for m in sorted(rc.methods or ()))
    return sorted(out)


TENANT_ROUTES = _tenant_routes()


def _concrete(path: str) -> str:
    return _PARAM.sub(lambda _: str(uuid.uuid4()), path)


def test_BR_08_enumeration_covers_the_school_routes() -> None:
    assert len(TENANT_ROUTES) > 40
    tenant_allowed = {("GET", "/api/v1/me")} | {
        k for k in ALLOWED if k[1].startswith("/api/v1/tenant/")
    }
    assert tenant_allowed <= set(TENANT_ROUTES)


# Allowlisted routes answer exactly as in an active school (random ids: 404; the full export
# request: 202). Only these statuses; never 403 tenant_suspended.
ALLOWED_STATUS = {
    ("POST", "/api/v1/admin/tenant-export"): 202,
    ("GET", "/api/v1/admin/tenant-export/{tenant_export_id}"): 404,
    ("GET", "/api/v1/admin/tenant-export/{tenant_export_id}/download-url"): 404,
}


@pytest.mark.parametrize(("method", "path"), TENANT_ROUTES)
def test_BR_08_every_other_route_is_403_tenant_suspended_for_the_owner(
    suspended: Any, api: Any, method: str, path: str
) -> None:
    _tid, people = suspended
    body: dict[str, Any] | None = {} if (method, path) in ALLOWED and method == "POST" else None
    res = api.call(people["owner"], method, _concrete(path), json=body)
    if (method, path) in ALLOWED:
        assert res.status_code == ALLOWED_STATUS.get((method, path), 200), res.text
    else:
        assert res.status_code == 403, f"{method} {path}: {res.status_code} {res.text}"
        body = res.json()
        assert body["code"] == "tenant_suspended"
        assert "Plan & billing" in body["detail"]


@pytest.mark.parametrize("role", ["owner", "principal"])
def test_BR_08_owner_and_principal_keep_me_and_billing(suspended: Any, api: Any, role: str) -> None:
    tid, people = suspended
    person = people[role]
    me = api.call(person, "GET", "/api/v1/me")
    assert me.status_code == 200, me.text
    assert me.json()["tenant_id"] == str(tid)
    # The web shows the "suspended" banner from this (FR-PLT-004).
    assert me.json()["tenant_status"] == "suspended"
    billing = api.call(person, "GET", "/api/v1/tenant/billing")
    assert billing.status_code == 200, billing.text
    invoices = api.call(person, "GET", "/api/v1/tenant/billing/invoices")
    assert invoices.status_code == 200, invoices.text
    switched = api.call(person, "POST", "/api/v1/me/active-tenant", json={"tenant_id": str(tid)})
    assert switched.status_code == 200, switched.text
    schools = api.call(person, "GET", "/api/v1/me/schools").json()["data"]
    assert [s["status"] for s in schools if s["tenant_id"] == str(tid)] == ["suspended"]
    # Everything else stays closed, even with the permission (owner holds user.manage).
    users = api.call(person, "GET", "/api/v1/users")
    assert users.status_code == 403
    assert users.json()["code"] == "tenant_suspended"


def test_BR_08_owner_can_still_export_all_data_while_suspended(
    suspended: Any, api: Any, admin_engine: Engine
) -> None:
    """FR-ADM-001, docs/16 §5.5: the owner requests the full export, the worker builds it for
    the suspended school, and the owner downloads it. The principal reaches the route but lacks
    tenant.export_all (403 forbidden, not tenant_suspended); other roles stay suspended."""
    tid, people = suspended
    ad = _admin_support()
    ad.install()
    school = W.School(tid, people=dict(people))
    ad.settle(admin_engine, school)
    res = api.call(people["owner"], "POST", "/api/v1/admin/tenant-export", json={})
    assert res.status_code == 202, res.text
    export_id = uuid.UUID(res.json()["id"])
    assert ad.run(school, export_id) == "ready"
    listed = api.call(people["owner"], "GET", "/api/v1/admin/tenant-export")
    assert export_id in {uuid.UUID(e["id"]) for e in listed.json()["data"]}
    link = api.call(people["owner"], "GET", f"/api/v1/admin/tenant-export/{export_id}/download-url")
    assert link.status_code == 200, link.text
    principal = api.call(people["principal"], "GET", "/api/v1/admin/tenant-export")
    assert principal.status_code == 403
    assert principal.json()["code"] != "tenant_suspended"
    for role in ("accountant", "office_admin"):
        res = api.call(people[role], "GET", "/api/v1/admin/tenant-export")
        assert res.status_code == 403
        assert res.json()["code"] == "tenant_suspended"
    # Retention settings are not on the allowlist, not even for the owner.
    retention = api.call(people["owner"], "GET", "/api/v1/admin/retention")
    assert retention.status_code == 403
    assert retention.json()["code"] == "tenant_suspended"


def _admin_support() -> Any:
    import importlib.util
    from pathlib import Path

    name = "sos_test_admin_support"
    if name not in sys.modules:
        path = Path(__file__).resolve().parents[1] / "admin" / "support.py"
        spec = importlib.util.spec_from_file_location(name, path)
        assert spec is not None
        assert spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


@pytest.mark.parametrize("role", ["accountant", "office_admin", "office_staff"])
def test_BR_08_other_roles_get_tenant_suspended_everywhere(
    suspended: Any, api: Any, role: str
) -> None:
    tid, people = suspended
    person = people[role]
    for method, path in (
        ("GET", "/api/v1/me"),
        ("GET", "/api/v1/tenant/billing"),  # accountant holds tenant.billing.read
        ("GET", "/api/v1/tenant/billing/invoices"),
        ("GET", "/api/v1/announcements"),
    ):
        res = api.call(person, method, path)
        assert res.status_code == 403, f"{role} {method} {path}: {res.text}"
        assert res.json()["code"] == "tenant_suspended"
    res = api.call(person, "POST", "/api/v1/me/active-tenant", json={"tenant_id": str(tid)})
    assert res.status_code == 403
    assert res.json()["code"] == "tenant_suspended"


def test_BR_08_allowlist_still_needs_the_route_permission(
    suspended: Any, api: Any, admin_engine: Engine
) -> None:
    """A principal without tenant.billing.read (custom setup) is refused by the permission."""
    tid, _people = suspended
    person = W.add_member(admin_engine, tid, ["principal"])
    with admin_engine.begin() as c:
        c.execute(
            text(
                "DELETE FROM core.role_permissions rp USING core.roles r "
                "WHERE rp.tenant_id = :t AND r.id = rp.role_id AND r.key = 'principal' "
                "AND rp.permission_key = 'tenant.billing.read'"
            ),
            {"t": tid},
        )
    try:
        res = api.call(person, "GET", "/api/v1/tenant/billing")
        assert res.status_code == 403
        assert res.json()["code"] != "tenant_suspended"
    finally:
        with admin_engine.begin() as c:
            c.execute(
                text(
                    "INSERT INTO core.role_permissions (tenant_id, role_id, permission_key) "
                    "SELECT :t, r.id, 'tenant.billing.read' FROM core.roles r "
                    "WHERE r.tenant_id = :t AND r.key = 'principal'"
                ),
                {"t": tid},
            )


def test_BR_08_login_event_succeeds_for_owner_and_is_denied_for_staff(
    suspended: Any, api: Any, admin_engine: Engine
) -> None:
    tid, people = suspended
    ok = api.call(people["owner"], "POST", "/api/v1/me/login-event")
    assert ok.status_code == 200, ok.text
    denied = api.call(people["office_staff"], "POST", "/api/v1/me/login-event")
    assert denied.status_code == 403
    assert denied.json()["code"] == "tenant_suspended"
    events = W.audit_events(admin_engine, tid)
    by_actor = {(e["action"], e["actor_id"]) for e in events}
    assert ("auth.login.succeeded", people["owner"].user_id) in by_actor
    assert ("auth.login.denied", people["office_staff"].user_id) in by_actor
    reasons = [
        e["summary"].get("reason")
        for e in events
        if e["action"] == "auth.login.denied" and e["actor_id"] == people["office_staff"].user_id
    ]
    assert "tenant_suspended" in reasons


def test_BR_08_offboarding_school_follows_the_same_allowlist(
    api: Any, admin_engine: Engine
) -> None:
    tid = W.provision_school()
    owner = W.add_member(admin_engine, tid, ["owner"])
    staff = W.add_member(admin_engine, tid, ["office_staff"])
    with platform_session() as pdb:
        tenancy.begin_offboarding(pdb, tid)
    assert api.call(owner, "GET", "/api/v1/tenant/billing").status_code == 200
    res = api.call(owner, "GET", "/api/v1/academic-years")
    assert res.status_code == 403
    assert res.json()["code"] == "tenant_suspended"
    assert api.call(staff, "GET", "/api/v1/me").json()["code"] == "tenant_suspended"


def test_BR_08_reactivated_school_is_open_again(api: Any, admin_engine: Engine) -> None:
    tid = W.provision_school()
    staff = W.add_member(admin_engine, tid, ["office_staff"])
    with platform_session() as pdb:
        tenancy.suspend_tenant(pdb, tid)
    assert api.call(staff, "GET", "/api/v1/me").status_code == 403
    with platform_session() as pdb:
        tenancy.reactivate_tenant(pdb, tid)
    me = api.call(staff, "GET", "/api/v1/me")
    assert me.status_code == 200
    assert me.json()["tenant_status"] == "active"
