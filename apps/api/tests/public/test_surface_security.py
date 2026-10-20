"""Public-surface security suite (R2-3I hardening; ADR-0046, ADR-0047; FR-PUB-001, FR-PUB-011;
SEC-036, SEC-037, SEC-039; owner decision D31). Synthetic data only.

Driven by ``app/public/public_routes.yaml``: every ``public_handle`` route is called the way the
BFF calls it, with live, unknown, malformed, expired, revoked, other-kind and suspended-school
keys, and must show:

- one 404 for every key that does not open a page: same status, same body (``instance`` is the
  route template, never the key), the same path through the guard (exactly one resolver call
  and one charge of the failure budget per request, whatever the reason), so a caller cannot
  tell unknown from expired, revoked, another kind or a suspended school;
- the failed-lookup budget on every route (a used-up budget refuses even a live key: no oracle);
- the BFF service token and no user token on every route;
- ``Cache-Control: no-store``, a ``Referrer-Policy``, ``nosniff`` and no cookie on every answer;
- no key, phone number or token in any response body or log line;
- nothing written for a suspended school (D31): no payment link from a dues link.

The webhook routes have their own suites (tests/messaging/test_webhook_forgery.py,
tests/fees/test_webhook_forgery.py).
"""

from __future__ import annotations

import datetime as dt
import importlib.util
import json
import secrets
import sys
from collections.abc import Callable, Iterator
from pathlib import Path
from types import ModuleType
from typing import Any

import httpx
import pytest
import yaml
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from app.core import ratelimit as rl
from app.core.db import tenant_session
from app.public import service, tokens
from app.public.config import PARENT_KINDS
from app.public.guards import LOOKUP_FAILURE_COST

pytestmark = pytest.mark.db
P = sys.modules["sos_test_public_support"]
W = sys.modules["sos_test_api_world"]
TESTS = Path(__file__).resolve().parents[1]
ROUTES_FILE = Path(__file__).resolve().parents[2] / "app" / "public" / "public_routes.yaml"
NOW = dt.datetime.now(dt.UTC)
_CODE_ALPHABET = "ABCDEFGHJKMNPQRSTVWXYZ0123456789"


def _load(name: str, path: Path) -> ModuleType:
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, path)
        assert spec is not None
        assert spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


F = _load("sos_test_fees_support", TESTS / "fees" / "support.py")
O = _load("sos_test_fees_online_support", TESTS / "fees" / "online_support.py")  # noqa: E741


def _handle_routes() -> dict[str, list[str]]:
    data = yaml.safe_load(ROUTES_FILE.read_text(encoding="utf-8"))
    return {
        route: list(entry["kinds"])
        for route, entry in data["routes"].items()
        if entry["guard"] == "public_handle"
    }


ROUTES = _handle_routes()
# How the BFF sends the key and a valid body, per route (a new public route must be added here).
BODIES: dict[str, dict[str, Any] | None] = {
    "POST /api/v1/public/links/exchange": None,  # the key is the body
    "GET /api/v1/public/links/current": None,
    "GET /api/v1/public/certificates/{code}": None,
    "GET /api/v1/public/links/current/document": None,
    "POST /api/v1/public/links/current/pay": {"instalments": [1]},
    "GET /api/v1/public/links/current/payment": None,
    "POST /api/v1/public/links/current/optin": {"language": "en"},
}


def test_SEC_036_every_public_handle_route_is_in_this_suite() -> None:
    assert set(ROUTES) == set(BODIES), "add the new public route to BODIES in this suite"


def _reset_budgets() -> None:
    rl.get_rate_limiter().store.reset()  # type: ignore[attr-defined]


def _call(client: TestClient, route: str, key: str, **kw: Any) -> httpx.Response:
    method, template = route.split(" ", 1)
    if "{code}" in template:
        path = template.replace("{code}", key)
        response: httpx.Response = P.public_call(client, method, path, **kw)
        return response
    if template.endswith("/exchange"):
        response = P.public_call(client, method, template, body={"token": key}, **kw)
        return response
    response = P.public_call(client, method, template, token=key, body=BODIES[route], **kw)
    return response


def _is_cert(route: str) -> bool:
    return ROUTES[route] == ["cert_verify"]


def _new_code() -> str:
    return "".join(secrets.choice(_CODE_ALPHABET) for _ in range(16))


def _live(school: Any, kind: str) -> str:
    """A live key of ``kind`` in ``school`` (its target need not exist: a dead target is the
    same 404 one step later, covered by test_pages.py)."""
    if kind == "cert_verify":
        code = _new_code()
        issued = P.issue(school, "cert_verify", key=code, target_type="cert")
        assert issued.key is None
        return code
    key: str = P.issue(school, kind).key
    return key


def _revoked(school: Any, kind: str) -> str:
    if kind == "cert_verify":
        code = _new_code()
        issued = P.issue(school, "cert_verify", key=code, target_type="cert")
    else:
        issued = P.issue(school, kind)
        code = issued.key
    with tenant_session(school.tenant_id) as db:
        service.revoke(db, issued.handle_id, reason="manual", by_user=False)
    return str(code)


def _expired(admin: Engine, school: Any, kind: str) -> str:
    key = tokens.new_parent_token()
    P.insert_raw(
        admin,
        school.tenant_id,
        kind=kind,
        key=key,
        created_at=NOW - dt.timedelta(days=40),
        expires_at=NOW - dt.timedelta(minutes=1),
    )
    return key


@pytest.fixture(scope="module")
def suspended_school(world: Any, admin_engine: Engine) -> Iterator[Any]:
    """A school of its own with one live link of every parent kind, then suspended."""
    s = W.School(W.provision_school())
    s.people["owner"] = W.add_member(admin_engine, s.tenant_id, ["owner"])
    s.keys = {kind: P.issue(s, kind).key for kind in PARENT_KINDS}
    P.set_tenant_status(admin_engine, s.tenant_id, "suspended")
    yield s
    P.set_tenant_status(admin_engine, s.tenant_id, "active")


def _dead_keys(route: str, world: Any, admin: Engine, suspended: Any) -> dict[str, str]:
    kinds = ROUTES[route]
    kind = kinds[0]
    keys: dict[str, str] = {
        "unknown": _new_code() if _is_cert(route) else tokens.new_parent_token(),
        "malformed": "not-a-key",
        "revoked": _revoked(world.a, kind),
    }
    if _is_cert(route):
        keys["other_kind"] = _live(world.a, "parent_notice")
        keys["lowercase"] = _new_code().lower()
        return keys  # codes never expire and still open while a school is suspended
    keys["empty"] = ""
    keys["expired"] = _expired(admin, world.a, kind)
    others = [k for k in PARENT_KINDS if k not in kinds]
    keys["other_kind"] = _live(world.a, others[0]) if others else _live(world.a, "cert_verify")
    shown_while_suspended = {"parent_receipt"}
    keys["suspended_school"] = suspended.keys[
        next(k for k in kinds if k not in shown_while_suspended)
    ]
    return keys


def _assert_safe_headers(res: httpx.Response) -> None:
    assert "no-store" in res.headers.get("cache-control", ""), res.headers
    assert res.headers.get("referrer-policy"), res.headers
    assert res.headers.get("x-content-type-options") == "nosniff"
    assert "set-cookie" not in res.headers


# --- uniform 404, one path, no leak --------------------------------------------------------------


@pytest.mark.parametrize("route", sorted(ROUTES))
def test_SEC_037_dead_keys_get_one_404_through_one_lookup(  # noqa: PLR0917 (fixtures)
    route: str,
    world: Any,
    admin_engine: Engine,
    public_client: TestClient,
    suspended_school: Any,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    keys = _dead_keys(route, world, admin_engine, suspended_school)
    lookups: list[int] = []
    real = service.resolve_any

    def counting(kinds: Any, key: str | None) -> Any:
        lookups.append(1)
        return real(kinds, key)

    monkeypatch.setattr(service, "resolve_any", counting)
    capsys.readouterr()
    bodies: dict[str, str] = {}
    template = route.split(" ", 1)[1]
    for name, key in keys.items():
        _reset_budgets()
        lookups.clear()
        res = _call(public_client, route, key)
        assert res.status_code == 404, (name, res.status_code, res.text)
        assert res.headers["content-type"].startswith("application/problem+json")
        _assert_safe_headers(res)
        body = P.comparable(res)
        assert body.pop("instance") == template, (name, body)
        if key:
            assert key not in res.text, name
        bodies[name] = json.dumps(body, sort_keys=True)
        # The same path for every reason: one resolver call, one failure charged (cost 2).
        assert lookups == [1], (name, lookups)
    assert len(set(bodies.values())) == 1, bodies
    assert json.loads(next(iter(bodies.values()))) == {
        "type": json.loads(next(iter(bodies.values())))["type"],
        "title": "Not found",
        "status": 404,
        "code": "not_found",
    }
    out, err = capsys.readouterr()
    logged = out + err
    for name, key in keys.items():
        if len(key) > 8:
            assert key not in logged, name
    assert "public.lookup_failed" in logged


@pytest.mark.parametrize("route", sorted(ROUTES))
def test_SEC_037_the_404_body_is_the_same_on_every_route(
    route: str, public_client: TestClient
) -> None:
    _reset_budgets()
    res = _call(public_client, route, tokens.new_parent_token())
    body = P.comparable(res)
    body.pop("instance")
    _reset_budgets()
    reference = P.comparable(P.current(public_client, tokens.new_parent_token()))
    reference.pop("instance")
    assert body == reference


# --- budgets ----------------------------------------------------------------------------------


@pytest.mark.parametrize("route", sorted(ROUTES))
def test_FR_PUB_011_every_route_spends_the_failure_budget_and_then_refuses_live_keys(
    route: str, world: Any, public_client: TestClient
) -> None:
    live = _live(world.a, ROUTES[route][0])
    unknown: Callable[[], str] = _new_code if _is_cert(route) else tokens.new_parent_token
    _reset_budgets()
    failures = rl.load_config().policies["public_lookup_fail"].quota // LOOKUP_FAILURE_COST
    codes = [_call(public_client, route, unknown()).status_code for _ in range(failures)]
    assert codes == [404] * failures
    refused = _call(public_client, route, live)
    assert refused.status_code == 429, refused.text
    assert refused.json()["code"] == "rate_limited"
    assert int(refused.headers["Retry-After"]) >= 1
    assert live not in refused.text
    _assert_safe_headers(refused)
    _reset_budgets()


# --- caller --------------------------------------------------------------------------------------


@pytest.mark.parametrize("route", sorted(ROUTES))
def test_FR_PUB_001_every_route_takes_the_service_token_and_no_user_token(
    route: str, world: Any, public_client: TestClient
) -> None:
    live = _live(world.a, ROUTES[route][0])
    _reset_budgets()
    for res in (
        _call(public_client, route, live, service_token=False),
        _call(public_client, route, live, bearer="eyJhbGciOiJSUzI1NiJ9.e30.c2ln"),
        _call(public_client, route, live, service_token=False, headers={"X-Service-Token": "x"}),
    ):
        assert res.status_code == 401, res.text
        assert live not in res.text
        assert res.json()["instance"] == route.split(" ", 1)[1]
        _assert_safe_headers(res)


def test_SEC_037_live_pages_are_not_cached_and_set_no_cookie(
    world: Any, public_client: TestClient
) -> None:
    key = _live(world.a, "parent_notice")
    _reset_budgets()
    for res in (P.exchange(public_client, key), P.current(public_client, key)):
        assert res.status_code == 200, res.text
        _assert_safe_headers(res)
        assert key not in res.text


def test_SEC_037_validation_errors_never_echo_the_key(
    world: Any, public_client: TestClient
) -> None:
    key = _live(world.a, "parent_notice")
    _reset_budgets()
    res = P.public_call(
        public_client, "POST", P.EXCHANGE, body={"token": key + "x" * 200, "extra": key}
    )
    assert res.status_code in (404, 422)
    assert key not in res.text


# --- suspended school (D31) ----------------------------------------------------------------------


@pytest.fixture(scope="module")
def fee_school(world: Any, admin_engine: Engine) -> Any:
    F.install()
    s = W.School(W.provision_school())
    s.people["owner"] = W.add_member(admin_engine, s.tenant_id, ["owner"])
    W.build_structure(s, s.people["owner"])
    for role in ("principal", "office_admin", "office_staff", "accountant"):
        s.people[role] = W.add_member(admin_engine, s.tenant_id, [role])
    F.enable(admin_engine, s.tenant_id)
    return s


def test_D31_a_suspended_school_makes_no_payment_link_and_counts_no_view(
    fee_school: Any, admin_engine: Engine, public_client: TestClient
) -> None:
    F.install()
    O.connect(fee_school)
    student = F.student(fee_school)
    account = F.account(fee_school, student)
    guardian = P.SW.add_guardian(fee_school, student, full_name="Synthetic Parent")
    owner = fee_school.people["owner"]
    with tenant_session(fee_school.tenant_id, owner.user_id) as db:
        issued = service.issue_parent_link(
            db,
            kind="parent_dues",
            target_id=account,
            guardian_id=guardian,
            created_by=owner.user_id,
        )
    key = issued.key
    _reset_budgets()
    assert P.current(public_client, key).status_code == 200  # live while active
    views = P.handle_row(admin_engine, issued.handle_id)["view_count"]
    P.set_tenant_status(admin_engine, fee_school.tenant_id, "suspended")
    try:
        for route in (
            "GET /api/v1/public/links/current",
            "POST /api/v1/public/links/current/pay",
            "GET /api/v1/public/links/current/payment",
        ):
            _reset_budgets()
            res = _call(public_client, route, key)
            assert res.status_code == 404, (route, res.text)
        intents = O.scalar(
            admin_engine,
            "SELECT count(*) FROM fin.payment_intents WHERE account_id = :a",
            a=account,
        )
        assert intents == 0
        assert P.handle_row(admin_engine, issued.handle_id)["view_count"] == views
    finally:
        P.set_tenant_status(admin_engine, fee_school.tenant_id, "active")
        _reset_budgets()
