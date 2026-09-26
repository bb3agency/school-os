"""Object-level authorization and cross-tenant isolation over the API (SEC-015, SEC-001,
invariant 3, docs/12 §4.3-4.4).

- Every route with an ID in its path answers 404 for another school's ID and for a random ID,
  even for the owner (never reveal existence), and changes nothing.
- Scoped roles get 404 for classes/sections outside their scopes.
- Lists never contain another school's objects; bodies cannot reference them.
"""

from __future__ import annotations

import importlib.util
import sys
import uuid
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from fastapi.routing import APIRoute, iter_route_contexts
from sqlalchemy import Engine

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

# Minimal valid bodies so the request reaches the object lookup.
BODIES: dict[tuple[str, str], dict[str, Any] | None] = {
    ("PATCH", "/api/v1/users/{user_id}"): {"status": "active"},
    ("PUT", "/api/v1/users/{user_id}/roles"): {"roles": ["teacher"]},
    ("PUT", "/api/v1/users/{user_id}/scopes"): {"scopes": []},
    ("PATCH", "/api/v1/academic-years/{year_id}"): {},
    ("PATCH", "/api/v1/classes/{class_id}"): {},
    ("PATCH", "/api/v1/sections/{section_id}"): {},
}
PARAM_TO_B = {
    "user_id": lambda w: w.b.people["target"].user_id,
    "year_id": lambda w: w.b.ids["year"],
    "class_id": lambda w: w.b.ids["class_ix"],
    "section_id": lambda w: w.b.ids["section_9a"],
}


def _id_routes() -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for rc in iter_route_contexts(create_app().routes):
        if isinstance(rc.original_route, APIRoute) and "{" in str(rc.path):
            out.extend((m, str(rc.path)) for m in sorted(rc.methods or ()))
    return sorted(out)


ID_ROUTES = _id_routes()


def _fill(path: str, value: uuid.UUID) -> str:
    for param in PARAM_TO_B:
        path = path.replace("{" + param + "}", str(value))
    return path


def _param(path: str) -> str:
    return next(p for p in PARAM_TO_B if "{" + p + "}" in path)


def test_SEC_015_every_id_route_is_covered() -> None:
    assert ID_ROUTES
    for method, path in ID_ROUTES:
        assert _param(path)
        if method != "GET":
            assert (method, path) in BODIES or path.endswith("/make-current"), path


@pytest.mark.parametrize("key", ID_ROUTES, ids=lambda k: f"{k[0]} {k[1]}")
def test_SEC_001_other_school_ids_are_404(
    world: Any, api: Any, admin_engine: Engine, key: tuple[str, str]
) -> None:
    method, template = key
    b_id = PARAM_TO_B[_param(template)](world)
    before = W.audit_events(admin_engine, world.b.tenant_id)
    bodies = []
    for target in (b_id, uuid.uuid4()):
        res = api.call(
            world.person("owner"),
            method,
            _fill(template, target),
            json=BODIES.get(key),
            headers={"If-Match": 'W/"1"'},
        )
        assert res.status_code == 404, (key, res.text)
        body = res.json()
        bodies.append({k: body.get(k) for k in ("status", "code", "title", "detail")})
    assert bodies[0] == bodies[1], "another school's ID is indistinguishable from a random one"
    assert W.audit_events(admin_engine, world.b.tenant_id) == before, "school B unchanged"


@pytest.mark.parametrize(
    ("role", "path_key"),
    [
        ("class_teacher", "section_9c"),
        ("class_teacher", "section_10a"),
        ("teacher", "section_9a"),
        ("teacher", "section_9c"),
    ],
)
def test_SEC_015_section_outside_scope_is_404(
    world: Any, api: Any, role: str, path_key: str
) -> None:
    res = api.call(world.person(role), "GET", f"/api/v1/sections/{world.a.ids[path_key]}")
    assert res.status_code == 404


@pytest.mark.parametrize(("role", "klass"), [("class_teacher", "class_x"), ("teacher", "class_ix")])
def test_SEC_015_class_outside_scope_is_404(world: Any, api: Any, role: str, klass: str) -> None:
    res = api.call(world.person(role), "GET", f"/api/v1/classes/{world.a.ids[klass]}")
    assert res.status_code == 404


def test_SEC_015_in_scope_objects_are_visible(world: Any, api: Any) -> None:
    ct = world.person("class_teacher")
    assert api.call(ct, "GET", f"/api/v1/sections/{world.a.ids['section_9a']}").status_code == 200
    assert api.call(ct, "GET", f"/api/v1/classes/{world.a.ids['class_ix']}").status_code == 200


@pytest.mark.parametrize(
    "path", ["/api/v1/users", "/api/v1/academic-years", "/api/v1/classes", "/api/v1/sections"]
)
def test_SEC_001_lists_never_show_other_school(world: Any, api: Any, path: str) -> None:
    res = api.call(world.person("owner"), "GET", path, params={"limit": 200})
    assert res.status_code == 200
    b_ids = {str(v) for v in world.b.ids.values()}
    b_ids |= {str(p.user_id) for p in world.b.people.values()}
    assert not {item["id"] for item in res.json()["data"]} & b_ids


def test_SEC_001_bodies_cannot_reference_other_school(world: Any, api: Any) -> None:
    owner = world.person("owner")
    res = api.call(
        owner,
        "POST",
        "/api/v1/sections",
        json={
            "academic_year_id": str(world.a.ids["year"]),
            "class_id": str(world.b.ids["class_ix"]),
            "name": "Z",
        },
    )
    assert res.status_code == 422
    res = api.call(
        owner,
        "POST",
        "/api/v1/sections",
        json={
            "academic_year_id": str(world.a.ids["year"]),
            "class_id": str(world.a.ids["class_ix"]),
            "name": "Y",
            "class_teacher_membership_id": str(world.b.people["target"].membership_id),
        },
    )
    assert res.status_code == 422
    res = api.call(
        owner,
        "PUT",
        f"/api/v1/users/{world.a.people['target'].user_id}/scopes",
        json={"scopes": [{"type": "class", "ref": str(world.b.ids["class_ix"])}]},
    )
    assert res.status_code == 422


def test_SEC_001_other_school_owner_sees_only_own_tenant(world: Any, api: Any) -> None:
    b_owner = world.b.people["owner"]
    me = api.call(b_owner, "GET", "/api/v1/me").json()
    assert me["tenant_id"] == str(world.b.tenant_id)
    tenant = api.call(b_owner, "GET", "/api/v1/tenant").json()
    assert tenant["id"] == str(world.b.tenant_id)
    res = api.call(b_owner, "GET", "/api/v1/me", tenant=world.a.tenant_id)
    assert res.status_code == 403
