"""Public parent-link routes through the real app (ADR-0046, ADR-0047; FR-PUB-001, FR-PUB-008,
FR-PUB-010..012; SEC-036, SEC-037). Synthetic data only.

Called the way the BFF calls them: the service token, no user token. Covers the exchange and the
current link's frame, the uniform 404 for unknown, malformed, expired, revoked and
suspended-school links, refusal of user tokens and missing service tokens, the failed-lookup and
per-handle budgets (a used-up failure budget also refuses a valid token: no oracle), view
counting, the parent language order with Telugu hidden for staff, field allowlists of the
responses and log lines without tokens.
"""

from __future__ import annotations

import datetime as dt
import json
import sys
import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text

from app.core import ratelimit as rl
from app.core.db import tenant_session
from app.core.languages import telugu_enabled
from app.public import service, tokens
from app.public.guards import LOOKUP_FAILURE_COST

pytestmark = pytest.mark.db
P = sys.modules["sos_test_public_support"]
W = sys.modules["sos_test_api_world"]
NOW = dt.datetime.now(dt.UTC)


def _school_language(admin: Engine, tenant_id: uuid.UUID, language: str | None) -> None:
    with admin.begin() as c:
        if language is None:
            c.execute(
                text(
                    "UPDATE core.tenants SET settings = settings - 'parent_language' WHERE id = :t"
                ),
                {"t": tenant_id},
            )
        else:
            c.execute(
                text(
                    "UPDATE core.tenants SET settings = jsonb_set(settings, '{parent_language}', "
                    "to_jsonb(CAST(:l AS text))) WHERE id = :t"
                ),
                {"t": tenant_id, "l": language},
            )


def _guardian_language(admin: Engine, guardian_id: uuid.UUID, language: str | None) -> None:
    with admin.begin() as c:
        c.execute(
            text("UPDATE sis.guardians SET preferred_language = :l WHERE id = :g"),
            {"l": language, "g": guardian_id},
        )


# --- exchange and current ------------------------------------------------------------------------


def test_FR_PUB_010_exchange_answers_kind_and_expiry_without_counting_a_view(
    world: Any, admin_engine: Engine, public_client: TestClient
) -> None:
    issued = P.issue(world.a, "parent_receipt", target_type="receipt")
    res = P.exchange(public_client, issued.key)
    assert res.status_code == 200, res.text
    body = res.json()
    assert set(body) == {"kind", "expires_at"}
    assert body["kind"] == "parent_receipt"
    assert dt.datetime.fromisoformat(body["expires_at"]) == issued.expires_at
    assert res.headers["Cache-Control"] == "no-store"
    assert P.handle_row(admin_engine, issued.handle_id)["view_count"] == 0


def test_FR_PUB_008_current_gives_the_frame_and_counts_views(
    world: Any, admin_engine: Engine, public_client: TestClient
) -> None:
    _, guardian = P.student_with_guardian(world.a)
    issued = P.issue(world.a, "parent_notice", guardian_id=guardian)
    first = P.current(public_client, issued.key)
    assert first.status_code == 200, first.text
    body = first.json()
    # Field allowlist (FR-PUB-009): nothing about the student, guardian or other schools.
    assert set(body) == {
        "kind", "expires_at", "language", "school_name", "notice", "receipt", "dues", "optin"
    }  # fmt: skip
    assert body["receipt"] is None
    assert body["dues"] is None
    assert body["optin"] is None
    assert set(body["notice"]) == {"title", "body", "date", "has_pdf"}  # R2-3H content
    assert body["kind"] == "parent_notice"
    assert body["school_name"] == "Synthetic Model School"
    assert P.current(public_client, issued.key).status_code == 200
    row = P.handle_row(admin_engine, issued.handle_id)
    assert row["view_count"] == 2
    viewed = [
        e
        for e in W.audit_events(admin_engine, world.a.tenant_id, "public.link.viewed")
        if e["resource_id"] == issued.handle_id
    ]
    assert len(viewed) == 1
    assert viewed[0]["summary"] == {"kind": "parent_notice", "target_type": "parent_notice"}


def test_FR_PUB_008_language_is_guardian_then_school_then_english_with_telugu_off(
    world: Any, admin_engine: Engine, public_client: TestClient
) -> None:
    assert telugu_enabled() is False  # staff surfaces stay English (ADR-0036)
    _, guardian = P.student_with_guardian(world.a)
    issued = P.issue(world.a, "parent_notice", guardian_id=guardian)
    plain = P.issue(world.a, "parent_notice")  # no guardian on the handle
    try:
        _school_language(admin_engine, world.a.tenant_id, None)
        assert P.current(public_client, issued.key).json()["language"] == "en"
        _school_language(admin_engine, world.a.tenant_id, "te")
        assert P.current(public_client, issued.key).json()["language"] == "te"
        assert P.current(public_client, plain.key).json()["language"] == "te"
        _guardian_language(admin_engine, guardian, "en")
        assert P.current(public_client, issued.key).json()["language"] == "en"
        _school_language(admin_engine, world.a.tenant_id, "en")
        _guardian_language(admin_engine, guardian, "te")
        assert P.current(public_client, issued.key).json()["language"] == "te"
        assert P.current(public_client, plain.key).json()["language"] == "en"
    finally:
        _school_language(admin_engine, world.a.tenant_id, None)
        _guardian_language(admin_engine, guardian, None)


# --- uniform 404 ---------------------------------------------------------------------------------


def test_FR_PUB_011_every_dead_link_gets_the_same_404(
    world: Any, admin_engine: Engine, public_client: TestClient
) -> None:
    expired = tokens.new_parent_token()
    P.insert_raw(
        admin_engine,
        world.a.tenant_id,
        kind="parent_notice",
        key=expired,
        created_at=NOW - dt.timedelta(days=31),
        expires_at=NOW - dt.timedelta(minutes=1),
    )
    revoked = P.issue(world.a, "parent_notice")
    with tenant_session(world.a.tenant_id) as db:
        service.revoke(db, revoked.handle_id, reason="manual", by_user=False)
    suspended = W.School(W.provision_school())
    suspended.people["owner"] = W.add_member(admin_engine, suspended.tenant_id, ["owner"])
    notice = P.issue(suspended, "parent_notice")
    P.set_tenant_status(admin_engine, suspended.tenant_id, "suspended")
    certificate = P.issue(world.a, "cert_verify", key="NOTP2345EFGH6789", target_type="cert")
    assert certificate.key is None
    cases = {
        "unknown": tokens.new_parent_token(),
        "malformed": "not a token",
        "empty": "",
        "expired": expired,
        "revoked": revoked.key,
        "suspended_school": notice.key,
        "other_kind": "NOTP2345EFGH6789",
    }
    try:
        bodies = {}
        for name, key in cases.items():
            for res in (P.exchange(public_client, key), P.current(public_client, key)):
                assert res.status_code == 404, (name, res.text)
                assert res.headers["content-type"].startswith("application/problem+json")
                body = P.comparable(res)
                body.pop("instance")
                bodies[(name, res.request.method)] = body
            rl.get_rate_limiter().store.reset()  # type: ignore[attr-defined]
        assert len({json.dumps(b, sort_keys=True) for b in bodies.values()}) == 1
        sample = next(iter(bodies.values()))
        assert sample == {
            "type": sample["type"],
            "title": "Not found",
            "status": 404,
            "code": "not_found",
        }
        assert "Synthetic Model School" not in json.dumps(list(bodies.values()))
    finally:
        P.set_tenant_status(admin_engine, suspended.tenant_id, "active")


# --- service token, user token -------------------------------------------------------------------


def test_FR_PUB_001_public_routes_refuse_user_tokens_and_missing_service_tokens(
    world: Any, public_client: TestClient
) -> None:
    issued = P.issue(world.a, "parent_notice")
    no_service = P.current(public_client, issued.key, service_token=False)
    assert no_service.status_code == 401
    with_user = P.current(public_client, issued.key, bearer="eyJhbGciOiJSUzI1NiJ9.e30.sig")
    assert with_user.status_code == 401
    bad_service = P.current(
        public_client,
        issued.key,
        service_token=False,
        headers={"X-Service-Token": "not-a-jwt"},
    )
    assert bad_service.status_code == 401
    assert P.current(public_client, issued.key).status_code == 200


# --- rate limits ---------------------------------------------------------------------------------


def test_FR_PUB_011_used_up_failure_budget_refuses_even_a_valid_link(
    world: Any, public_client: TestClient
) -> None:
    issued = P.issue(world.a, "parent_notice")
    policy = rl.load_config().policies["public_lookup_fail"]
    failures = policy.quota // LOOKUP_FAILURE_COST
    codes = [
        P.exchange(public_client, tokens.new_parent_token()).status_code for _ in range(failures)
    ]
    assert codes == [404] * failures
    refused = P.exchange(public_client, issued.key)
    assert refused.status_code == 429
    assert refused.json()["code"] == "rate_limited"
    assert int(refused.headers["Retry-After"]) >= 1
    rl.get_rate_limiter().store.reset()  # type: ignore[attr-defined]
    assert P.exchange(public_client, issued.key).status_code == 200


def test_FR_PUB_011_successful_lookups_do_not_spend_the_failure_budget(
    world: Any, public_client: TestClient
) -> None:
    issued = P.issue(world.a, "parent_notice")
    policy = rl.load_config().policies["public_lookup_fail"]
    for _ in range(policy.quota):
        assert P.exchange(public_client, issued.key).status_code == 200


def test_FR_PUB_011_one_handle_has_its_own_view_budget(
    world: Any, public_client: TestClient
) -> None:
    hot = P.issue(world.a, "parent_notice")
    other = P.issue(world.a, "parent_notice")
    quota = rl.load_config().policies["public_token"].quota
    codes = [P.current(public_client, hot.key).status_code for _ in range(quota + 1)]
    assert codes[:quota] == [200] * quota
    assert codes[quota] == 429
    assert P.current(public_client, other.key).status_code == 200


# --- logs ----------------------------------------------------------------------------------------


def test_SEC_037_tokens_never_reach_the_logs(
    world: Any, public_client: TestClient, capsys: pytest.CaptureFixture[str]
) -> None:
    issued = P.issue(world.a, "parent_notice")
    unknown = tokens.new_parent_token()
    capsys.readouterr()
    P.exchange(public_client, issued.key)
    P.current(public_client, issued.key)
    P.exchange(public_client, unknown)
    P.current(public_client, unknown)
    out, err = capsys.readouterr()
    logged = out + err
    assert issued.key not in logged
    assert unknown not in logged
    assert "public.lookup_failed" in logged
