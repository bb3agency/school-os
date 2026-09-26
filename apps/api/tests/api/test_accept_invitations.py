"""Invitation acceptance on first sign-in (ADR-0019, FR-IAM-013, US-102, FR-TEN-003).

An invited membership becomes active only when the invitee signs in with the IdP subject the
invite was made for, within 30 days; only `invited` memberships in active schools change.
"""

from __future__ import annotations

import sys
import uuid
from typing import Any

import pytest
from sqlalchemy import Engine, text

pytestmark = pytest.mark.db
W = sys.modules["sos_test_api_world"]


def _status(admin: Engine, membership_id: uuid.UUID) -> str:
    with admin.connect() as c:
        return str(
            c.execute(
                text("SELECT status FROM core.memberships WHERE id = :m"), {"m": membership_id}
            ).scalar_one()
        )


def test_ADR_0019_invitee_accepts_on_first_sign_in(
    world: Any, api: Any, admin_engine: Engine
) -> None:
    invitee = W.add_member(admin_engine, world.a.tenant_id, ["office_staff"], status="invited")
    # Before acceptance the invitee has no usable membership.
    assert api.call(invitee, "GET", "/api/v1/me").status_code == 403
    res = api.call(invitee, "POST", "/api/v1/me/accept-invitations")
    assert res.status_code == 200, res.text
    assert res.json() == {"accepted": [str(world.a.tenant_id)]}
    assert _status(admin_engine, invitee.membership_id) == "active"
    assert api.call(invitee, "GET", "/api/v1/me").status_code == 200
    events = W.audit_events(admin_engine, world.a.tenant_id, "membership.invitation_accepted")
    assert [e["resource_id"] for e in events] == [invitee.membership_id]
    assert events[0]["actor_id"] == invitee.user_id


def test_ADR_0019_accepting_twice_changes_nothing(
    world: Any, api: Any, admin_engine: Engine
) -> None:
    invitee = W.add_member(admin_engine, world.a.tenant_id, ["teacher"], status="invited")
    assert api.call(invitee, "POST", "/api/v1/me/accept-invitations").json()["accepted"]
    again = api.call(invitee, "POST", "/api/v1/me/accept-invitations")
    assert again.status_code == 200
    assert again.json() == {"accepted": []}
    events = W.audit_events(admin_engine, world.a.tenant_id, "membership.invitation_accepted")
    assert sum(1 for e in events if e["resource_id"] == invitee.membership_id) == 1


def test_ADR_0019_only_the_callers_own_invitations_change(
    world: Any, api: Any, admin_engine: Engine
) -> None:
    mine = W.add_member(admin_engine, world.a.tenant_id, ["teacher"], status="invited")
    other = W.add_member(admin_engine, world.a.tenant_id, ["teacher"], status="invited")
    api.call(mine, "POST", "/api/v1/me/accept-invitations")
    assert _status(admin_engine, other.membership_id) == "invited"


@pytest.mark.parametrize("status", ["suspended", "removed"])
def test_ADR_0019_non_invited_memberships_are_never_reactivated(
    world: Any, api: Any, admin_engine: Engine, status: str
) -> None:
    person = W.add_member(admin_engine, world.a.tenant_id, ["teacher"], status=status)
    res = api.call(person, "POST", "/api/v1/me/accept-invitations")
    assert res.json() == {"accepted": []}
    assert _status(admin_engine, person.membership_id) == status


def test_ADR_0019_invitations_expire_after_30_days(
    world: Any, api: Any, admin_engine: Engine
) -> None:
    invitee = W.add_member(admin_engine, world.a.tenant_id, ["teacher"], status="invited")
    with admin_engine.begin() as c:
        c.execute(
            text(
                "UPDATE core.memberships SET created_at = now() - interval '31 days' WHERE id = :m"
            ),
            {"m": invitee.membership_id},
        )
    assert api.call(invitee, "POST", "/api/v1/me/accept-invitations").json() == {"accepted": []}
    assert _status(admin_engine, invitee.membership_id) == "invited"


def test_ADR_0019_not_accepted_while_school_is_not_active(
    world: Any, api: Any, admin_engine: Engine
) -> None:
    result = W.tenancy.provision_tenant(  # never activated: stays in 'provisioning'
        code=f"s-{uuid.uuid4().hex[:12]}", name="Synthetic Model School", wrapper=W.wrapper()
    )
    tenant_id = result.tenant_id
    invitee = W.add_member(admin_engine, tenant_id, ["owner"], status="invited")
    assert api.call(invitee, "POST", "/api/v1/me/accept-invitations").json() == {"accepted": []}
    assert _status(admin_engine, invitee.membership_id) == "invited"


def test_ADR_0019_disabled_user_cannot_accept(world: Any, api: Any, admin_engine: Engine) -> None:
    invitee = W.add_member(admin_engine, world.a.tenant_id, ["teacher"], status="invited")
    with admin_engine.begin() as c:
        c.execute(
            text("UPDATE core.users SET status = 'disabled' WHERE id = :u"), {"u": invitee.user_id}
        )
    assert api.call(invitee, "POST", "/api/v1/me/accept-invitations").json() == {"accepted": []}


def test_ADR_0019_function_is_pinned_and_not_callable_by_other_roles(
    admin_engine: Engine,
) -> None:
    with admin_engine.connect() as c:
        row = c.execute(
            text(
                "SELECT r.rolname AS owner, p.prosecdef, "
                "coalesce(array_to_string(p.proconfig, ','), '') AS config "
                "FROM pg_proc p JOIN pg_roles r ON r.oid = p.proowner "
                "JOIN pg_namespace n ON n.oid = p.pronamespace "
                "WHERE n.nspname = 'core' AND p.proname = 'accept_invitations'"
            )
        ).one()
        assert row.owner == "sos_definer"
        assert row.prosecdef
        assert "search_path=" in row.config
        for role, allowed in (
            ("sos_app", True),
            ("sos_platform", False),
            ("sos_readonly", False),
            ("public", False),
        ):
            can: bool = c.execute(
                text(
                    "SELECT has_function_privilege(:r, 'core.accept_invitations(text)', 'EXECUTE')"
                ),
                {"r": role},
            ).scalar_one()
            assert can is allowed, role
