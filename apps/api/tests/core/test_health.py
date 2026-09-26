"""Health endpoints and public-route allowlist (docs/10 §6, CLAUDE.md §6.2)."""

from __future__ import annotations

from collections.abc import Callable

from app.core.health import PUBLIC_PATHS, get_checks
from app.main import create_app
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

DOC_PATHS = {"/api/v1/openapi.json", "/api/v1/docs", "/api/v1/docs/oauth2-redirect"}


def _client(checks: dict[str, Callable[[], bool]]) -> TestClient:
    app = create_app()
    app.dependency_overrides[get_checks] = lambda: checks
    return TestClient(app)


def test_healthz_is_ok_without_dependencies() -> None:
    res = TestClient(create_app()).get("/healthz")
    assert res.status_code == 200
    assert res.json() == {"status": "ok"}


def test_readyz_ok_when_all_dependencies_up() -> None:
    res = _client({"database": lambda: True, "redis": lambda: True}).get("/readyz")
    assert res.status_code == 200
    assert res.json() == {"status": "ok", "checks": {"database": "ok", "redis": "ok"}}


def test_readyz_503_when_database_down() -> None:
    res = _client({"database": lambda: False, "redis": lambda: True}).get("/readyz")
    assert res.status_code == 503
    assert res.json()["checks"]["database"] == "fail"


def test_SEC_003_only_health_routes_are_unprotected() -> None:
    """Every API route must declare require(); only health checks are public.

    ``require()`` dependencies carry a ``sos_permission`` attribute (authz module contract).
    """
    app = create_app()
    unprotected: list[str] = []
    for route in app.routes:
        if not isinstance(route, APIRoute) or route.path in PUBLIC_PATHS | DOC_PATHS:
            continue
        deps = [d.call for d in route.dependant.dependencies]
        if not any(getattr(dep, "sos_permission", None) for dep in deps):
            unprotected.append(f"{sorted(route.methods or set())} {route.path}")
    assert unprotected == []
