"""Operators and platform roles, bootstrap CLI, emergency break-glass (FR-PLT-028, SEC-029)."""

from __future__ import annotations

import uuid
from collections.abc import Callable

import pytest
from sqlalchemy import text

from app.core.db import platform_session
from app.platform import bootstrap_owner, operators
from app.platform import repository as repo

from .conftest import Api, MakeOperator, Operator, provision_payload

pytestmark = pytest.mark.db


def test_FR_PLT_028_invite_assign_roles_and_deactivate(
    api: Api, owner: Operator, make_operator: MakeOperator
) -> None:
    body = {
        "email": f"new-{uuid.uuid4().hex[:6]}@example.test",
        "display_name": "Synthetic New Operator",
        "idp_subject": f"sub-{uuid.uuid4()}",
        "roles": ["support_agent"],
    }
    invited = api.call("POST", "/operators", owner, json=body)
    assert invited.status_code == 201, invited.text
    op_id = invited.json()["id"]
    assert (invited.json()["status"], invited.json()["roles"]) == ("invited", ["support_agent"])
    roles = api.call(
        "PUT",
        f"/operators/{op_id}/roles",
        owner,
        json={"roles": ["billing_admin", "platform_viewer"]},
    )
    assert roles.json()["roles"] == ["billing_admin", "platform_viewer"]
    own = api.call(
        "PUT", f"/operators/{owner.id}/roles", owner, json={"roles": ["platform_viewer"]}
    )
    assert (own.status_code, own.json()["code"]) == (409, "own_roles")
    gone = api.call("POST", f"/operators/{op_id}/deactivate", owner)
    assert gone.json()["status"] == "deactivated"
    listing = api.call("GET", "/operators?limit=200", owner).json()["data"]
    assert any(o["id"] == op_id for o in listing)
    stale = api.call("POST", "/operators", owner, json=body, fresh=False)
    assert stale.status_code == 428


def test_FR_PLT_028_last_platform_owner_cannot_be_removed(
    make_operator: MakeOperator, monkeypatch: pytest.MonkeyPatch
) -> None:
    only, admin = make_operator("platform_owner"), make_operator("platform_owner")
    monkeypatch.setattr(repo, "active_owner_ids", lambda _s, lock=False: [only.id])
    from app.core.errors import Conflict

    with pytest.raises(Conflict, match="owner"):
        operators.set_roles(admin.actor, only.id, ["platform_viewer"])
    with pytest.raises(Conflict, match="owner"):
        operators.deactivate(admin.actor, only.id)


def test_FR_PLT_028_bootstrap_refuses_when_an_owner_exists(
    owner: Operator, capsys: pytest.CaptureFixture[str]
) -> None:
    code = bootstrap_owner.main(
        [
            "--subject",
            f"sub-{uuid.uuid4()}",
            "--email",
            "founder@example.test",
            "--display-name",
            "Founder",
        ]
    )
    assert code == 1
    assert "owner_exists" in capsys.readouterr().err


def test_FR_PLT_028_bootstrap_creates_first_owner(
    platform_engine: object, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(repo, "active_owner_ids", lambda _s, lock=False: [])
    subject = f"sub-{uuid.uuid4()}"
    code = bootstrap_owner.main(
        [
            "--subject",
            subject,
            "--email",
            f"f-{uuid.uuid4().hex[:6]}@example.test",
            "--display-name",
            "Founder",
        ]
    )
    assert code == 0
    new_id = uuid.UUID(capsys.readouterr().out.strip())
    with platform_session() as s:
        row = s.execute(
            text("SELECT status, mfa_enrolled FROM platform.operators WHERE id = :i"), {"i": new_id}
        ).one()
        role = s.execute(
            text("SELECT role_key, granted_by FROM platform.operator_roles WHERE operator_id = :i"),
            {"i": new_id},
        ).one()
    assert tuple(row) == ("active", True)
    assert tuple(role) == ("platform_owner", None)


def test_SEC_029_emergency_breakglass_needs_two_different_operators(
    api: Api, make_operator: MakeOperator, owner: Operator, make_plan: Callable[..., uuid.UUID]
) -> None:
    agent = make_operator("support_agent")
    first, second = make_operator("platform_owner"), make_operator("platform_owner")
    tid = api.call("POST", "/tenants", owner, json=provision_payload(make_plan())).json()[
        "tenant_id"
    ]
    req = api.call(
        "POST",
        "/break-glass-requests",
        agent,
        json={
            "tenant_id": tid,
            "reason_code": "security_incident",
            "reason": "Suspected account takeover under investigation",
            "scope": {"resource": "audit.read"},
            "duration_minutes": 60,
            "emergency": True,
        },
    )
    assert req.status_code == 201, req.text
    rid = req.json()["id"]
    assert (
        api.call("POST", f"/break-glass-requests/{rid}/emergency-confirm", agent).status_code == 403
    )
    one = api.call("POST", f"/break-glass-requests/{rid}/emergency-confirm", first)
    assert one.json()["status"] == "requested"
    same = api.call("POST", f"/break-glass-requests/{rid}/emergency-confirm", first)
    assert (same.status_code, same.json()["code"]) == (409, "same_operator")
    two = api.call("POST", f"/break-glass-requests/{rid}/emergency-confirm", second)
    assert two.json()["status"] == "approved"
    listed = api.call("GET", "/break-glass-requests", make_operator("platform_viewer")).json()[
        "data"
    ]
    assert any(r["id"] == rid for r in listed)
    too_long = api.call(
        "POST",
        "/break-glass-requests",
        agent,
        json={
            "tenant_id": tid,
            "reason_code": "support_request",
            "reason": "Needs a long window here",
            "scope": {},
            "duration_minutes": 481,
        },
    )
    assert too_long.status_code == 422
