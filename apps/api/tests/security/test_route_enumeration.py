"""Route enumeration (SEC-003, CLAUDE.md invariant 2, docs/12 §4.1).

Every API route (except the public health checks) declares exactly one guard from ``require()``
or ``require_principal()`` whose permission exists in the catalog (``permissions.yaml`` and
``core.permissions``); step-up flags agree with the catalog; platform permissions never guard
tenant routes; dedicated deployments mount no control-plane routes.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute, iter_route_contexts
from sqlalchemy import Engine, text

from app.authz.catalog import AUTHENTICATED, CatalogError, permission_catalog
from app.authz.dependencies import require
from app.core.config import DeploymentMode, Settings
from app.core.health import PUBLIC_PATHS
from app.main import create_app

# Routes that authenticate the caller but do not need a resolved school.
TENANTLESS = {
    ("GET", "/api/v1/me/schools"),
    ("POST", "/api/v1/me/active-tenant"),
    ("POST", "/api/v1/me/login-event"),
}
MUTATING = {"POST", "PUT", "PATCH", "DELETE"}


def api_routes(app: FastAPI) -> list[tuple[str, str, APIRoute]]:
    out: list[tuple[str, str, APIRoute]] = []
    for rc in iter_route_contexts(app.routes):
        route = rc.original_route
        if isinstance(route, APIRoute):
            for method in sorted(rc.methods or ()):
                out.append((method, str(rc.path), route))
    return out


def guards(route: APIRoute) -> list[Any]:
    return [d.call for d in route.dependant.dependencies if hasattr(d.call, "sos_permission")]


@pytest.fixture(scope="module")
def app() -> FastAPI:
    return create_app()


def test_SEC_003_every_route_has_exactly_one_known_guard(app: FastAPI) -> None:
    routes = api_routes(app)
    assert len(routes) > 30, "the tenant routers are mounted"
    catalog = permission_catalog()
    problems: list[str] = []
    for method, path, route in routes:
        if path in PUBLIC_PATHS:
            assert guards(route) == [], path
            continue
        found = guards(route)
        if len(found) != 1:
            problems.append(f"{method} {path}: {len(found)} guards")
            continue
        perm = found[0].sos_permission
        pdef = catalog.get(perm)
        if pdef is None:
            problems.append(f"{method} {path}: unknown permission {perm}")
            continue
        if pdef.is_platform != path.startswith("/api/v1/platform/"):
            problems.append(f"{method} {path}: platform/tenant permission mismatch ({perm})")
        if getattr(found[0], "sos_tenantless", False) != ((method, path) in TENANTLESS):
            problems.append(f"{method} {path}: unexpected tenantless guard")
    assert problems == []


def test_SEC_005_step_up_flags_match_catalog(app: FastAPI) -> None:
    catalog = permission_catalog()
    problems: list[str] = []
    for method, path, route in api_routes(app):
        for guard in guards(route):
            pdef = catalog[guard.sos_permission]
            if guard.sos_step_up and not pdef.step_up:
                problems.append(f"{method} {path}: step-up on a non-step-up permission")
            if method in MUTATING and pdef.step_up and not guard.sos_step_up:
                problems.append(f"{method} {path}: {pdef.key} changes need step-up")
    assert problems == []


def test_SEC_003_only_docs_and_health_are_not_api_routes(app: FastAPI) -> None:
    allowed = {app.openapi_url, app.docs_url, app.swagger_ui_oauth2_redirect_url} | PUBLIC_PATHS
    others = [
        str(rc.path)
        for rc in iter_route_contexts(app.routes)
        if not isinstance(rc.original_route, APIRoute)
    ]
    assert set(others) <= allowed


@pytest.mark.db
def test_SEC_003_route_permissions_exist_in_core_permissions(
    app: FastAPI, app_engine: Engine
) -> None:
    with app_engine.connect() as conn:
        keys: set[str] = set(conn.execute(text("SELECT key FROM core.permissions")).scalars())
    used = {g.sos_permission for _, _, r in api_routes(app) for g in guards(r)}
    assert used
    assert used <= keys


def test_SEC_003_require_rejects_unknown_platform_and_bad_step_up() -> None:
    with pytest.raises(CatalogError):
        require("student.teleport")
    with pytest.raises(CatalogError):
        require("platform.tenants.read")
    with pytest.raises(CatalogError):
        require("audit.read", step_up=True)
    with pytest.raises(CatalogError):
        require("audit.read", scope="galaxy")  # type: ignore[arg-type]
    guard = require("user.manage", step_up=True)
    assert (guard.sos_permission, guard.sos_step_up) == ("user.manage", True)
    assert require(AUTHENTICATED).sos_permission == AUTHENTICATED


def test_SEC_026_dedicated_mode_mounts_no_control_plane_routes() -> None:
    app = create_app(Settings(deployment_mode=DeploymentMode.DEDICATED))
    paths = [path for _, path, _ in api_routes(app)]
    assert not [p for p in paths if p.startswith(("/api/v1/platform/", "/api/v1/fleet/"))]
    assert "/api/v1/me" in paths, "school routes stay on in dedicated mode"
