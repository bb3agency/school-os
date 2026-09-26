"""Platform authz matrix (FR-PLT-028, SEC-027, SEC-003; docs/12 §4.12, docs/16 §6).

Generated from the route table and ``permissions.yaml``: for every ``/api/v1/platform/*`` route
and every platform role, granted -> not 401/403/428, not granted -> 403, granted but step-up
stale on an ᴿ route -> 428. Also: staff tokens -> 401, unknown/deactivated operators -> 403,
dedicated mode -> 404 and no control-plane beat tasks.
"""

from __future__ import annotations

import re
import uuid
from typing import Any

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.core.config import DeploymentMode, Settings
from app.core.db import platform_session
from app.main import create_app
from app.platform.auth import RequirePlatform
from app.platform.fleet import RequireFleetSignature
from app.platform.permissions import ANY_OPERATOR, catalog
from app.platform.tasks import beat_schedule

from .conftest import ROLES, Api, MakeOperator

pytestmark = pytest.mark.db

# docs/16 §6 == docs/07 §6.5 (✓ per role, in ROLES order). ᴿ = step-up.
DOC_MATRIX: dict[str, tuple[str, bool]] = {
    "platform.tenants.read": ("11111", False),
    "platform.tenants.provision": ("11000", True),
    "platform.tenants.suspend": ("11000", True),
    "platform.tenants.offboard": ("10000", True),
    "platform.plans.manage": ("10010", True),
    "platform.subscriptions.read": ("10011", False),
    "platform.subscriptions.manage": ("10010", True),
    "platform.invoices.read": ("10011", False),
    "platform.invoices.manage": ("10010", False),
    "platform.flags.read": ("11001", False),
    "platform.flags.manage": ("11000", True),
    "platform.usage.read": ("11111", False),
    "platform.fleet.read": ("11101", False),
    "platform.fleet.manage": ("11000", True),
    "platform.announcements.manage": ("10100", False),
    "platform.support.read": ("11101", False),
    "platform.support.manage": ("10100", False),
    "platform.breakglass.request": ("11100", False),
    "platform.breakglass.emergency": ("10000", True),
    "platform.operators.manage": ("10000", True),
    "platform.audit.read": ("11001", False),
}


def test_FR_PLT_028_catalog_matches_documented_matrix() -> None:
    cat = catalog()
    assert set(cat.permissions) == set(DOC_MATRIX)
    for key, (marks, step_up) in DOC_MATRIX.items():
        assert cat.permissions[key].step_up is step_up, key
        for role, mark in zip(ROLES, marks, strict=True):
            assert (key in cat.roles[role]) is (mark == "1"), f"{role} {key}"
    assert cat.permissions["platform.tenants.offboard"].two_person
    assert cat.permissions["platform.breakglass.emergency"].two_person


def api_routes(routes: list[Any]) -> list[APIRoute]:
    """Flatten included routers (FastAPI >= 0.140 keeps them nested in ``app.routes``)."""
    out: list[APIRoute] = []
    for route in routes:
        nested = getattr(route, "original_router", None)
        if nested is not None:
            out.extend(api_routes(list(nested.routes)))
        elif isinstance(route, APIRoute):
            out.append(route)
    return out


def _platform_routes() -> list[APIRoute]:
    return [r for r in api_routes(create_app().routes) if r.path.startswith("/api/v1/platform")]


def _guard(route: APIRoute) -> RequirePlatform:
    guards = [d.call for d in route.dependant.dependencies if isinstance(d.call, RequirePlatform)]
    assert len(guards) == 1, f"{route.path} must declare exactly one require_platform()"
    return guards[0]


def test_SEC_003_every_control_plane_route_declares_require_platform() -> None:
    routes = _platform_routes()
    assert len(routes) > 60
    for route in routes:
        _guard(route)
    hb = [r for r in api_routes(create_app().routes) if r.path == "/api/v1/fleet/heartbeat"]
    assert len(hb) == 1
    assert any(isinstance(d.call, RequireFleetSignature) for d in hb[0].dependant.dependencies)


def _concrete(path: str) -> str:
    path = path.replace("{key}", "synthetic.flag")
    return re.sub(r"\{[a-z_]+\}", lambda _m: str(uuid.uuid4()), path)


CASES = [
    (sorted(route.methods or {"GET"})[0], route.path, _guard(route)) for route in _platform_routes()
]


def _granted(guard: RequirePlatform, role: str) -> bool:
    if ANY_OPERATOR in guard.permissions:
        return True
    return bool(catalog().roles[role] & set(guard.permissions))


@pytest.mark.parametrize("role", ROLES)
def test_FR_PLT_028_authz_matrix_every_route_every_role(
    api: Api, make_operator: MakeOperator, role: str
) -> None:
    op = make_operator(role)
    for method, path, guard in CASES:
        url = _concrete(path).removeprefix("/api/v1/platform")
        body: Any = {} if method in ("POST", "PUT", "PATCH") else None
        res = api.call(method, url, op, json=body)
        if _granted(guard, role):
            assert res.status_code not in (401, 403, 428), f"{role} {method} {path}: {res.text}"
            assert res.status_code < 500, f"{role} {method} {path}: {res.text}"
            if guard.step_up:
                stale = api.call(method, url, op, json=body, fresh=False)
                assert stale.status_code == 428, f"{role} {method} {path} stale: {stale.text}"
                assert stale.json()["code"] == "step_up_required"
        else:
            assert res.status_code == 403, f"{role} {method} {path}: {res.status_code}"


def test_SEC_027_staff_token_is_rejected_on_platform_routes(api: Api) -> None:
    res = api.client.get(
        "/api/v1/platform/dashboard", headers=api.headers(None, tenant_subject="staff-sub")
    )
    assert res.status_code == 401


def test_SEC_027_unknown_and_deactivated_operators_are_forbidden(
    api: Api, make_operator: MakeOperator
) -> None:
    ghost = make_operator("platform_owner")
    with platform_session() as s:
        s.execute(
            text("DELETE FROM platform.operator_roles WHERE operator_id = :o"), {"o": ghost.id}
        )
        s.execute(text("DELETE FROM platform.operators WHERE id = :o"), {"o": ghost.id})
    res = api.call("GET", "/me", ghost)
    assert (res.status_code, res.json()["code"]) == (403, "not_operator")
    gone = make_operator("platform_owner", status="deactivated")
    assert api.call("GET", "/me", gone).status_code == 403


def test_SEC_027_operator_without_mfa_is_refused(api: Api, make_operator: MakeOperator) -> None:
    op = make_operator("platform_owner")
    token = api.platform_idp.mint("synthopsclient", op.subject, mfa=False)
    headers = api.headers(None)
    headers["Authorization"] = f"Bearer {token}"
    res = api.client.get("/api/v1/platform/me", headers=headers)
    assert (res.status_code, res.json()["code"]) == (403, "mfa_required")


def test_FR_PLT_028_invited_operator_is_activated_on_first_mfa_sign_in(
    api: Api, make_operator: MakeOperator
) -> None:
    op = make_operator("support_agent", status="invited")
    res = api.call("GET", "/me", op)
    assert res.status_code == 200, res.text
    assert res.json()["roles"] == ["support_agent"]
    with platform_session() as s:
        status = s.execute(
            text("SELECT status, mfa_enrolled FROM platform.operators WHERE id = :o"), {"o": op.id}
        ).one()
        events: Any = s.execute(
            text(
                "SELECT count(*) FROM platform.audit_events WHERE action = 'operator.activated' "
                "AND resource_id = :o"
            ),
            {"o": op.id},
        ).scalar_one()
    assert tuple(status) == ("active", True)
    assert events == 1


def test_ADR_0017_dedicated_mode_mounts_no_control_plane() -> None:
    app = create_app(Settings(deployment_mode=DeploymentMode.DEDICATED))
    client = TestClient(app)
    assert client.get("/api/v1/platform/me").status_code == 404
    assert client.post("/api/v1/fleet/heartbeat", content=b"{}").status_code == 404
    paths = {r.path for r in api_routes(app.routes)}
    assert not any(p.startswith(("/api/v1/platform", "/api/v1/fleet")) for p in paths)


def test_ADR_0017_beat_schedule_per_deployment_mode() -> None:
    dedicated = beat_schedule(Settings(deployment_mode=DeploymentMode.DEDICATED))
    shared = beat_schedule(Settings(deployment_mode=DeploymentMode.SHARED))
    assert {v["task"] for v in dedicated.values()} == {"fleet.send_heartbeat"}
    assert "fleet.send_heartbeat" not in {v["task"] for v in shared.values()}
    assert {"billing.generate_invoices", "usage.collect_daily", "fleet.check_staleness"} <= {
        v["task"] for v in shared.values()
    }


def test_FR_PLT_028_platform_permissions_come_from_the_single_authz_catalog() -> None:
    """roles.yaml only references platform.* keys of app/authz/permissions.yaml (one catalog),
    and every operator role holds the key used for "any operator" routes."""
    from app.authz.catalog import permission_catalog

    authz = {k for k, v in permission_catalog().items() if v.is_platform}
    assert set(catalog().permissions) == authz
    for role in ROLES:
        assert catalog().roles[role] <= authz
        assert ANY_OPERATOR in catalog().roles[role], role
