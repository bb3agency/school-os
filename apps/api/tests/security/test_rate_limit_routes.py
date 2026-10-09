"""Every route resolves to rate-limit policies, or is explicitly exempt (P2-07, OWASP API4:2023).

The per-IP layer runs in the middleware for every request that is not exempt; layers 2-4 run
inside the route guards (``authz.require`` / ``require_any`` / ``require_principal`` call
``app.core.ratelimit.enforce``; ``require_platform`` does the same per operator). Route budgets
shared by a whole school (``per: school``) are charged by the tenant guards only after
authorization (``ratelimit.charge_school_routes``; owner decision 2026-10-07). A new guard
class that skips the limiter, a stale route in ``rate_limits.yaml``, an exempt path that is not a
health check, or an expensive-looking route without a stricter per-route budget fails here, so a
new route cannot silently skip limiting.
"""

from __future__ import annotations

import inspect
import re
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute, iter_route_contexts

from app.authz.dependencies import AnyOfRequirement, PrincipalRequirement, Requirement
from app.core import ratelimit as rl
from app.core.health import PUBLIC_PATHS
from app.main import create_app
from app.platform.auth import RequirePlatform
from app.platform.fleet import RequireFleetSignature
from app.tally.agent_auth import RequireEdgeAgentEnrolment, RequireEdgeAgentSignature

LIMITED_GUARDS: tuple[type[Any], ...] = (
    Requirement,
    AnyOfRequirement,
    PrincipalRequirement,
    RequirePlatform,
)
MACHINE_GUARDS: tuple[type[Any], ...] = (
    RequireFleetSignature,
    RequireEdgeAgentEnrolment,
    RequireEdgeAgentSignature,
)
# Routes that look expensive or abuse-prone and therefore need a stricter per-route budget.
EXPENSIVE = re.compile(
    r"(/exports?(/|$)|/export$|tenant-export$|/imports(/|$)|import-templates$|/uploads$|/sheet$"
    r"|/sheet/versions$|/search$|/render$|/print$|/preview$|promotions:preview$|/duplicates$"
    r"|/invitation-email$|owner-invite:resend$|/dq/runs$|/support/tickets$|support-session$"
    r"|/login-event$)"
)
READ_ONLY_EXPENSIVE_OK = {
    # Reads of an export or import that already exists (status, rows, sheet view): the user layer
    # covers them; the work happened when it was created.
    ("GET", "/api/v1/exports"),
    ("GET", "/api/v1/exports/{export_id}"),
    ("GET", "/api/v1/exports/{export_id}/download-url"),
    ("GET", "/api/v1/imports"),
    ("GET", "/api/v1/imports/{import_id}"),
    ("GET", "/api/v1/imports/{import_id}/rows"),
    ("GET", "/api/v1/imports/{import_id}/sheet"),
    ("GET", "/api/v1/documents/{document_id}/sheet"),
    ("GET", "/api/v1/admin/tenant-export"),
    ("GET", "/api/v1/support/tickets"),
    ("GET", "/api/v1/platform/support/tickets"),
    ("GET", "/api/v1/import-templates"),
    ("PATCH", "/api/v1/imports/{import_id}/sheet/rows/{row_no}"),
}


@pytest.fixture(scope="module")
def app() -> FastAPI:
    return create_app()


def routes(app: FastAPI) -> list[tuple[str, str, APIRoute]]:
    out: list[tuple[str, str, APIRoute]] = []
    for rc in iter_route_contexts(app.routes):
        if isinstance(rc.original_route, APIRoute):
            for method in sorted(rc.methods or ()):
                out.append((method, str(rc.path), rc.original_route))
    return out


def guard_of(route: APIRoute) -> Any:
    found = [d.call for d in route.dependant.dependencies if hasattr(d.call, "sos_permission")]
    return found[0] if len(found) == 1 else None


def test_P2_07_every_route_resolves_to_policies_or_is_exempt(app: FastAPI) -> None:
    config = rl.load_config()
    problems: list[str] = []
    for method, path, route in routes(app):
        if path in config.exempt_paths:
            continue
        guard = guard_of(route)
        if config.is_machine_path(path):
            if not isinstance(guard, MACHINE_GUARDS):
                problems.append(f"{method} {path}: machine path without a machine guard")
            continue
        if not isinstance(guard, LIMITED_GUARDS):
            problems.append(f"{method} {path}: guard {guard!r} does not apply the rate limiter")
    assert problems == []


def test_P2_07_only_health_checks_are_exempt() -> None:
    assert set(rl.load_config().exempt_paths) == set(PUBLIC_PATHS)


def test_P2_07_limited_guards_call_enforce() -> None:
    """The guard classes the enumeration accepts really call the limiter."""
    for guard in LIMITED_GUARDS:
        source = inspect.getsource(guard.__call__)
        assert "limit(" in source or "ratelimit.enforce(" in source, guard.__name__


def test_P2_07_route_policies_name_real_routes(app: FastAPI) -> None:
    known = {f"{method} {path}" for method, path, _ in routes(app)}
    stale = sorted(set(rl.load_config().routes) - known)
    assert stale == []


def test_P2_07_expensive_routes_have_a_stricter_budget(app: FastAPI) -> None:
    config = rl.load_config()
    missing = [
        f"{method} {path}"
        for method, path, _ in routes(app)
        if EXPENSIVE.search(path)
        and not config.is_machine_path(path)
        and (method, path) not in READ_ONLY_EXPENSIVE_OK
        and not config.route_policies(method, path)
    ]
    assert missing == []


def test_P2_07_platform_routes_use_operator_scoped_route_policies(app: FastAPI) -> None:
    config = rl.load_config()
    for method, path, _ in routes(app):
        for policy in config.route_policies(method, path):
            if path.startswith("/api/v1/platform/"):
                assert policy.per in {"user", "operator"}, f"{method} {path}: {policy.name}"


def test_school_budgets_are_charged_by_tenant_guards_after_authorization() -> None:
    """Per-school route budgets are charged last in the tenant guards (owner decision
    2026-10-07): after the permission, scope, step-up and break-glass checks, never before;
    ``enforce`` (before the permission check) leaves them out."""
    for guard in (Requirement, AnyOfRequirement):
        source = inspect.getsource(guard.__call__)
        charged = source.index("ratelimit.charge_school_routes(")
        assert charged > source.index("raise Forbidden()"), guard.__name__
        assert charged > source.index("breakglass_guard.enforce("), guard.__name__
        if "require_recent_auth(" in source:
            assert charged > source.index("require_recent_auth("), guard.__name__
    assert 'policy.per != "school"' in inspect.getsource(rl.enforce)


def test_school_budgets_sit_only_on_tenant_guarded_routes(app: FastAPI) -> None:
    """A ``per: school`` route policy on a route without a tenant guard would never be charged
    (no school, or a guard that does not call ``charge_school_routes``)."""
    config = rl.load_config()
    problems = [
        f"{method} {path}: {policy.name}"
        for method, path, route in routes(app)
        for policy in config.route_policies(method, path)
        if policy.per == "school" and not isinstance(guard_of(route), Requirement)
    ]
    assert problems == []
