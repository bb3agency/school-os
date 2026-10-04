"""Break-glass support access, end to end (US-103 AC1-AC2, FR-OPS-004, SEC-021, SEC-029,
docs/07 §6.4, T17). Synthetic schools and operators only."""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass
from typing import Any

import pytest
from sqlalchemy import Engine, text

from app.breakglass import service
from app.core.errors import Conflict
from app.platform import breakglass as platform_breakglass

from .conftest import (
    OPERATOR_NAME,
    Campus,
    MakeOperator,
    W,
    grant_row,
    notifications_of,
    platform_audit,
    platform_request,
    raise_request,
)

pytestmark = pytest.mark.db
BASE = "/api/v1/breakglass"


@dataclass
class Caller:
    """An operator signing in to the school app with the support client of the operator pool
    (ADR-0023): the operator's IdP subject, principal kind ``support``."""

    subject: str
    kind: str = "support"


def _pending(campus: Campus, operator: Any, **kw: Any) -> tuple[uuid.UUID, uuid.UUID]:
    request_id = raise_request(campus.tenant_id, operator, **kw)
    service.sync_school(campus.tenant_id)
    grant = grant_row_by(campus, request_id)
    return request_id, grant


def grant_row_by(campus: Campus, request_id: uuid.UUID) -> uuid.UUID:
    from app.core.db import tenant_session

    with tenant_session(campus.tenant_id) as s:
        value: object = s.execute(
            text("SELECT id FROM ops.break_glass_grants WHERE platform_request_id = :r"),
            {"r": request_id},
        ).scalar_one()
    return uuid.UUID(str(value))


def _tenant_audit(admin: Engine, tenant_id: uuid.UUID, action: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = W.audit_events(admin, tenant_id, action)
    return events


# --- AC1: the school sees reason, scope and duration ---------------------------------------


def test_US_103_AC1_pending_request_shows_reason_scope_and_duration(
    campus: Campus, api: Any, admin_engine: Engine, make_operator: MakeOperator
) -> None:
    op = make_operator("support_agent")
    section = campus.id("section_9a")
    request_id = raise_request(
        campus.tenant_id, op, minutes=90, scope={"section_id": section, "area": "imports"}
    )
    # The list pulls new requests from the control plane first.
    res = api.call(campus.person("owner"), "GET", f"{BASE}/requests")
    assert res.status_code == 200, res.text
    item = next(i for i in res.json()["data"] if i["platform_request_id"] == str(request_id))
    assert item["status"] == "requested"
    assert item["duration_minutes"] == 90
    assert item["scope"] == {"section_id": str(section), "area": "imports"}
    assert item["reason"].startswith("Import batch shows duplicate rows")
    assert item["operator_display_name"] == OPERATOR_NAME
    assert item["membership_id"] is None
    one = api.call(campus.person("principal"), "GET", f"{BASE}/requests/{item['id']}")
    assert one.json() == item
    # Audited in the school's chain (actor: the platform) and announced to the approvers only.
    events = _tenant_audit(admin_engine, campus.tenant_id, "breakglass.requested")
    assert [e["resource_id"] for e in events] == [uuid.UUID(item["id"])]
    assert events[0]["summary"]["duration_minutes"] == 90
    for role in ("owner", "principal"):
        assert notifications_of(admin_engine, campus.person(role).membership_id) == [
            "breakglass.requested"
        ]
    for role in ("office_admin", "teacher"):
        assert notifications_of(admin_engine, campus.person(role).membership_id) == []
    # Pulling again changes nothing (idempotent).
    assert service.sync_school(campus.tenant_id).received == 0
    assert len(_tenant_audit(admin_engine, campus.tenant_id, "breakglass.requested")) == 1


def test_US_103_only_approvers_see_requests(campus: Campus, api: Any) -> None:
    for role in ("office_admin", "office_staff", "teacher"):
        res = api.call(campus.person(role), "GET", f"{BASE}/requests")
        assert res.status_code == 403, role


# --- AC2: approval with step-up starts access; it ends by itself --------------------------


def test_US_103_AC2_access_starts_only_after_approval_with_step_up(
    campus: Campus, api: Any, admin_engine: Engine, make_operator: MakeOperator
) -> None:
    op = make_operator("support_agent")
    _, grant_id = _pending(campus, op, minutes=45)
    operator = Caller(op.subject)
    assert api.call(operator, "GET", "/api/v1/me").status_code == 403, "no access before approval"

    stale = api.call(
        campus.person("owner"), "POST", f"{BASE}/requests/{grant_id}/approve", auth_age_s=301
    )
    assert stale.status_code == 428
    assert stale.json()["code"] == "step_up_required"
    for role in ("office_admin", "teacher"):
        denied = api.call(campus.person(role), "POST", f"{BASE}/requests/{grant_id}/approve")
        assert denied.status_code == 403, role

    before = dt.datetime.now(dt.UTC)
    ok = api.call(campus.person("principal"), "POST", f"{BASE}/requests/{grant_id}/approve")
    assert ok.status_code == 200, ok.text
    body = ok.json()
    assert body["status"] == "active"
    assert body["approved_by_membership"] == str(campus.person("principal").membership_id)
    starts = dt.datetime.fromisoformat(body["starts_at"])
    expires = dt.datetime.fromisoformat(body["expires_at"])
    assert before - dt.timedelta(seconds=5) <= starts <= dt.datetime.now(dt.UTC)
    assert expires - starts == dt.timedelta(minutes=45)

    me = api.call(operator, "GET", "/api/v1/me")
    assert me.status_code == 200, me.text
    assert me.json()["roles"] == ["platform_support"]
    assert me.json()["tenant_id"] == str(campus.tenant_id)
    assert set(me.json()["permissions"]) == {
        "session.authenticated",
        "student.read_basic",
        "dq.findings.read",
        "document.read",
    }
    # The temporary membership expires with the grant.
    with admin_engine.connect() as c:
        membership = c.execute(
            text("SELECT status, expires_at, mfa_required FROM core.memberships WHERE id = :m"),
            {"m": uuid.UUID(body["membership_id"])},
        ).one()
    assert membership.status == "active"
    assert membership.expires_at == expires
    assert membership.mfa_required is True
    # A second decision is refused.
    again = api.call(campus.person("owner"), "POST", f"{BASE}/requests/{grant_id}/approve")
    assert again.status_code == 409


def test_US_103_approval_is_audited_on_both_chains_and_notified(
    campus: Campus, api: Any, admin_engine: Engine, make_operator: MakeOperator
) -> None:
    op = make_operator("support_agent")
    request_id, grant_id = _pending(campus, op)
    res = api.call(campus.person("owner"), "POST", f"{BASE}/requests/{grant_id}/approve")
    assert res.status_code == 200, res.text
    approved = _tenant_audit(admin_engine, campus.tenant_id, "breakglass.approved")
    assert len(approved) == 1
    assert approved[0]["actor_id"] == campus.person("owner").user_id
    assert approved[0]["summary"]["platform_request_id"] == str(request_id)
    opened = _tenant_audit(admin_engine, campus.tenant_id, "membership.breakglass_opened")
    assert opened[0]["summary"]["via_breakglass"] is True
    # Control plane: status and its own hash-chained audit.
    assert platform_request(admin_engine, request_id)["status"] == "active"
    assert platform_audit(admin_engine, request_id) == ["breakglass.requested", "breakglass.active"]
    assert grant_row(admin_engine, request_id)["platform_status_synced"] == "active"
    assert notifications_of(admin_engine, campus.person("principal").membership_id) == [
        "breakglass.requested",
        "breakglass.approved",
    ]


def test_US_103_deny_gives_no_access(
    campus: Campus, api: Any, admin_engine: Engine, make_operator: MakeOperator
) -> None:
    op = make_operator("support_agent")
    request_id, grant_id = _pending(campus, op)
    stale = api.call(
        campus.person("owner"), "POST", f"{BASE}/requests/{grant_id}/deny", auth_age_s=301
    )
    assert stale.status_code == 428
    res = api.call(campus.person("owner"), "POST", f"{BASE}/requests/{grant_id}/deny")
    assert res.status_code == 200, res.text
    assert res.json()["status"] == "denied"
    assert res.json()["denied_by_membership"] == str(campus.person("owner").membership_id)
    assert api.call(Caller(op.subject), "GET", "/api/v1/me").status_code == 403
    assert platform_request(admin_engine, request_id)["status"] == "denied"
    assert "breakglass.denied" in platform_audit(admin_engine, request_id)
    assert len(_tenant_audit(admin_engine, campus.tenant_id, "breakglass.denied")) == 1
    approve = api.call(campus.person("owner"), "POST", f"{BASE}/requests/{grant_id}/approve")
    assert approve.status_code == 409


# --- self-approval, withdrawn requests, inactive operators ---------------------------------


def test_ADR_0023_staff_subject_collision_never_attaches_a_support_membership(
    campus: Campus, api: Any, admin_engine: Engine, make_operator: MakeOperator
) -> None:
    # A staff-pool account (an owner here) whose subject equals the operator's operator-pool
    # subject. Different issuers: different identities (ADR-0023). While the old unique subject
    # constraint exists (expand phase) the operator identity cannot be created: fail closed.
    insider = W.add_member(admin_engine, campus.tenant_id, ["owner"])
    op = make_operator("support_agent", subject=insider.subject)
    request_id, grant_id = _pending(campus, op)
    for approver in (insider, campus.person("owner")):
        res = api.call(approver, "POST", f"{BASE}/requests/{grant_id}/approve")
        assert res.status_code == 409, res.text
        assert res.json()["code"] == "breakglass_identity_conflict"
    assert grant_row(admin_engine, request_id)["status"] == "requested"
    # The staff account got nothing: still only its owner role, no platform_support anywhere.
    with admin_engine.connect() as c:
        roles: set[str] = set(
            c.execute(
                text(
                    "SELECT r.key FROM core.membership_roles mr JOIN core.roles r "
                    "ON r.tenant_id = mr.tenant_id AND r.id = mr.role_id "
                    "JOIN core.memberships m ON m.tenant_id = mr.tenant_id "
                    "AND m.id = mr.membership_id WHERE m.user_id = :u"
                ),
                {"u": insider.user_id},
            ).scalars()
        )
    assert roles == {"owner"}
    # Neither a support token nor a staff token with that subject opens support access.
    assert api.call(Caller(op.subject), "GET", "/api/v1/me").status_code == 403
    staff = api.call(insider, "GET", "/api/v1/me")
    assert staff.status_code == 200
    assert staff.json()["roles"] == ["owner"]


def test_US_103_support_access_never_holds_approval_rights(
    campus: Campus, api: Any, admin_engine: Engine, make_operator: MakeOperator
) -> None:
    # The operator's own identity holds (support) access in this school: it cannot approve a
    # further request for itself (read-only role without breakglass.approve).
    op = make_operator("support_agent")
    _, first = _pending(campus, op)
    approved = api.call(campus.person("owner"), "POST", f"{BASE}/requests/{first}/approve")
    assert approved.status_code == 200, approved.text
    request_id, second = _pending(campus, op)
    own = api.call(Caller(op.subject), "POST", f"{BASE}/requests/{second}/approve")
    assert own.status_code == 403
    assert grant_row(admin_engine, request_id)["status"] == "requested"


def test_SEC_021_database_refuses_self_approval_and_approved_emergencies(
    campus: Campus, admin_engine: Engine
) -> None:
    from sqlalchemy.exc import IntegrityError

    from app.core.db import tenant_session

    owner = campus.person("owner")
    now = dt.datetime.now(dt.UTC)
    insert = text(
        "INSERT INTO ops.break_glass_grants (id, tenant_id, platform_user_id, reason, scope, "
        "status, starts_at, expires_at, approved_by_membership, membership_id, emergency) "
        "VALUES (gen_random_uuid(), :t, gen_random_uuid(), 'synthetic support reason', '{}', "
        "'active', :s, :e, :a, :m, :em)"
    )
    params = {
        "t": campus.tenant_id,
        "s": now,
        "e": now + dt.timedelta(hours=1),
        "a": owner.membership_id,
    }
    with (
        pytest.raises(IntegrityError, match="break_glass_grants_no_self_approval"),
        tenant_session(campus.tenant_id) as s,
    ):
        s.execute(insert, {**params, "m": owner.membership_id, "em": False})
    with (
        pytest.raises(IntegrityError, match="break_glass_grants_emergency_unapproved"),
        tenant_session(campus.tenant_id) as s,
    ):
        s.execute(insert, {**params, "m": None, "em": True})
    from sqlalchemy.exc import ProgrammingError

    with (
        pytest.raises(ProgrammingError, match="permission denied"),
        tenant_session(campus.tenant_id) as s,
    ):
        s.execute(text("DELETE FROM ops.break_glass_grants"))


def test_US_103_withdrawn_or_stale_requests_cannot_be_approved(
    campus: Campus, api: Any, admin_engine: Engine, make_operator: MakeOperator
) -> None:
    op = make_operator("support_agent")
    request_id, grant_id = _pending(campus, op)
    with admin_engine.begin() as c:
        c.execute(
            text("UPDATE platform.breakglass_requests SET status = 'denied' WHERE id = :r"),
            {"r": request_id},
        )
    res = api.call(campus.person("owner"), "POST", f"{BASE}/requests/{grant_id}/approve")
    assert res.status_code == 409
    assert res.json()["code"] == "request_withdrawn"

    request_id, grant_id = _pending(campus, op)
    with admin_engine.begin() as c:
        c.execute(
            text(
                "UPDATE ops.break_glass_grants SET requested_at = now() - interval '25 hours' "
                "WHERE id = :g"
            ),
            {"g": grant_id},
        )
    res = api.call(campus.person("owner"), "POST", f"{BASE}/requests/{grant_id}/approve")
    assert res.status_code == 409
    assert res.json()["code"] == "request_expired"
    swept = service.sweep_school(campus.tenant_id)
    assert swept["stale_requests"] == 1
    assert grant_row(admin_engine, request_id)["status"] == "expired"
    assert platform_request(admin_engine, request_id)["status"] == "expired"


def test_US_103_deactivated_operator_cannot_be_approved(
    campus: Campus, api: Any, admin_engine: Engine, make_operator: MakeOperator
) -> None:
    op = make_operator("support_agent")
    _, grant_id = _pending(campus, op)
    with admin_engine.begin() as c:
        c.execute(
            text(
                "UPDATE platform.operators SET status = 'deactivated', deactivated_at = now() "
                "WHERE id = :o"
            ),
            {"o": op.id},
        )
    res = api.call(campus.person("owner"), "POST", f"{BASE}/requests/{grant_id}/approve")
    assert res.status_code == 409
    assert res.json()["code"] == "operator_inactive"


# --- what support can do: read-only, scope-limited, everything visible ---------------------


def test_SEC_021_support_access_is_read_only_and_scope_limited(
    campus: Campus, api: Any, admin_engine: Engine, make_operator: MakeOperator
) -> None:
    op = make_operator("support_agent")
    _, grant_id = _pending(campus, op, scope={"section_id": campus.id("section_9a")})
    assert (
        api.call(campus.person("owner"), "POST", f"{BASE}/requests/{grant_id}/approve").status_code
        == 200
    )
    operator = Caller(op.subject)
    in_scope = api.call(operator, "GET", f"/api/v1/sections/{campus.id('section_9a')}")
    assert in_scope.status_code == 200, in_scope.text
    for other in ("section_9c", "section_10a"):
        res = api.call(operator, "GET", f"/api/v1/sections/{campus.id(other)}")
        assert res.status_code == 404, other
    listed = api.call(operator, "GET", "/api/v1/sections").json()["data"]
    assert {s["id"] for s in listed} == {str(campus.id("section_9a"))}
    # No write permission at all, no user management, no audit log, no approvals.
    writes = [
        ("POST", "/api/v1/classes", {"code": "ZZ", "display_en": "Z", "display_te": "జ"}),
        ("POST", "/api/v1/users", {"idp_subject": "x", "display_name": "X", "roles": ["teacher"]}),
        ("POST", f"{BASE}/requests/{grant_id}/approve", None),
        ("POST", f"{BASE}/grants/{grant_id}/revoke", None),
    ]
    for method, path, body in writes:
        assert api.call(operator, method, path, json=body).status_code == 403, path
    for path in ("/api/v1/users", "/api/v1/audit/events", f"{BASE}/requests"):
        assert api.call(operator, "GET", path).status_code == 403, path
    # Own session routes stay usable.
    assert api.call(operator, "POST", "/api/v1/notifications/read-all").status_code == 200


def test_T17_every_support_call_is_visible_in_the_school_audit_log(
    campus: Campus, api: Any, admin_engine: Engine, make_operator: MakeOperator
) -> None:
    op = make_operator("support_agent")
    _, grant_id = _pending(campus, op)
    approved = api.call(campus.person("owner"), "POST", f"{BASE}/requests/{grant_id}/approve")
    membership_id = approved.json()["membership_id"]
    operator = Caller(op.subject)
    section = campus.id("section_10a")
    assert api.call(operator, "GET", f"/api/v1/sections/{section}").status_code == 200
    assert api.call(operator, "GET", "/api/v1/classes").status_code == 200
    events = _tenant_audit(admin_engine, campus.tenant_id, "breakglass.access")
    routes = [(e["summary"]["method"], e["summary"]["route"]) for e in events]
    assert ("GET", "/api/v1/sections/{section_id}") in routes
    assert ("GET", "/api/v1/classes") in routes
    for event in events:
        assert event["summary"]["via_breakglass"] is True
        assert event["summary"]["membership_id"] == membership_id
    by_section = next(e for e in events if e["summary"]["route"].endswith("{section_id}"))
    assert by_section["resource_id"] == section
    assert by_section["summary"]["path_ids"] == {"section_id": str(section)}
    # The school's own audit viewer shows them, flagged.
    viewer = api.call(
        campus.person("owner"),
        "GET",
        "/api/v1/audit/events",
        params={"action": "breakglass.access", "limit": 200},
    )
    assert viewer.status_code == 200, viewer.text
    shown = viewer.json()["data"]
    assert shown
    assert all(e["summary"]["via_breakglass"] is True for e in shown)


# --- revoke and expiry --------------------------------------------------------------------


def test_US_103_school_can_revoke_at_any_time(
    campus: Campus, api: Any, admin_engine: Engine, make_operator: MakeOperator
) -> None:
    op = make_operator("support_agent")
    request_id, grant_id = _pending(campus, op, minutes=480)
    api.call(campus.person("owner"), "POST", f"{BASE}/requests/{grant_id}/approve")
    operator = Caller(op.subject)
    assert api.call(operator, "GET", "/api/v1/me").status_code == 200
    stale = api.call(
        campus.person("principal"), "POST", f"{BASE}/grants/{grant_id}/revoke", auth_age_s=301
    )
    assert stale.status_code == 428
    res = api.call(campus.person("principal"), "POST", f"{BASE}/grants/{grant_id}/revoke")
    assert res.status_code == 200, res.text
    assert res.json()["status"] == "revoked"
    assert res.json()["revoked_by_membership"] == str(campus.person("principal").membership_id)
    assert api.call(operator, "GET", "/api/v1/me").status_code == 403, "access ends at once"
    assert platform_request(admin_engine, request_id)["status"] == "revoked"
    assert platform_audit(admin_engine, request_id)[-1] == "breakglass.revoked"
    assert len(_tenant_audit(admin_engine, campus.tenant_id, "breakglass.revoked")) == 1
    assert len(_tenant_audit(admin_engine, campus.tenant_id, "membership.breakglass_closed")) == 1
    assert "breakglass.revoked" in notifications_of(
        admin_engine, campus.person("owner").membership_id
    )
    again = api.call(campus.person("principal"), "POST", f"{BASE}/grants/{grant_id}/revoke")
    assert again.status_code == 409


def test_US_103_AC2_access_ends_automatically(
    campus: Campus, api: Any, admin_engine: Engine, make_operator: MakeOperator
) -> None:
    op = make_operator("support_agent")
    request_id, grant_id = _pending(campus, op, minutes=15)
    body = api.call(campus.person("owner"), "POST", f"{BASE}/requests/{grant_id}/approve").json()
    operator = Caller(op.subject)
    assert api.call(operator, "GET", "/api/v1/me").status_code == 200
    # Time passes: move the window into the past (grant and membership together).
    with admin_engine.begin() as c:
        c.execute(
            text(
                "UPDATE ops.break_glass_grants SET starts_at = now() - interval '20 minutes', "
                "expires_at = now() - interval '5 minutes' WHERE id = :g"
            ),
            {"g": grant_id},
        )
        c.execute(
            text(
                "UPDATE core.memberships SET created_at = now() - interval '20 minutes', "
                "expires_at = now() - interval '5 minutes' WHERE id = :m"
            ),
            {"m": uuid.UUID(body["membership_id"])},
        )
    # Refused at once by sign-in resolution, before any job runs.
    assert api.call(operator, "GET", "/api/v1/me").status_code == 403
    swept = service.sweep_school(campus.tenant_id)
    assert swept["expired"] == 1
    assert grant_row(admin_engine, request_id)["status"] == "expired"
    assert platform_request(admin_engine, request_id)["status"] == "expired"
    assert platform_audit(admin_engine, request_id)[-1] == "breakglass.expired"
    expired = _tenant_audit(admin_engine, campus.tenant_id, "breakglass.expired")
    assert [e["resource_id"] for e in expired] == [grant_id]
    assert expired[0]["summary"]["membership_id"] == body["membership_id"]
    with admin_engine.connect() as c:
        status: object = c.execute(
            text("SELECT status FROM core.memberships WHERE id = :m"),
            {"m": uuid.UUID(body["membership_id"])},
        ).scalar_one()
    assert status == "removed"
    assert "breakglass.expired" in notifications_of(
        admin_engine, campus.person("owner").membership_id
    )
    assert service.sweep_school(campus.tenant_id)["expired"] == 0, "idempotent"


def test_US_103_same_operator_can_be_granted_again_later(
    campus: Campus, api: Any, admin_engine: Engine, make_operator: MakeOperator
) -> None:
    op = make_operator("support_agent")
    _, first = _pending(campus, op)
    one = api.call(campus.person("owner"), "POST", f"{BASE}/requests/{first}/approve").json()
    api.call(campus.person("owner"), "POST", f"{BASE}/grants/{first}/revoke")
    _, second = _pending(campus, op, scope={"class_id": campus.id("class_x")})
    two = api.call(campus.person("owner"), "POST", f"{BASE}/requests/{second}/approve")
    assert two.status_code == 200, two.text
    assert two.json()["membership_id"] == one["membership_id"], "one membership per person"
    operator = Caller(op.subject)
    assert api.call(operator, "GET", f"/api/v1/classes/{campus.id('class_x')}").status_code == 200
    assert api.call(operator, "GET", f"/api/v1/classes/{campus.id('class_ix')}").status_code == 404


def test_FR_IAM_014_staff_cannot_assign_or_edit_support_access_by_hand(
    campus: Campus, api: Any, admin_engine: Engine, make_operator: MakeOperator
) -> None:
    op = make_operator("support_agent")
    _, grant_id = _pending(campus, op)
    api.call(campus.person("owner"), "POST", f"{BASE}/requests/{grant_id}/approve")
    owner = campus.person("owner")
    roles = api.call(owner, "GET", "/api/v1/roles").json()["data"]
    assert "platform_support" not in {r["key"] for r in roles}
    invite = api.call(
        owner,
        "POST",
        "/api/v1/users",
        json={
            "idp_subject": f"sub-{uuid.uuid4().hex}",
            "display_name": "Synthetic Helper",
            "roles": ["platform_support"],
        },
    )
    assert invite.status_code == 403
    assert invite.json()["code"] == "role_not_grantable"
    with admin_engine.connect() as c:
        user_id: object = c.execute(
            text("SELECT id FROM core.users WHERE idp_subject = :s"), {"s": op.subject}
        ).scalar_one()
    for method, path, body in (
        ("PUT", f"/api/v1/users/{user_id}/roles", {"roles": ["teacher"]}),
        ("PUT", f"/api/v1/users/{user_id}/scopes", {"scopes": []}),
    ):
        res = api.call(owner, method, path, json=body)
        assert res.status_code == 409, path
        assert res.json()["code"] == "breakglass_membership"
    listed = api.call(owner, "GET", "/api/v1/users", params={"limit": 200}).json()["data"]
    support = next(u for u in listed if u["id"] == str(user_id))
    assert support["roles"] == ["platform_support"], "visible to the school"


# --- emergency access: two operators, no school approval, school told at once ---------------


@pytest.mark.usefixtures("telugu_on")  # Telugu output: switched on (ADR-0036)
def test_SEC_029_emergency_access_needs_two_operators_and_notifies_the_school(
    campus: Campus, api: Any, admin_engine: Engine, make_operator: MakeOperator
) -> None:
    requester = make_operator("platform_owner")
    second = make_operator("platform_owner")
    # The requester already has a SchoolOS sign-in (an earlier approved support session).
    other = W.School(W.provision_school())
    other.people["owner"] = W.add_member(admin_engine, other.tenant_id, ["owner"])
    from .conftest import register_deployment

    register_deployment(other.tenant_id)
    _, earlier = _pending(Campus(other), requester)
    api.call(other.people["owner"], "POST", f"{BASE}/requests/{earlier}/approve")

    request_id = raise_request(
        campus.tenant_id,
        requester,
        minutes=120,
        emergency=True,
        reason_code="security_incident",
    )
    assert service.sync_school(campus.tenant_id).emergency == 0, "one confirmation is not enough"
    platform_breakglass.emergency_confirm(requester.actor, request_id)
    with pytest.raises(Conflict) as same:
        platform_breakglass.emergency_confirm(requester.actor, request_id)
    assert same.value.code == "same_operator"
    service.sync_school(campus.tenant_id)
    with admin_engine.connect() as c:
        assert (
            c.execute(
                text("SELECT count(*) FROM ops.break_glass_grants WHERE platform_request_id = :r"),
                {"r": request_id},
            ).scalar_one()
            == 0
        )
    platform_breakglass.emergency_confirm(second.actor, request_id)

    result = service.sync_school(campus.tenant_id)
    assert result.emergency == 1
    grant = grant_row(admin_engine, request_id)
    assert grant["emergency"] is True
    assert grant["status"] == "active"
    assert grant["approved_by_membership"] is None
    assert grant["expires_at"] - grant["starts_at"] == dt.timedelta(minutes=120)
    # The school is told immediately: notification + audit (actor: the platform).
    for role in ("owner", "principal"):
        assert "breakglass.emergency" in notifications_of(
            admin_engine, campus.person(role).membership_id
        )
    events = _tenant_audit(admin_engine, campus.tenant_id, "breakglass.emergency_opened")
    assert len(events) == 1
    assert events[0]["summary"]["operator_confirmations"] == 2
    assert events[0]["summary"]["access_opened"] is True
    note = api.call(
        campus.person("principal"),
        "GET",
        "/api/v1/notifications",
        headers={"Accept-Language": "te"},
    ).json()["data"][0]
    assert note["template_key"] == "breakglass.emergency"
    assert "భద్రతా సంఘటన" in note["body"]
    # Read-only access works; the school can end it now.
    operator = Caller(requester.subject)
    assert api.call(operator, "GET", "/api/v1/me", tenant=campus.tenant_id).status_code == 200
    assert platform_request(admin_engine, request_id)["status"] == "active"
    res = api.call(campus.person("owner"), "POST", f"{BASE}/grants/{grant['id']}/revoke")
    assert res.status_code == 200, res.text
    assert api.call(operator, "GET", "/api/v1/me", tenant=campus.tenant_id).status_code == 403
    assert platform_request(admin_engine, request_id)["status"] == "revoked"
    # School approval routes never apply to emergency grants.
    assert (
        api.call(
            campus.person("owner"), "POST", f"{BASE}/requests/{grant['id']}/approve"
        ).status_code
        == 409
    )


def test_SEC_029_emergency_for_operator_without_school_sign_in_fails_closed(
    campus: Campus, api: Any, admin_engine: Engine, make_operator: MakeOperator
) -> None:
    requester = make_operator("platform_owner")
    second = make_operator("platform_owner")
    request_id = raise_request(
        campus.tenant_id, requester, emergency=True, reason_code="legal_obligation"
    )
    platform_breakglass.emergency_confirm(requester.actor, request_id)
    platform_breakglass.emergency_confirm(second.actor, request_id)
    service.sync_school(campus.tenant_id)
    grant = grant_row(admin_engine, request_id)
    assert grant["status"] == "approved"
    assert grant["membership_id"] is None
    assert api.call(Caller(requester.subject), "GET", "/api/v1/me").status_code == 403
    # The school is still told, and can close it.
    assert "breakglass.emergency" in notifications_of(
        admin_engine, campus.person("owner").membership_id
    )
    events = _tenant_audit(admin_engine, campus.tenant_id, "breakglass.emergency_opened")
    assert events[-1]["summary"]["access_opened"] is False
    res = api.call(campus.person("owner"), "POST", f"{BASE}/grants/{grant['id']}/revoke")
    assert res.status_code == 200
    assert platform_request(admin_engine, request_id)["status"] == "revoked"


# --- control-plane bridge -------------------------------------------------------------------


def test_FR_OPS_004_control_plane_outcome_is_scoped_and_idempotent(
    campus: Campus, admin_engine: Engine, make_operator: MakeOperator
) -> None:
    op = make_operator("support_agent")
    request_id = raise_request(campus.tenant_id, op)
    other_school = uuid.uuid4()
    grant = uuid.uuid4()
    assert not platform_breakglass.record_school_outcome(
        other_school, request_id, "active", grant_id=grant
    )
    assert not platform_breakglass.record_school_outcome(
        campus.tenant_id, request_id, "revoked", grant_id=grant
    ), "requested -> revoked is not a valid transition"
    assert platform_breakglass.record_school_outcome(
        campus.tenant_id, request_id, "denied", grant_id=grant
    )
    assert not platform_breakglass.record_school_outcome(
        campus.tenant_id, request_id, "denied", grant_id=grant
    )
    assert platform_audit(admin_engine, request_id) == ["breakglass.requested", "breakglass.denied"]
    assert platform_breakglass.request_for_school(other_school, request_id) is None


def test_ADR_0015_dedicated_hosts_do_not_pull_from_a_control_plane(
    campus: Campus, make_operator: MakeOperator, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.core.config import DeploymentMode, Settings

    op = make_operator("support_agent")
    raise_request(campus.tenant_id, op)
    dedicated = Settings(deployment_mode=DeploymentMode.DEDICATED)
    monkeypatch.setattr(service, "get_settings", lambda: dedicated)
    assert service.sync_school(campus.tenant_id) == service.SyncResult()
    assert service.report_outcomes(campus.tenant_id) == 0
