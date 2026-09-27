"""``require_any()``: a route guard satisfied by any one of several tenant permissions
(SEC-003, CLAUDE.md §6.2).

The guard passes exactly when the caller holds one of the listed permissions: no scope rule
and, unless ``step_up=True``, no step-up (a read shared by the maker and the checker, or by the
export permissions), 403 for everyone else. With ``step_up=True`` (creating a pre-check export,
ADR-0021) every listed permission must be a step-up permission and the caller also needs MFA
within 5 minutes (428), checked after the permission (403 first). Objects outside the caller's
scope or school are the service's 404, not the guard's. Break-glass sessions go through the
same read-only guard as ``require()``, recorded under the first listed permission the caller
holds. The matrix and route-enumeration tests read ``sos_permission``, ``sos_any_of`` and
``sos_step_up``.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import uuid
from collections.abc import Callable
from typing import Any

import pytest
from fastapi import Depends, FastAPI, Request
from fastapi.routing import APIRoute, iter_route_contexts
from fastapi.testclient import TestClient

from app.authz import breakglass_guard
from app.authz.catalog import CatalogError
from app.authz.context import Scopes, UserContext
from app.authz.dependencies import AnyOfRequirement, Requirement, get_user_context, require_any
from app.core.errors import install_error_handlers
from app.identity.principal import Principal, get_principal
from app.main import create_app

REQUEST = "student.identity_change.request"
APPROVE = "student.identity_change.approve"
BOARD, PORTAL, STUDENT_EXPORT = "export.board", "export.portal", "student.export"
READ_ALL, DOWNLOAD_ANY = "export.read_all", "export.download_any"


def _ctx(*permissions: str, scoped: bool = False, breakglass: bool = False) -> UserContext:
    held = frozenset({"session.authenticated", *permissions})
    return UserContext(
        user_id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        membership_id=uuid.uuid4(),
        roles=frozenset({"platform_support" if breakglass else "teacher"}),
        permissions=held,
        scopes=Scopes(section_ids=frozenset({uuid.uuid4()})) if scoped else Scopes(school=True),
        scoped_permissions=held if scoped else frozenset(),
        mfa=True,
        auth_time=None,
        via_breakglass=breakglass,
    )


def _principal(*, mfa: bool = True, age_s: int = 0) -> Principal:
    now = dt.datetime.now(dt.UTC)
    return Principal(
        subject="synthetic-sub",
        issuer="https://idp.synthetic.test/pool",
        kind="user",
        auth_time=now - dt.timedelta(seconds=age_s),
        mfa=mfa,
        session_id=None,
        expires_at=now + dt.timedelta(minutes=5),
    )


def _client(guard: AnyOfRequirement, ctx: UserContext, principal: Principal) -> TestClient:
    app = FastAPI()
    install_error_handlers(app)
    # A default rather than a local ``Annotated`` alias: string annotations
    # (``from __future__ import annotations``) cannot see function locals.

    @app.get("/probe")
    def read(ctx: UserContext = Depends(guard)) -> dict[str, str]:  # noqa: B008
        return {"membership_id": str(ctx.membership_id)}

    @app.post("/probe")
    def write(ctx: UserContext = Depends(guard)) -> dict[str, str]:  # noqa: B008
        return {"membership_id": str(ctx.membership_id)}

    app.dependency_overrides[get_user_context] = lambda: ctx
    app.dependency_overrides[get_principal] = lambda: principal
    return TestClient(app)


@pytest.fixture
def breakglass_calls(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:
    """Record (method, permission) for each call of the real break-glass guard; its audit write
    goes to a stub instead of the school's chain (tests/breakglass covers the database side)."""
    calls: list[tuple[str, str]] = []
    real: Callable[[UserContext, Request, str], None] = breakglass_guard.enforce

    def spy(ctx: UserContext, request: Request, permission: str) -> None:
        calls.append((request.method, permission))
        real(ctx, request, permission)

    class _Audit:
        @staticmethod
        def record(session: Any, **kwargs: Any) -> None:
            assert kwargs["summary"]["via_breakglass"] is True

    monkeypatch.setattr(breakglass_guard, "enforce", spy)
    monkeypatch.setattr(breakglass_guard, "tenant_session", lambda *a: contextlib.nullcontext())
    monkeypatch.setattr(breakglass_guard, "audit", _Audit)
    return calls


def test_SEC_003_require_any_carries_the_route_enumeration_contract() -> None:
    guard = require_any(BOARD, PORTAL, STUDENT_EXPORT)
    assert isinstance(guard, AnyOfRequirement)
    assert isinstance(guard, Requirement)  # the suspended-school test lists Requirement guards
    assert guard.sos_permission == BOARD
    assert guard.sos_any_of == (PORTAL, STUDENT_EXPORT)
    assert guard.sos_scope is None
    assert guard.sos_step_up is False
    assert repr(guard) == "require_any('export.board', 'export.portal', 'student.export')"


@pytest.mark.parametrize(
    "keys",
    [
        ("no.such.permission", PORTAL),
        (BOARD, "no.such.permission"),
        (BOARD, "platform.tenants.read"),
        ("platform.tenants.read", BOARD),
    ],
)
def test_SEC_003_require_any_refuses_unknown_and_platform_permissions(
    keys: tuple[str, str],
) -> None:
    with pytest.raises(CatalogError):
        require_any(*keys)


@pytest.mark.parametrize("held", [REQUEST, APPROVE])
def test_SEC_003_require_any_passes_with_each_alternative(
    held: str, breakglass_calls: list[tuple[str, str]]
) -> None:
    ctx = _ctx(held)
    with _client(require_any(REQUEST, APPROVE), ctx, _principal()) as client:
        res = client.get("/probe")
    assert res.status_code == 200, res.text
    assert res.json() == {"membership_id": str(ctx.membership_id)}
    assert breakglass_calls == [("GET", held)]


def test_SEC_003_require_any_passes_with_the_third_alternative() -> None:
    guard = require_any(BOARD, PORTAL, STUDENT_EXPORT)
    with _client(guard, _ctx(STUDENT_EXPORT), _principal()) as client:
        assert client.get("/probe").status_code == 200


@pytest.mark.parametrize(
    "held",
    [
        (),
        ("student.read_basic", "document.read"),
        (STUDENT_EXPORT,),  # listed on other guards, not on this one
    ],
)
def test_SEC_003_require_any_refuses_callers_holding_none(
    held: tuple[str, ...], breakglass_calls: list[tuple[str, str]]
) -> None:
    with _client(require_any(BOARD, PORTAL), _ctx(*held), _principal()) as client:
        for method in ("GET", "POST"):
            res = client.request(method, "/probe")
            assert res.status_code == 403, res.text
            assert res.json()["code"] == "forbidden"
    assert breakglass_calls == []  # refused before the break-glass guard


def test_SEC_003_require_any_does_not_apply_a_scope_rule() -> None:
    # Scoped ("S") holders pass the guard; the service filters objects by scope (404 outside it).
    with _client(require_any(REQUEST, APPROVE), _ctx(REQUEST, scoped=True), _principal()) as c:
        assert c.get("/probe").status_code == 200


@pytest.mark.parametrize(
    "principal",
    [_principal(age_s=3600), _principal(mfa=False)],
    ids=["stale-auth", "no-mfa"],
)
def test_SEC_005_require_any_does_not_ask_for_step_up(principal: Principal) -> None:
    with _client(require_any(REQUEST, APPROVE), _ctx(APPROVE), principal) as client:
        assert client.get("/probe").status_code == 200


def test_SEC_003_require_any_records_the_first_held_permission_for_breakglass(
    breakglass_calls: list[tuple[str, str]],
) -> None:
    ctx = _ctx(PORTAL, STUDENT_EXPORT, breakglass=True)
    guard = require_any(BOARD, PORTAL, STUDENT_EXPORT)
    with _client(guard, ctx, _principal()) as client:
        assert client.get("/probe").status_code == 200
        res = client.post("/probe")
    assert res.status_code == 403
    assert res.json()["code"] == "breakglass_read_only"
    assert breakglass_calls == [("GET", PORTAL), ("POST", PORTAL)]


def _any_of_guards() -> list[tuple[str, str, Any]]:
    out: list[tuple[str, str, Any]] = []
    for rc in iter_route_contexts(create_app().routes):
        route = rc.original_route
        if not isinstance(route, APIRoute):
            continue
        for dep in route.dependant.dependencies:
            if getattr(dep.call, "sos_any_of", None) and not str(rc.path).startswith(
                "/api/v1/platform"
            ):
                for method in sorted(rc.methods or ()):
                    out.append((method, str(rc.path), dep.call))
    return out


def test_SEC_003_tenant_any_of_guards_all_use_the_shared_require_any() -> None:
    guards = _any_of_guards()
    readers = (PORTAL, STUDENT_EXPORT, READ_ALL)
    expected = {
        ("GET", "/api/v1/change-requests"): (REQUEST, (APPROVE,), False),
        ("GET", "/api/v1/change-requests/{change_request_id}"): (REQUEST, (APPROVE,), False),
        ("GET", "/api/v1/change-requests/{change_request_id}/memo"): (REQUEST, (APPROVE,), False),
        ("GET", "/api/v1/export-profiles"): (BOARD, (PORTAL,), False),
        # ADR-0021 decision 1: creating any pre-check needs step-up.
        ("POST", "/api/v1/exports"): (BOARD, (PORTAL,), True),
        ("GET", "/api/v1/exports"): (BOARD, readers, False),
        ("GET", "/api/v1/exports/{export_id}"): (BOARD, readers, False),
        ("GET", "/api/v1/exports/{export_id}/download-url"): (
            BOARD,
            (PORTAL, STUDENT_EXPORT, DOWNLOAD_ANY),
            False,
        ),
        # Staff directory (FR-TEN-010): the service requires a school-wide grant of either.
        ("GET", "/api/v1/staff"): ("tenant.structure.manage", ("user.manage",), False),
    }
    assert {
        (m, p): (g.sos_permission, g.sos_any_of, g.sos_step_up) for m, p, g in guards
    } == expected
    assert all(type(g) is AnyOfRequirement for _, _, g in guards)
    assert all(g.sos_scope is None for _, _, g in guards)


# --- step_up=True (ADR-0021, SEC-005) ------------------------------------------------------------


def test_SEC_005_require_any_step_up_carries_the_flag() -> None:
    guard = require_any(BOARD, PORTAL, step_up=True)
    assert guard.sos_step_up is True
    assert (guard.sos_permission, guard.sos_any_of) == (BOARD, (PORTAL,))
    assert repr(guard) == "require_any('export.board', 'export.portal', step_up=True)"


@pytest.mark.parametrize(
    "keys",
    [
        (BOARD, READ_ALL),  # export.read_all is not a step-up permission
        (READ_ALL, BOARD),
        (REQUEST, APPROVE),
    ],
)
def test_SEC_005_require_any_step_up_only_with_step_up_permissions(
    keys: tuple[str, str],
) -> None:
    with pytest.raises(CatalogError):
        require_any(*keys, step_up=True)


@pytest.mark.parametrize("held", [BOARD, PORTAL])
def test_SEC_005_require_any_step_up_passes_with_recent_mfa(held: str) -> None:
    with _client(require_any(BOARD, PORTAL, step_up=True), _ctx(held), _principal()) as client:
        assert client.post("/probe").status_code == 200


@pytest.mark.parametrize(
    "principal",
    [_principal(age_s=301), _principal(mfa=False)],
    ids=["stale-auth", "no-mfa"],
)
@pytest.mark.parametrize("held", [BOARD, PORTAL])
def test_SEC_005_require_any_step_up_refuses_stale_or_non_mfa_sign_in(
    held: str, principal: Principal
) -> None:
    with _client(require_any(BOARD, PORTAL, step_up=True), _ctx(held), principal) as client:
        for method in ("GET", "POST"):
            res = client.request(method, "/probe")
            assert res.status_code == 428, res.text
            assert res.json()["code"] == "step_up_required"


def test_SEC_005_require_any_step_up_checks_permission_first() -> None:
    # 403 before 428: a caller without the permission never learns step-up would help.
    guard = require_any(BOARD, PORTAL, step_up=True)
    with _client(guard, _ctx(STUDENT_EXPORT), _principal(age_s=3600)) as client:
        res = client.post("/probe")
    assert res.status_code == 403
    assert res.json()["code"] == "forbidden"
