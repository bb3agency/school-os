"""Invited owners and principals of a suspended school can answer their invitation (lead decision
D67, ADR-0056; migration 0078_accept_invite_suspended; BR-08, FR-PLT-004, FR-IAM-013, ADR-0019,
ADR-0023 amendment).

``core.accept_invitations``, ``core.pending_invitations`` and ``core.respond_to_invitation``
answer an invitation while its school is ``active``, or while it is ``suspended`` when the invited
membership holds the ``owner`` or ``principal`` role (the roles of the suspended-school allowlist,
``SUSPENDED_ROLES``). Once accepted, they reach only the allowlisted routes. Any other invitation
in a suspended school stays unanswered; provisioning, offboarding and deleted schools never
answer. Synthetic data only.
"""

from __future__ import annotations

import sys
import uuid
from collections.abc import Callable
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import make_url

from app.authz.resolver import SUSPENDED_ROLES
from app.core.db import platform_session
from app.tenancy import service as tenancy

pytestmark = pytest.mark.db
W = sys.modules["sos_test_api_world"]

REVISION = "0078_accept_invite_suspended"
PREVIOUS = "0075_breach_retention"
DB = "schoolos_accept_invite_suspended"
ACCEPT = "/api/v1/me/accept-invitations"
SIGNATURES = (
    "core.accept_invitations(text, text)",
    "core.pending_invitations(text, text)",
    "core.respond_to_invitation(text, text, uuid, boolean)",
)


def _status(admin: Engine, membership_id: uuid.UUID) -> str:
    with admin.connect() as c:
        return str(
            c.execute(
                text("SELECT status FROM core.memberships WHERE id = :m"), {"m": membership_id}
            ).scalar_one()
        )


def _set_school_status(admin: Engine, tid: uuid.UUID, status: str) -> None:
    with admin.begin() as c:
        c.execute(
            text("UPDATE core.tenants SET status = :s WHERE id = :i"), {"s": status, "i": tid}
        )


def _suspended_school() -> uuid.UUID:
    tid: uuid.UUID = W.provision_school()
    with platform_session() as pdb:
        tenancy.suspend_tenant(pdb, tid)
    return tid


def test_D67_the_owner_and_principal_are_the_suspended_allowlist_roles() -> None:
    # The SQL condition of 0078 names these two role keys; change both together (ADR-0056).
    assert frozenset({"owner", "principal"}) == SUSPENDED_ROLES


@pytest.mark.parametrize("role", ["owner", "principal"])
def test_D67_FR_PLT_004_invited_owner_or_principal_of_suspended_school_accepts(
    api: Any, admin_engine: Engine, role: str
) -> None:
    tid = _suspended_school()
    invitee = W.add_member(admin_engine, tid, [role], status="invited")
    res = api.call(invitee, "POST", ACCEPT)
    assert res.status_code == 200, res.text
    assert res.json() == {"accepted": [str(tid)]}
    assert _status(admin_engine, invitee.membership_id) == "active"
    events = W.audit_events(admin_engine, tid, "membership.invitation_accepted")
    assert [e["actor_id"] for e in events if e["resource_id"] == invitee.membership_id] == [
        invitee.user_id
    ]
    # Then only the suspended-school allowlist (BR-08): who am I and Plan & billing.
    me = api.call(invitee, "GET", "/api/v1/me")
    assert me.status_code == 200, me.text
    assert me.json()["tenant_status"] == "suspended"
    assert api.call(invitee, "GET", "/api/v1/tenant/billing").status_code == 200
    users = api.call(invitee, "GET", "/api/v1/users")
    assert (users.status_code, users.json()["code"]) == (403, "tenant_suspended")


@pytest.mark.parametrize("role", ["teacher", "office_admin", "accountant"])
def test_D67_other_invited_staff_of_suspended_school_cannot_accept(
    api: Any, admin_engine: Engine, role: str
) -> None:
    tid = _suspended_school()
    invitee = W.add_member(admin_engine, tid, [role], status="invited")
    assert api.call(invitee, "POST", ACCEPT).json() == {"accepted": []}
    assert _status(admin_engine, invitee.membership_id) == "invited"
    assert api.call(invitee, "GET", "/api/v1/me/invitations").json()["data"] == []
    res = api.call(invitee, "POST", f"/api/v1/me/invitations/{invitee.membership_id}/accept")
    assert (res.status_code, res.json()["code"]) == (404, "invitation_not_found")
    assert _status(admin_engine, invitee.membership_id) == "invited"
    # Once the school is active again, the invitation is answered as before.
    with platform_session() as pdb:
        tenancy.reactivate_tenant(pdb, tid)
    assert api.call(invitee, "POST", ACCEPT).json() == {"accepted": [str(tid)]}


def test_D67_existing_account_invited_as_owner_of_suspended_school_answers_explicitly(
    api: Any, admin_engine: Engine
) -> None:
    """A person who already has a membership elsewhere (DL-09) sees and accepts it on
    ``/me/invitations``, the same rule."""
    other, tid = W.provision_school(), _suspended_school()
    person = W.add_member(admin_engine, other, ["teacher"])
    mid = uuid.uuid4()
    with admin_engine.begin() as c:
        c.execute(
            text(
                "INSERT INTO core.memberships (id, tenant_id, user_id, status) "
                "VALUES (:m, :t, :u, 'invited')"
            ),
            {"m": mid, "t": tid, "u": person.user_id},
        )
        c.execute(
            text(
                "INSERT INTO core.membership_roles (tenant_id, membership_id, role_id) "
                "SELECT :t, :m, r.id FROM core.roles r WHERE r.tenant_id = :t AND r.key = 'owner'"
            ),
            {"t": tid, "m": mid},
        )
    assert api.call(person, "POST", ACCEPT).json() == {"accepted": []}  # DL-09 unchanged
    pending = api.call(person, "GET", "/api/v1/me/invitations").json()["data"]
    assert [(i["membership_id"], i["roles"]) for i in pending] == [(str(mid), ["owner"])]
    res = api.call(person, "POST", f"/api/v1/me/invitations/{mid}/accept")
    assert res.status_code == 200, res.text
    assert _status(admin_engine, mid) == "active"


def test_D67_active_school_unchanged(api: Any, admin_engine: Engine) -> None:
    tid = W.provision_school()
    invitee = W.add_member(admin_engine, tid, ["teacher"], status="invited")
    assert api.call(invitee, "POST", ACCEPT).json() == {"accepted": [str(tid)]}


@pytest.mark.parametrize("status", ["offboarding", "deleted"])
@pytest.mark.parametrize("role", ["owner", "principal", "teacher"])
def test_D67_offboarding_and_deleted_schools_never_answer(
    api: Any, admin_engine: Engine, status: str, role: str
) -> None:
    tid = W.provision_school()
    invitee = W.add_member(admin_engine, tid, [role], status="invited")
    _set_school_status(admin_engine, tid, status)
    assert api.call(invitee, "POST", ACCEPT).json() == {"accepted": []}
    assert api.call(invitee, "GET", "/api/v1/me/invitations").json()["data"] == []
    res = api.call(invitee, "POST", f"/api/v1/me/invitations/{invitee.membership_id}/accept")
    assert res.status_code == 404
    assert _status(admin_engine, invitee.membership_id) == "invited"


def test_D67_provisioning_school_never_answers(api: Any, admin_engine: Engine) -> None:
    result = tenancy.provision_tenant(
        code=f"s-{uuid.uuid4().hex[:12]}", name="Synthetic Model School", wrapper=W.wrapper()
    )
    invitee = W.add_member(admin_engine, result.tenant_id, ["owner"], status="invited")
    assert api.call(invitee, "POST", ACCEPT).json() == {"accepted": []}
    assert _status(admin_engine, invitee.membership_id) == "invited"


# --- migration --------------------------------------------------------------------------------


def _functions(engine: Engine) -> dict[str, tuple[str, bool, str, str]]:
    """signature -> (owner, security definer, config, body)."""
    out: dict[str, tuple[str, bool, str, str]] = {}
    with engine.connect() as c:
        for sig in SIGNATURES:
            row = c.execute(
                text(
                    "SELECT r.rolname, p.prosecdef, "
                    "coalesce(array_to_string(p.proconfig, ','), ''), p.prosrc "
                    "FROM pg_proc p JOIN pg_roles r ON r.oid = p.proowner "
                    "WHERE p.oid = CAST(:s AS regprocedure)"
                ),
                {"s": sig},
            ).one()
            out[sig] = (row[0], row[1], row[2], row[3])
    return out


def _executors(engine: Engine, sig: str) -> set[str]:
    with engine.connect() as c:
        return {
            role
            for role in ("sos_app", "sos_platform", "sos_readonly", "public")
            if c.execute(
                text("SELECT has_function_privilege(:r, :s, 'EXECUTE')"), {"r": role, "s": sig}
            ).scalar_one()
        }


def _assert_pinned(engine: Engine, *, suspended: bool) -> None:
    for sig, (owner, secdef, config, body) in _functions(engine).items():
        assert (owner, secdef) == ("sos_definer", True), sig
        assert config == "search_path=pg_catalog, pg_temp", sig
        assert ("'suspended'" in body) is suspended, sig
        assert _executors(engine, sig) == {"sos_app"}, sig


def test_D67_migration_0078_round_trip(
    test_database: Any, make_alembic_config: Callable[[str], Config]
) -> None:
    url = test_database.create_fresh(DB)
    cfg = make_alembic_config(url)
    command.upgrade(cfg, REVISION)
    engine = create_engine(make_url(test_database.admin_url).set(database=DB))
    try:
        _assert_pinned(engine, suspended=True)
        command.downgrade(cfg, PREVIOUS)
        _assert_pinned(engine, suspended=False)
        command.upgrade(cfg, "head")
        _assert_pinned(engine, suspended=True)
    finally:
        engine.dispose()
