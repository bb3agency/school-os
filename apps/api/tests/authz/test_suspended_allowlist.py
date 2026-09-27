"""Pins the suspended-school allowlist (BR-08, FR-PLT-004, FR-TEN-002; decision 2026-09-27).

While a school is suspended (or offboarding) only the routes below remain, and only for the
owner and principal. The list lives in ``app.authz.resolver.SUSPENDED_SCHOOL_ALLOWLIST``; this
test fails when anyone widens it, or when a listed route disappears or loses its guard. The
end-to-end behaviour (every other route answers 403 ``tenant_suspended``) is in
``tests/api/test_suspended_school.py``.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute, iter_route_contexts

from app.authz.catalog import system_roles
from app.authz.dependencies import route_of
from app.authz.resolver import (
    ME_ACTIVE_TENANT,
    ME_LOGIN_EVENT,
    SUSPENDED_MESSAGE,
    SUSPENDED_ROLES,
    SUSPENDED_SCHOOL_ALLOWLIST,
    RouteKey,
    suspended_access_allowed,
)
from app.main import create_app

PINNED = {
    ("GET", "/api/v1/me", frozenset({"owner", "principal"})),
    ("POST", "/api/v1/me/active-tenant", frozenset({"owner", "principal"})),
    ("POST", "/api/v1/me/login-event", frozenset({"owner", "principal"})),
    ("GET", "/api/v1/tenant/billing", frozenset({"owner", "principal"})),
    ("GET", "/api/v1/tenant/billing/invoices", frozenset({"owner", "principal"})),
}


@pytest.fixture(scope="module")
def app() -> FastAPI:
    return create_app()


def _routes(app: FastAPI) -> dict[tuple[str, str], APIRoute]:
    out: dict[tuple[str, str], APIRoute] = {}
    for rc in iter_route_contexts(app.routes):
        if isinstance(rc.original_route, APIRoute):
            for method in rc.methods or ():
                out[(method, str(rc.path))] = rc.original_route
    return out


def test_BR_08_allowlist_is_exactly_the_pinned_set() -> None:
    got = {(e.method, e.path, e.roles) for e in SUSPENDED_SCHOOL_ALLOWLIST}
    assert got == PINNED
    assert len(SUSPENDED_SCHOOL_ALLOWLIST) == len(PINNED), "no duplicate entries"
    assert frozenset({"owner", "principal"}) == SUSPENDED_ROLES


def test_BR_08_allowlisted_roles_are_system_roles_and_never_break_glass() -> None:
    roles = set(system_roles())
    for entry in SUSPENDED_SCHOOL_ALLOWLIST:
        assert entry.roles <= roles
        assert "platform_support" not in entry.roles


def test_BR_08_every_allowlisted_route_exists_and_is_guarded(app: FastAPI) -> None:
    routes = _routes(app)
    for entry in SUSPENDED_SCHOOL_ALLOWLIST:
        route = routes.get((entry.method, entry.path))
        assert route is not None, f"{entry.method} {entry.path} is not a route"
        guards = [d.call for d in route.dependant.dependencies if hasattr(d.call, "sos_permission")]
        assert len(guards) == 1, f"{entry.method} {entry.path} needs its route guard"
        # The allowlist never bypasses the permission: GET routes are read-only views.
        assert entry.method in {"GET"} or entry.key in {ME_ACTIVE_TENANT, ME_LOGIN_EVENT}


def test_BR_08_route_matching_is_exact_and_fails_closed() -> None:
    owner = frozenset({"owner"})
    assert suspended_access_allowed(RouteKey("GET", "/api/v1/tenant/billing"), owner)
    assert suspended_access_allowed(RouteKey("GET", "/api/v1/me"), frozenset({"principal"}))
    assert not suspended_access_allowed(RouteKey("POST", "/api/v1/tenant/billing"), owner)
    assert not suspended_access_allowed(RouteKey("GET", "/api/v1/tenant/billing/"), owner)
    assert not suspended_access_allowed(RouteKey("GET", "/api/v1/users"), owner)
    assert not suspended_access_allowed(None, owner)
    for role in ("accountant", "office_admin", "office_staff", "teacher", "platform_support"):
        assert not suspended_access_allowed(
            RouteKey("GET", "/api/v1/tenant/billing"), frozenset({role})
        )
    both = frozenset({"accountant", "principal"})
    assert suspended_access_allowed(RouteKey("GET", "/api/v1/tenant/billing"), both)


def test_BR_08_route_of_reads_the_matched_template() -> None:
    def request(route: Any, method: str = "GET") -> Any:
        return SimpleNamespace(method=method, scope={"route": route} if route else {})

    template = SimpleNamespace(path="/api/v1/students/{student_id}")
    assert route_of(request(template)) == RouteKey("GET", "/api/v1/students/{student_id}")
    assert route_of(request(None)) is None
    assert route_of(request(SimpleNamespace(path=None))) is None


def test_BR_08_message_is_plain_and_says_what_still_works() -> None:
    assert "suspended" in SUSPENDED_MESSAGE
    assert "Plan & billing" in SUSPENDED_MESSAGE
    assert "export" in SUSPENDED_MESSAGE
    assert "support" in SUSPENDED_MESSAGE
