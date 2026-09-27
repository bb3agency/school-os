"""SchoolOS support sign-in during break-glass (ADR-0023 option C; US-103, FR-OPS-004, SEC-021,
SEC-005, T17). The operator's support principal (support client of the operator pool) reaches
only its own active, approved grant in that one school, for the grant window, read-only, and
every sign-in and call is audited in the school's chain (and the session start in the platform
chain). Synthetic schools and operators only."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

import pytest
from sqlalchemy import Engine, text

from app.breakglass import service
from app.core.config import get_settings
from app.core.db import tenant_session

from .conftest import Campus, MakeOperator, W, build_campus, platform_audit, raise_request

pytestmark = pytest.mark.db
BASE = "/api/v1/breakglass"
SESSION = f"{BASE}/support-session"


@dataclass
class Support:
    """The operator signed in with the support client (principal kind ``support``)."""

    subject: str
    kind: str = "support"


@dataclass
class StaffToken:
    """Someone presenting a STAFF-pool token that carries the operator's subject."""

    subject: str
    kind: str = "user"


def _grant_id(campus: Campus, request_id: uuid.UUID) -> uuid.UUID:
    with tenant_session(campus.tenant_id) as s:
        value: object = s.execute(
            text("SELECT id FROM ops.break_glass_grants WHERE platform_request_id = :r"),
            {"r": request_id},
        ).scalar_one()
    return uuid.UUID(str(value))


def _approved(
    campus: Campus, api: Any, make_operator: MakeOperator, **kw: Any
) -> tuple[Any, uuid.UUID, uuid.UUID, dict[str, Any]]:
    op = make_operator("support_agent")
    request_id = raise_request(campus.tenant_id, op, **kw)
    service.sync_school(campus.tenant_id)
    grant_id = _grant_id(campus, request_id)
    res = api.call(campus.person("owner"), "POST", f"{BASE}/requests/{grant_id}/approve")
    assert res.status_code == 200, res.text
    return op, request_id, grant_id, res.json()


def _events(admin: Engine, tenant: uuid.UUID, action: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = W.audit_events(admin, tenant, action)
    return events


def test_ADR_0023_support_session_starts_with_step_up_and_is_audited_on_both_chains(
    campus: Campus, api: Any, admin_engine: Engine, make_operator: MakeOperator
) -> None:
    op, request_id, grant_id, grant = _approved(campus, api, make_operator, minutes=30)
    who = Support(op.subject)
    stale = api.call(
        who, "POST", SESSION, json={"platform_request_id": str(request_id)}, auth_age_s=301
    )
    assert stale.status_code == 428, stale.text
    assert _events(admin_engine, campus.tenant_id, "breakglass.session_started") == []

    res = api.call(who, "POST", SESSION, json={"platform_request_id": str(request_id)})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["grant_id"] == str(grant_id)
    assert body["tenant_id"] == str(campus.tenant_id)
    assert body["expires_at"] == grant["expires_at"]

    started = _events(admin_engine, campus.tenant_id, "breakglass.session_started")
    assert len(started) == 1
    summary = started[0]["summary"]
    assert started[0]["resource_id"] == grant_id
    assert summary["platform_request_id"] == str(request_id)
    assert summary["membership_id"] == grant["membership_id"]
    assert summary["operator_id"] == str(op.id)
    assert summary["issuer_kind"] == "operator_support"
    assert summary["via_breakglass"] is True
    assert len(summary["session_ref"]) == 16
    assert "synthetic-session" not in str(summary), "never the raw IdP session id"
    # Platform chain copy (control plane), IDs only.
    assert "breakglass.session_started" in platform_audit(admin_engine, request_id)
    # The call itself is also a recorded support access.
    routes = [
        e["summary"]["route"] for e in _events(admin_engine, campus.tenant_id, "breakglass.access")
    ]
    assert f"{BASE}/support-session" in routes


def test_ADR_0023_login_event_of_support_carries_issuer_kind(
    campus: Campus, api: Any, admin_engine: Engine, make_operator: MakeOperator
) -> None:
    op, _, _, grant = _approved(campus, api, make_operator)
    res = api.call(Support(op.subject), "POST", "/api/v1/me/login-event")
    assert res.status_code == 200, res.text
    events = [
        e
        for e in _events(admin_engine, campus.tenant_id, "auth.login.succeeded")
        if e["resource_id"] == uuid.UUID(grant["membership_id"])
    ]
    assert len(events) == 1
    assert events[0]["summary"]["issuer_kind"] == "operator_support"


def test_ADR_0023_support_token_only_for_that_school(
    campus: Campus, api: Any, admin_engine: Engine, make_operator: MakeOperator
) -> None:
    op, _, _, _ = _approved(campus, api, make_operator)
    other = build_campus(admin_engine)
    who = Support(op.subject)
    assert api.call(who, "GET", "/api/v1/me", tenant=campus.tenant_id).status_code == 200
    denied = api.call(who, "GET", "/api/v1/me", tenant=other.tenant_id)
    assert denied.status_code == 403
    assert denied.json()["code"] == "no_membership"
    # The other school's request IDs are unknown here.
    foreign = raise_request(other.tenant_id, op)
    res = api.call(who, "POST", SESSION, json={"platform_request_id": str(foreign)})
    assert res.status_code == 404
    # Another school's objects do not exist for this session (never revealed).
    assert api.call(who, "GET", f"/api/v1/sections/{other.id('section_9a')}").status_code == 404


def test_ADR_0023_support_token_only_for_the_grant_window(
    campus: Campus, api: Any, admin_engine: Engine, make_operator: MakeOperator
) -> None:
    op, request_id, _, grant = _approved(campus, api, make_operator, minutes=15)
    who = Support(op.subject)
    assert api.call(who, "GET", "/api/v1/me").status_code == 200
    with admin_engine.begin() as c:
        c.execute(
            text(
                "UPDATE core.memberships SET created_at = now() - interval '20 minutes', "
                "expires_at = now() - interval '5 minutes' WHERE id = :m"
            ),
            {"m": uuid.UUID(grant["membership_id"])},
        )
    assert api.call(who, "GET", "/api/v1/me").status_code == 403
    res = api.call(who, "POST", SESSION, json={"platform_request_id": str(request_id)})
    assert res.status_code == 403


def test_ADR_0023_support_needs_an_active_grant_not_just_a_membership(
    campus: Campus, api: Any, admin_engine: Engine, make_operator: MakeOperator
) -> None:
    op, _, grant_id, _ = _approved(campus, api, make_operator)
    who = Support(op.subject)
    assert api.call(who, "GET", "/api/v1/me").status_code == 200
    # The grant ends but (say, a crash) the membership was not closed: still refused.
    with admin_engine.begin() as c:
        c.execute(
            text("UPDATE ops.break_glass_grants SET status = 'expired' WHERE id = :g"),
            {"g": grant_id},
        )
    res = api.call(who, "GET", "/api/v1/me")
    assert res.status_code == 403
    assert res.json()["code"] == "breakglass_grant_inactive"


def test_ADR_0023_revoked_grant_ends_support_sign_in(
    campus: Campus, api: Any, admin_engine: Engine, make_operator: MakeOperator
) -> None:
    op, request_id, grant_id, _ = _approved(campus, api, make_operator)
    who = Support(op.subject)
    assert api.call(who, "POST", SESSION, json={"platform_request_id": str(request_id)}).is_success
    revoke = api.call(campus.person("owner"), "POST", f"{BASE}/grants/{grant_id}/revoke")
    assert revoke.status_code == 200, revoke.text
    assert api.call(who, "GET", "/api/v1/me").status_code == 403
    res = api.call(who, "POST", SESSION, json={"platform_request_id": str(request_id)})
    assert res.status_code == 403


def test_ADR_0023_no_grant_no_access(
    campus: Campus, api: Any, admin_engine: Engine, make_operator: MakeOperator
) -> None:
    op = make_operator("support_agent")
    request_id = raise_request(campus.tenant_id, op)
    service.sync_school(campus.tenant_id)
    who = Support(op.subject)
    assert api.call(who, "GET", "/api/v1/me").status_code == 403
    res = api.call(who, "POST", SESSION, json={"platform_request_id": str(request_id)})
    assert res.status_code == 403


def test_ADR_0023_staff_token_with_operator_subject_gets_no_support_access(
    campus: Campus, api: Any, admin_engine: Engine, make_operator: MakeOperator
) -> None:
    op, request_id, _, _ = _approved(campus, api, make_operator)
    impostor = StaffToken(op.subject)
    assert api.call(impostor, "GET", "/api/v1/me").status_code == 403
    res = api.call(impostor, "POST", SESSION, json={"platform_request_id": str(request_id)})
    assert res.status_code == 403


def test_ADR_0023_staff_member_cannot_start_a_support_session(
    campus: Campus, api: Any, admin_engine: Engine, make_operator: MakeOperator
) -> None:
    _, request_id, _, _ = _approved(campus, api, make_operator)
    res = api.call(
        campus.person("owner"), "POST", SESSION, json={"platform_request_id": str(request_id)}
    )
    assert res.status_code == 403
    assert res.json()["code"] == "breakglass_only"
    assert _events(admin_engine, campus.tenant_id, "breakglass.session_started") == []


def test_ADR_0023_support_membership_with_an_extra_role_is_refused(
    campus: Campus, api: Any, admin_engine: Engine, make_operator: MakeOperator
) -> None:
    op, _, _, grant = _approved(campus, api, make_operator)
    who = Support(op.subject)
    assert api.call(who, "GET", "/api/v1/me").status_code == 200
    with admin_engine.begin() as c:  # someone slips a staff role onto the support membership
        c.execute(
            text(
                "INSERT INTO core.membership_roles (tenant_id, membership_id, role_id) "
                "SELECT :t, :m, r.id FROM core.roles r WHERE r.tenant_id = :t AND r.key = 'teacher'"
            ),
            {"t": campus.tenant_id, "m": uuid.UUID(grant["membership_id"])},
        )
    assert api.call(who, "GET", "/api/v1/me").status_code == 403


def test_ADR_0023_support_never_accepts_invitations(
    campus: Campus, api: Any, make_operator: MakeOperator
) -> None:
    op, _, _, _ = _approved(campus, api, make_operator)
    res = api.call(Support(op.subject), "POST", "/api/v1/me/accept-invitations")
    assert res.status_code == 403
    assert res.json()["code"] == "breakglass_only"


def test_ADR_0023_support_writes_stay_refused(
    campus: Campus, api: Any, admin_engine: Engine, make_operator: MakeOperator
) -> None:
    op, _, _, _ = _approved(campus, api, make_operator)
    res = api.call(
        Support(op.subject),
        "POST",
        "/api/v1/classes",
        json={"code": "ZZ", "display_en": "Z", "display_te": "జ"},
    )
    assert res.status_code == 403


def test_ADR_0023_operator_identity_carries_the_operator_issuer(
    campus: Campus, api: Any, admin_engine: Engine, make_operator: MakeOperator
) -> None:
    op, _, _, grant = _approved(campus, api, make_operator)
    with admin_engine.connect() as c:
        row = c.execute(
            text(
                "SELECT u.idp_issuer, u.idp_subject FROM core.users u "
                "JOIN core.memberships m ON m.user_id = u.id WHERE m.id = :m"
            ),
            {"m": uuid.UUID(grant["membership_id"])},
        ).one()
    assert (row.idp_issuer, row.idp_subject) == (
        get_settings().resolved_support_issuer,
        op.subject,
    )
