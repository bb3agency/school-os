"""Generated authorization matrix (SEC-003, SEC-005, FR-IAM-002, FR-IAM-010, docs/12 §4.2).

For every (system role, tenant route) pair a synthetic member holding only that role calls the
route with a valid request: 2xx when roles.yaml grants the route's permission, 403 otherwise.
Also: 428 for step-up routes with a stale sign-in, 403 ``mfa_required`` for privileged roles
without MFA. Expectations come from roles.yaml (itself pinned to docs/07 §6.2) and the live
route table, so a new route without a matrix entry fails the suite.
"""

from __future__ import annotations

import importlib.util
import itertools
import sys
import uuid
from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from fastapi.routing import APIRoute, iter_route_contexts
from sqlalchemy import Engine

from app.authz.catalog import AUTHENTICATED, system_roles
from app.main import create_app

pytestmark = pytest.mark.db


def _load_world() -> ModuleType:
    name = "sos_test_api_world"
    if name not in sys.modules:
        path = Path(__file__).resolve().parents[1] / "api" / "world.py"
        spec = importlib.util.spec_from_file_location(name, path)
        assert spec is not None
        assert spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


W = _load_world()
world = W.world
api = W.api

Request = tuple[str, dict[str, Any] | None, dict[str, str]]
Builder = Callable[[Any, str, Engine], Request]
_years = itertools.count(2100)


def _ct(role: str, w: Any, scoped: str, default: str) -> uuid.UUID:
    value: uuid.UUID = w.a.ids[scoped] if role == "teacher" else w.a.ids[default]
    return value


def _if_match(version: int) -> dict[str, str]:
    return {"If-Match": f'W/"{version}"'}


def _new_year(w: Any, role: str, admin: Engine) -> Request:
    y = next(_years)
    body = {
        "label": f"{y}-{(y + 1) % 100:02d}",
        "starts_on": f"{y}-06-01",
        "ends_on": f"{y + 1}-03-31",
    }
    return "/api/v1/academic-years", body, {}


def _target(w: Any) -> Any:
    return w.a.people["target"]


SPECS: dict[tuple[str, str], Builder] = {
    ("GET", "/api/v1/me"): lambda w, r, a: ("/api/v1/me", None, {}),
    ("GET", "/api/v1/me/schools"): lambda w, r, a: ("/api/v1/me/schools", None, {}),
    ("POST", "/api/v1/me/active-tenant"): lambda w, r, a: (
        "/api/v1/me/active-tenant",
        {"tenant_id": str(w.a.tenant_id)},
        {},
    ),
    ("POST", "/api/v1/me/login-event"): lambda w, r, a: ("/api/v1/me/login-event", None, {}),
    ("GET", "/api/v1/users"): lambda w, r, a: ("/api/v1/users", None, {}),
    ("GET", "/api/v1/users/{user_id}"): lambda w, r, a: (
        f"/api/v1/users/{_target(w).user_id}",
        None,
        {},
    ),
    ("POST", "/api/v1/users"): lambda w, r, a: (
        "/api/v1/users",
        {
            "idp_subject": f"sub-{uuid.uuid4().hex}",
            "display_name": "Synthetic Matrix Invitee",
            "roles": ["teacher"],
        },
        {},
    ),
    ("PATCH", "/api/v1/users/{user_id}"): lambda w, r, a: (
        f"/api/v1/users/{_target(w).user_id}",
        {"status": "active"},
        _if_match(W.version_of(a, "core.memberships", _target(w).membership_id)),
    ),
    ("PUT", "/api/v1/users/{user_id}/roles"): lambda w, r, a: (
        f"/api/v1/users/{_target(w).user_id}/roles",
        {"roles": ["teacher"]},
        {},
    ),
    ("PUT", "/api/v1/users/{user_id}/scopes"): lambda w, r, a: (
        f"/api/v1/users/{_target(w).user_id}/scopes",
        {"scopes": []},
        {},
    ),
    ("GET", "/api/v1/roles"): lambda w, r, a: ("/api/v1/roles", None, {}),
    ("GET", "/api/v1/permissions"): lambda w, r, a: ("/api/v1/permissions", None, {}),
    ("GET", "/api/v1/tenant"): lambda w, r, a: ("/api/v1/tenant", None, {}),
    ("PATCH", "/api/v1/tenant"): lambda w, r, a: (
        "/api/v1/tenant",
        {"idle_timeout_minutes": 15},
        _if_match(W.tenant_version(a, w.a.tenant_id)),
    ),
    ("GET", "/api/v1/academic-years"): lambda w, r, a: ("/api/v1/academic-years", None, {}),
    ("GET", "/api/v1/academic-years/{year_id}"): lambda w, r, a: (
        f"/api/v1/academic-years/{w.a.ids['old_year']}",
        None,
        {},
    ),
    ("POST", "/api/v1/academic-years"): _new_year,
    ("PATCH", "/api/v1/academic-years/{year_id}"): lambda w, r, a: (
        f"/api/v1/academic-years/{w.a.ids['old_year']}",
        {},
        _if_match(W.version_of(a, "core.academic_years", w.a.ids["old_year"])),
    ),
    ("POST", "/api/v1/academic-years/{year_id}/make-current"): lambda w, r, a: (
        f"/api/v1/academic-years/{w.a.ids['year']}/make-current",
        None,
        _if_match(W.version_of(a, "core.academic_years", w.a.ids["year"])),
    ),
    ("GET", "/api/v1/classes"): lambda w, r, a: ("/api/v1/classes", None, {}),
    ("POST", "/api/v1/classes/defaults"): lambda w, r, a: ("/api/v1/classes/defaults", None, {}),
    ("GET", "/api/v1/classes/{class_id}"): lambda w, r, a: (
        f"/api/v1/classes/{_ct(r, w, 'class_x', 'class_ix')}",
        None,
        {},
    ),
    ("POST", "/api/v1/classes"): lambda w, r, a: (
        "/api/v1/classes",
        {
            "code": W.unique("M")[:12],
            "display_en": "Matrix class",
            "display_te": "తరగతి",
            "sort_order": 900,
        },
        {},
    ),
    ("PATCH", "/api/v1/classes/{class_id}"): lambda w, r, a: (
        f"/api/v1/classes/{w.a.ids['class_ix']}",
        {},
        _if_match(W.version_of(a, "core.classes", w.a.ids["class_ix"])),
    ),
    ("GET", "/api/v1/sections"): lambda w, r, a: ("/api/v1/sections", None, {}),
    ("GET", "/api/v1/sections/{section_id}"): lambda w, r, a: (
        f"/api/v1/sections/{_ct(r, w, 'section_10a', 'section_9a')}",
        None,
        {},
    ),
    ("POST", "/api/v1/sections"): lambda w, r, a: (
        "/api/v1/sections",
        {
            "academic_year_id": str(w.a.ids["year"]),
            "class_id": str(w.a.ids["class_ix"]),
            "name": W.unique("S")[:10],
        },
        {},
    ),
    ("PATCH", "/api/v1/sections/{section_id}"): lambda w, r, a: (
        f"/api/v1/sections/{w.a.ids['section_9a']}",
        {},
        _if_match(W.version_of(a, "core.sections", w.a.ids["section_9a"])),
    ),
    ("GET", "/api/v1/audit/events"): lambda w, r, a: ("/api/v1/audit/events", None, {}),
    ("GET", "/api/v1/audit/verify"): lambda w, r, a: ("/api/v1/audit/verify", None, {}),
}


def _route_table() -> dict[tuple[str, str], Any]:
    table: dict[tuple[str, str], Any] = {}
    for rc in iter_route_contexts(create_app().routes):
        route = rc.original_route
        if not isinstance(route, APIRoute):
            continue
        guard = [d.call for d in route.dependant.dependencies if hasattr(d.call, "sos_permission")]
        if not guard:
            continue  # public health checks
        for method in rc.methods or ():
            table[(method, str(rc.path))] = guard[0]
    return table


ROUTES = _route_table()
ROLES = tuple(system_roles())
STEP_UP_ROUTES = sorted(k for k, g in ROUTES.items() if g.sos_step_up)


def _success(method: str, path: str) -> int:
    creates = {"/api/v1/users", "/api/v1/academic-years", "/api/v1/classes", "/api/v1/sections"}
    return 201 if method == "POST" and path in creates else 200


def _call(api: Any, w: Any, admin: Engine, role: str, key: tuple[str, str], **kw: Any) -> Any:
    path, body, headers = SPECS[key](w, role, admin)
    return api.call(w.person(role), key[0], path, json=body, headers=headers, **kw)


def test_SEC_003_matrix_covers_every_protected_route() -> None:
    assert set(SPECS) == set(ROUTES)


@pytest.mark.parametrize("role", ROLES)
@pytest.mark.parametrize("key", sorted(SPECS), ids=lambda k: f"{k[0]} {k[1]}")
def test_SEC_003_role_route_matrix(
    world: Any, api: Any, admin_engine: Engine, role: str, key: tuple[str, str]
) -> None:
    permission = ROUTES[key].sos_permission
    granted = permission == AUTHENTICATED or permission in system_roles()[role].permission_keys
    res = _call(api, world, admin_engine, role, key)
    expected = _success(*key) if granted else 403
    assert res.status_code == expected, f"{role} {key}: {res.status_code} {res.text}"


@pytest.mark.parametrize("key", STEP_UP_ROUTES, ids=lambda k: f"{k[0]} {k[1]}")
def test_SEC_005_step_up_routes_need_recent_mfa(
    world: Any, api: Any, admin_engine: Engine, key: tuple[str, str]
) -> None:
    permission = ROUTES[key].sos_permission
    holders = [r for r in ROLES if permission in system_roles()[r].permission_keys]
    assert holders
    for role in holders:
        res = _call(api, world, admin_engine, role, key, auth_age_s=301)
        assert res.status_code == 428, (role, res.text)
        assert res.json()["code"] == "step_up_required"


@pytest.mark.parametrize("key", sorted(SPECS), ids=lambda k: f"{k[0]} {k[1]}")
def test_FR_IAM_002_privileged_roles_need_mfa_on_every_route(
    world: Any, api: Any, admin_engine: Engine, key: tuple[str, str]
) -> None:
    for role in ("owner", "principal", "office_admin"):
        res = _call(api, world, admin_engine, role, key, mfa=False)
        assert res.status_code == 403, (role, res.text)
        assert res.json()["code"] == "mfa_required"
