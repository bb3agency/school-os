"""Invitations to an existing account need that person's consent (audit DL-09, owner decision
2026-10-04; ADR-0023 amendment, ADR-0019, US-102, SEC-001).

A school that knows another school's user's IdP subject could invite them with ``POST /users``,
read their email while the invitation was pending, and have them attached on their next sign-in
without being asked. Now: sign-in accepts only a brand-new account's only invitation; anyone else
accepts or declines explicitly (``/me/invitations``), both audited in the inviting school's chain;
the inviting school does not see the person's email while the invitation is open and cannot
activate it by hand; acceptance matches the identity's issuer. Synthetic data only.
"""

from __future__ import annotations

import sys
import uuid
from typing import Any

import pytest
from sqlalchemy import Engine, text

pytestmark = pytest.mark.db
W = sys.modules["sos_test_api_world"]
USERS = "/api/v1/users"
EMAIL = "existing.teacher@example.test"


def _status(admin: Engine, membership_id: uuid.UUID) -> str:
    with admin.connect() as c:
        return str(
            c.execute(
                text("SELECT status FROM core.memberships WHERE id = :m"), {"m": membership_id}
            ).scalar_one()
        )


def _events(admin: Engine, tenant: uuid.UUID, action: str, resource: uuid.UUID) -> list[Any]:
    return [e for e in W.audit_events(admin, tenant, action) if e["resource_id"] == resource]


def _invite_existing(api: Any, admin_engine: Engine) -> tuple[Any, Any, uuid.UUID, dict[str, Any]]:
    """School A's teacher (an existing account) invited by school B's owner through the API."""
    a, b = W.provision_school(), W.provision_school()
    W.add_member(admin_engine, a, ["owner"])
    person = W.add_member(admin_engine, a, ["teacher"], email=EMAIL)
    owner_b = W.add_member(admin_engine, b, ["owner"])
    res = api.call(
        owner_b,
        "POST",
        USERS,
        json={
            "idp_subject": person.subject,
            "display_name": "Synthetic Typed Name",
            "email": "typed@example.test",
            "preferred_language": "en",
            "roles": ["teacher"],
            "scopes": [],
        },
    )
    assert res.status_code == 201, res.text
    return person, owner_b, b, res.json()


def test_DL_09_inviting_school_does_not_see_the_existing_accounts_email(
    api: Any, admin_engine: Engine
) -> None:
    person, owner_b, _b, invited = _invite_existing(api, admin_engine)
    assert invited["status"] == "invited"
    assert invited["email"] is None
    assert invited["contact_hidden"] is True
    one = api.call(owner_b, "GET", f"{USERS}/{person.user_id}")
    assert one.status_code == 200, one.text
    assert (one.json()["email"], one.json()["contact_hidden"]) == (None, True)
    listed = api.call(owner_b, "GET", USERS, params={"limit": 100})
    row = next(u for u in listed.json()["data"] if u["id"] == str(person.user_id))
    assert row["email"] is None
    assert EMAIL not in listed.text


def test_DL_09_sign_in_does_not_accept_an_invitation_to_an_existing_account(
    api: Any, admin_engine: Engine
) -> None:
    person, _owner_b, b, invited = _invite_existing(api, admin_engine)
    mid = uuid.UUID(invited["membership_id"])
    res = api.call(person, "POST", "/api/v1/me/accept-invitations")
    assert res.status_code == 200, res.text
    assert res.json() == {"accepted": []}
    assert _status(admin_engine, mid) == "invited"
    schools = api.call(person, "GET", "/api/v1/me/schools").json()["data"]
    assert str(b) not in {s["tenant_id"] for s in schools}


def test_DL_09_the_person_sees_and_accepts_the_invitation(api: Any, admin_engine: Engine) -> None:
    person, owner_b, b, invited = _invite_existing(api, admin_engine)
    mid = uuid.UUID(invited["membership_id"])
    pending = api.call(person, "GET", "/api/v1/me/invitations")
    assert pending.status_code == 200, pending.text
    [item] = [i for i in pending.json()["data"] if i["membership_id"] == str(mid)]
    assert item["tenant_id"] == str(b)
    assert item["school_name"] == "Synthetic Model School"
    assert item["roles"] == ["teacher"]
    accepted = api.call(person, "POST", f"/api/v1/me/invitations/{mid}/accept")
    assert accepted.status_code == 200, accepted.text
    assert accepted.json() == {"tenant_id": str(b), "membership_id": str(mid), "status": "active"}
    assert _status(admin_engine, mid) == "active"
    [event] = _events(admin_engine, b, "membership.invitation_accepted", mid)
    assert event["actor_id"] == person.user_id
    # Once they accepted, the school sees the person as any other member.
    one = api.call(owner_b, "GET", f"{USERS}/{person.user_id}").json()
    assert (one["email"], one["contact_hidden"]) == (EMAIL, False)
    again = api.call(person, "POST", f"/api/v1/me/invitations/{mid}/accept")
    assert (again.status_code, again.json()["code"]) == (404, "invitation_not_found")


def test_DL_09_the_person_declines_the_invitation(api: Any, admin_engine: Engine) -> None:
    person, owner_b, b, invited = _invite_existing(api, admin_engine)
    mid = uuid.UUID(invited["membership_id"])
    declined = api.call(person, "POST", f"/api/v1/me/invitations/{mid}/decline")
    assert declined.status_code == 200, declined.text
    assert declined.json()["status"] == "removed"
    assert _status(admin_engine, mid) == "removed"
    [event] = _events(admin_engine, b, "membership.invitation_declined", mid)
    assert event["actor_id"] == person.user_id
    assert api.call(person, "GET", "/api/v1/me/invitations").json()["data"] == []
    one = api.call(owner_b, "GET", f"{USERS}/{person.user_id}").json()
    assert (one["status"], one["email"], one["contact_hidden"]) == ("removed", None, True)


def test_DL_09_inviting_school_does_not_see_when_the_existing_account_signs_in(
    api: Any, admin_engine: Engine
) -> None:
    """Audit 2026-10-05 A-03: ``last_login_at`` is activity in the person's other school. Until
    they accept (and after a decline) the inviting school does not see it, as with the email."""
    person, owner_b, _b, invited = _invite_existing(api, admin_engine)
    with admin_engine.begin() as c:
        c.execute(
            text("UPDATE core.users SET last_login_at = now() WHERE id = :u"),
            {"u": person.user_id},
        )
    one = api.call(owner_b, "GET", f"{USERS}/{person.user_id}").json()
    assert (one["contact_hidden"], one["last_login_at"]) == (True, None)
    listed = api.call(owner_b, "GET", USERS, params={"limit": 100}).json()["data"]
    row = next(u for u in listed if u["id"] == str(person.user_id))
    assert row["last_login_at"] is None
    mid = uuid.UUID(invited["membership_id"])
    assert api.call(person, "POST", f"/api/v1/me/invitations/{mid}/decline").status_code == 200
    one = api.call(owner_b, "GET", f"{USERS}/{person.user_id}").json()
    assert (one["status"], one["last_login_at"]) == ("removed", None)


def test_DL_09_inviting_school_cannot_activate_the_invitation_itself(
    api: Any, admin_engine: Engine
) -> None:
    person, owner_b, _b, invited = _invite_existing(api, admin_engine)
    etag = api.call(owner_b, "GET", f"{USERS}/{person.user_id}").headers["ETag"]
    res = api.call(
        owner_b,
        "PATCH",
        f"{USERS}/{person.user_id}",
        json={"status": "active"},
        headers={"If-Match": etag},
    )
    assert (res.status_code, res.json()["code"]) == (409, "invitation_needs_consent")
    assert _status(admin_engine, uuid.UUID(invited["membership_id"])) == "invited"


def test_DL_09_nobody_else_answers_someone_elses_invitation(api: Any, admin_engine: Engine) -> None:
    person, owner_b, _b, invited = _invite_existing(api, admin_engine)
    mid = invited["membership_id"]
    for who in (owner_b, W.add_member(admin_engine, W.provision_school(), ["teacher"])):
        for verb in ("accept", "decline"):
            res = api.call(who, "POST", f"/api/v1/me/invitations/{mid}/{verb}")
            assert (res.status_code, res.json()["code"]) == (404, "invitation_not_found")
        assert mid not in api.call(who, "GET", "/api/v1/me/invitations").text
    assert _status(admin_engine, uuid.UUID(mid)) == "invited"
    assert api.call(person, "GET", "/api/v1/me/invitations").json()["data"]


def test_DL_09_a_brand_new_accounts_only_invitation_is_still_accepted_at_sign_in(
    world: Any, api: Any, admin_engine: Engine
) -> None:
    invitee = W.add_member(admin_engine, world.a.tenant_id, ["teacher"], status="invited")
    res = api.call(invitee, "POST", "/api/v1/me/accept-invitations")
    assert res.json() == {"accepted": [str(world.a.tenant_id)]}
    assert _status(admin_engine, invitee.membership_id) == "active"


def test_DL_09_acceptance_matches_the_issuer(admin_engine: Engine) -> None:
    """ADR-0023: the identity is (issuer, subject); another issuer's account with the same
    subject never accepts or answers this one's invitations."""
    tid = W.provision_school()
    invitee = W.add_member(admin_engine, tid, ["teacher"], status="invited")
    other = "https://other-issuer.example.test"
    with admin_engine.begin() as c:
        c.execute(text("SET LOCAL ROLE sos_app"))
        rows = c.execute(
            text("SELECT * FROM core.accept_invitations(:s, :i)"),
            {"s": invitee.subject, "i": other},
        ).all()
        assert rows == []
        listed = c.execute(
            text("SELECT * FROM core.pending_invitations(:s, :i)"),
            {"s": invitee.subject, "i": other},
        ).all()
        assert listed == []
        answered = c.execute(
            text("SELECT * FROM core.respond_to_invitation(:s, :i, :m, true)"),
            {"s": invitee.subject, "i": other, "m": invitee.membership_id},
        ).all()
        assert answered == []
    assert _status(admin_engine, invitee.membership_id) == "invited"


@pytest.mark.parametrize(
    "signature",
    [
        "core.accept_invitations(text, text)",
        "core.pending_invitations(text, text)",
        "core.respond_to_invitation(text, text, uuid, boolean)",
        "core.accept_invitations(text)",
    ],
)
def test_DL_09_functions_are_pinned_and_not_callable_by_other_roles(
    admin_engine: Engine, signature: str
) -> None:
    with admin_engine.connect() as c:
        row = c.execute(
            text(
                "SELECT r.rolname AS owner, p.prosecdef, "
                "coalesce(array_to_string(p.proconfig, ','), '') AS config "
                "FROM pg_proc p JOIN pg_roles r ON r.oid = p.proowner "
                "WHERE p.oid = CAST(:sig AS regprocedure)"
            ),
            {"sig": signature},
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
                text("SELECT has_function_privilege(:r, :sig, 'EXECUTE')"),
                {"r": role, "sig": signature},
            ).scalar_one()
            assert can is allowed, (signature, role)
