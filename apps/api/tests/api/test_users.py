"""Staff accounts, roles and scopes (US-102, FR-IAM-010..014, invariant 7)."""

from __future__ import annotations

import datetime as dt
import sys
import uuid
from typing import Any

import pytest
from sqlalchemy import Engine, text

from app.authz.kv import kv_store

pytestmark = pytest.mark.db
W = sys.modules["sos_test_api_world"]
USERS = "/api/v1/users"


def invite_body(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "idp_subject": f"sub-{uuid.uuid4().hex}",
        "display_name": "Synthetic Invitee",
        "email": "invitee@example.test",
        "preferred_language": "te",
        "roles": ["teacher"],
        "scopes": [],
    }
    body.update(overrides)
    return body


def events_for(admin: Engine, tenant: uuid.UUID, resource_id: uuid.UUID) -> list[str]:
    return [e["action"] for e in W.audit_events(admin, tenant) if e["resource_id"] == resource_id]


def test_US_102_AC1_invite_class_teacher_scoped_to_sections(
    world: Any, api: Any, admin_engine: Engine
) -> None:
    owner = world.person("office_admin")  # holds user.manage, not role.assign
    body = invite_body(
        roles=["class_teacher"],
        scopes=[
            {"type": "section", "ref": str(world.a.ids["section_9a"])},
            {"type": "section", "ref": str(world.a.ids["section_9c"])},
        ],
    )
    res = api.call(owner, "POST", USERS, json=body)
    assert res.status_code == 201, res.text
    user = res.json()
    assert user["status"] == "invited"
    assert user["roles"] == ["class_teacher"]
    assert {s["ref"] for s in user["scopes"]} == {
        str(world.a.ids["section_9a"]),
        str(world.a.ids["section_9c"]),
    }
    assert res.headers["Location"] == f"/api/v1/users/{user['id']}"
    assert res.headers["ETag"] == f'W/"{user["version"]}"'
    mid = uuid.UUID(user["membership_id"])
    actions = events_for(admin_engine, world.a.tenant_id, mid)
    assert actions.count("membership.created") == 1
    assert actions.count("membership.role_granted") == 1
    assert actions.count("membership.scope_added") == 2
    assert events_for(admin_engine, world.a.tenant_id, uuid.UUID(user["id"])) == ["user.invited"]

    # Activate, then the new teacher sees only 9A and 9C.
    patched = api.call(
        owner,
        "PATCH",
        f"{USERS}/{user['id']}",
        json={"status": "active"},
        headers={"If-Match": res.headers["ETag"]},
    )
    assert patched.status_code == 200, patched.text
    teacher = W.Person("class_teacher", uuid.UUID(user["id"]), mid, body["idp_subject"], "x")
    sections = api.call(teacher, "GET", "/api/v1/sections").json()["data"]
    assert {s["id"] for s in sections} == {
        str(world.a.ids["section_9a"]),
        str(world.a.ids["section_9c"]),
    }


def test_FR_IAM_010_auditor_invite_expires_in_14_days(world: Any, api: Any) -> None:
    res = api.call(
        world.person("owner"), "POST", USERS, json=invite_body(roles=["auditor_readonly"])
    )
    assert res.status_code == 201, res.text
    expires = dt.datetime.fromisoformat(res.json()["expires_at"])
    delta = expires - dt.datetime.now(dt.UTC)
    assert dt.timedelta(days=13, hours=23) < delta <= dt.timedelta(days=14)


def test_FR_IAM_002_privileged_invite_sets_mfa_required(
    world: Any, api: Any, admin_engine: Engine
) -> None:
    res = api.call(world.person("owner"), "POST", USERS, json=invite_body(roles=["office_admin"]))
    assert res.status_code == 201
    with admin_engine.connect() as c:
        flag: bool = c.execute(
            text("SELECT mfa_required FROM core.memberships WHERE id = :m"),
            {"m": res.json()["membership_id"]},
        ).scalar_one()
    assert flag is True


def test_US_102_office_admin_cannot_grant_owner(world: Any, api: Any, admin_engine: Engine) -> None:
    before = len(W.audit_events(admin_engine, world.a.tenant_id))
    res = api.call(world.person("office_admin"), "POST", USERS, json=invite_body(roles=["owner"]))
    assert res.status_code == 403
    assert res.json()["code"] == "role_not_grantable"
    assert len(W.audit_events(admin_engine, world.a.tenant_id)) == before


def test_US_102_unknown_role_is_422(world: Any, api: Any) -> None:
    res = api.call(world.person("owner"), "POST", USERS, json=invite_body(roles=["janitor"]))
    assert res.status_code == 422


def test_US_102_cross_tenant_scope_ref_is_422(world: Any, api: Any, admin_engine: Engine) -> None:
    before = len(W.audit_events(admin_engine, world.a.tenant_id))
    body = invite_body(scopes=[{"type": "section", "ref": str(world.b.ids["section_9a"])}])
    res = api.call(world.person("owner"), "POST", USERS, json=body)
    assert res.status_code == 422
    assert len(W.audit_events(admin_engine, world.a.tenant_id)) == before, "rolled back"


def test_US_102_duplicate_invite_is_409(world: Any, api: Any) -> None:
    body = invite_body()
    assert api.call(world.person("owner"), "POST", USERS, json=body).status_code == 201
    again = api.call(world.person("owner"), "POST", USERS, json=body)
    assert again.status_code == 409


def test_US_102_invite_rejects_unknown_fields(world: Any, api: Any) -> None:
    res = api.call(world.person("owner"), "POST", USERS, json=invite_body(is_admin=True))
    assert res.status_code == 422


def test_SEC_005_invite_needs_step_up(world: Any, api: Any) -> None:
    res = api.call(world.person("owner"), "POST", USERS, json=invite_body(), auth_age_s=600)
    assert res.status_code == 428
    assert res.json()["code"] == "step_up_required"


def test_docs_09_idempotent_invite_replays(world: Any, api: Any, admin_engine: Engine) -> None:
    body = invite_body()
    key = {"Idempotency-Key": f"invite-{uuid.uuid4().hex}"}
    first = api.call(world.person("owner"), "POST", USERS, json=body, headers=key)
    second = api.call(world.person("owner"), "POST", USERS, json=body, headers=key)
    assert first.status_code == second.status_code == 201
    assert first.json() == second.json()
    assert second.headers["Location"] == first.headers["Location"]
    assert second.headers.get("Idempotent-Replayed") == "true"
    with admin_engine.connect() as c:
        count: int = c.execute(
            text("SELECT count(*) FROM core.users WHERE idp_subject = :s"),
            {"s": body["idp_subject"]},
        ).scalar_one()
    assert count == 1
    other = api.call(world.person("owner"), "POST", USERS, json=invite_body(), headers=key)
    assert other.status_code == 422
    assert other.json()["code"] == "idempotency_key_reused"


def test_US_102_list_and_get_users(world: Any, api: Any) -> None:
    owner = world.person("owner")
    page = api.call(owner, "GET", USERS, params={"limit": 3})
    assert page.status_code == 200
    first = page.json()
    assert len(first["data"]) == 3
    assert first["next_cursor"]
    rest = api.call(owner, "GET", USERS, params={"limit": 200, "cursor": first["next_cursor"]})
    ids = [u["id"] for u in first["data"]] + [u["id"] for u in rest.json()["data"]]
    assert len(ids) == len(set(ids))
    assert str(world.person("teacher").user_id) in ids
    one = api.call(owner, "GET", f"{USERS}/{world.person('teacher').user_id}")
    assert one.status_code == 200
    assert one.json()["roles"] == ["teacher"]
    assert one.headers["ETag"].startswith('W/"')
    assert api.call(owner, "GET", USERS, params={"cursor": "!!"}).status_code == 422
    assert api.call(owner, "GET", USERS, params={"limit": 201}).status_code == 422


def test_FR_IAM_014_status_change_is_audited_and_immediate(
    world: Any, api: Any, admin_engine: Engine
) -> None:
    owner = world.person("owner")
    member = W.add_member(admin_engine, world.a.tenant_id, ["office_staff"])
    assert api.call(member, "GET", "/api/v1/me").status_code == 200
    etag = api.call(owner, "GET", f"{USERS}/{member.user_id}").headers["ETag"]
    res = api.call(
        owner,
        "PATCH",
        f"{USERS}/{member.user_id}",
        json={"status": "suspended"},
        headers={"If-Match": etag},
    )
    assert res.status_code == 200, res.text
    assert res.json()["status"] == "suspended"
    assert api.call(member, "GET", "/api/v1/me").status_code == 403
    events = [
        e
        for e in W.audit_events(admin_engine, world.a.tenant_id, "membership.status_changed")
        if e["resource_id"] == member.membership_id
    ]
    assert len(events) == 1
    assert events[0]["summary"]["from"] == "active"
    assert events[0]["summary"]["to"] == "suspended"
    stale = api.call(
        owner,
        "PATCH",
        f"{USERS}/{member.user_id}",
        json={"status": "active"},
        headers={"If-Match": etag},
    )
    assert stale.status_code == 412
    missing = api.call(owner, "PATCH", f"{USERS}/{member.user_id}", json={"status": "active"})
    assert missing.status_code == 400
    assert missing.json()["code"] == "if_match_required"


def test_US_102_last_owner_cannot_be_suspended_or_demoted(api: Any, admin_engine: Engine) -> None:
    tid = W.provision_school()
    owner = W.add_member(admin_engine, tid, ["owner"])
    etag = api.call(owner, "GET", f"{USERS}/{owner.user_id}").headers["ETag"]
    res = api.call(
        owner,
        "PATCH",
        f"{USERS}/{owner.user_id}",
        json={"status": "suspended"},
        headers={"If-Match": etag},
    )
    assert res.status_code == 409
    assert res.json()["code"] == "last_owner"
    res = api.call(owner, "PUT", f"{USERS}/{owner.user_id}/roles", json={"roles": ["principal"]})
    assert res.status_code == 409
    second = W.add_member(admin_engine, tid, ["owner"])
    res = api.call(owner, "PUT", f"{USERS}/{second.user_id}/roles", json={"roles": ["principal"]})
    assert res.status_code == 200, res.text


def test_FR_IAM_014_role_change_is_effective_on_next_request(
    world: Any, api: Any, admin_engine: Engine
) -> None:
    owner = world.person("owner")
    member = W.add_member(admin_engine, world.a.tenant_id, ["office_staff"])
    assert api.call(member, "GET", "/api/v1/audit/events").status_code == 403  # cached now
    res = api.call(
        owner,
        "PUT",
        f"{USERS}/{member.user_id}/roles",
        json={"roles": ["office_staff", "office_admin"]},
    )
    assert res.status_code == 200, res.text
    assert res.json()["roles"] == ["office_admin", "office_staff"]
    assert api.call(member, "GET", "/api/v1/audit/events").status_code == 200
    granted = [
        e
        for e in W.audit_events(admin_engine, world.a.tenant_id, "membership.role_granted")
        if e["resource_id"] == member.membership_id
    ]
    assert [e["summary"]["role_key"] for e in granted] == ["office_admin"]
    res = api.call(
        owner, "PUT", f"{USERS}/{member.user_id}/roles", json={"roles": ["office_staff"]}
    )
    assert res.status_code == 200
    assert api.call(member, "GET", "/api/v1/audit/events").status_code == 403
    revoked = W.audit_events(admin_engine, world.a.tenant_id, "membership.role_revoked")
    assert [e for e in revoked if e["resource_id"] == member.membership_id]


def test_FR_IAM_014_out_of_band_change_is_visible_within_60_s(
    world: Any, api: Any, admin_engine: Engine
) -> None:
    """Without explicit invalidation the 60 s snapshot TTL bounds staleness."""
    store = kv_store()
    clock = [1000.0]
    store.clock = lambda: clock[0]  # type: ignore[attr-defined]
    member = W.add_member(admin_engine, world.a.tenant_id, ["office_staff"])
    assert api.call(member, "GET", "/api/v1/audit/events").status_code == 403
    with admin_engine.begin() as c:
        c.execute(
            text(
                "INSERT INTO core.membership_roles (tenant_id, membership_id, role_id) "
                "SELECT :t, :m, id FROM core.roles "
                "WHERE tenant_id = :t AND key = 'auditor_readonly'"
            ),
            {"t": world.a.tenant_id, "m": member.membership_id},
        )
    assert api.call(member, "GET", "/api/v1/audit/events").status_code == 403  # still cached
    clock[0] += 61
    assert api.call(member, "GET", "/api/v1/audit/events").status_code == 200


def test_FR_IAM_012_scopes_replace_and_audit(world: Any, api: Any, admin_engine: Engine) -> None:
    owner = world.person("owner")
    member = W.add_member(
        admin_engine,
        world.a.tenant_id,
        ["class_teacher"],
        scopes=[("section", world.a.ids["section_9a"])],
    )
    visible = api.call(member, "GET", "/api/v1/sections").json()["data"]
    assert [s["id"] for s in visible] == [str(world.a.ids["section_9a"])]
    res = api.call(
        owner,
        "PUT",
        f"{USERS}/{member.user_id}/scopes",
        json={"scopes": [{"type": "class", "ref": str(world.a.ids["class_x"])}]},
    )
    assert res.status_code == 200, res.text
    visible = api.call(member, "GET", "/api/v1/sections").json()["data"]
    assert [s["id"] for s in visible] == [str(world.a.ids["section_10a"])]
    actions = events_for(admin_engine, world.a.tenant_id, member.membership_id)
    assert actions.count("membership.scope_removed") == 1
    assert actions.count("membership.scope_added") == 1
    bad = api.call(
        owner,
        "PUT",
        f"{USERS}/{member.user_id}/scopes",
        json={"scopes": [{"type": "school", "ref": str(world.a.ids["class_x"])}]},
    )
    assert bad.status_code == 422


def test_US_102_AC2_without_role_assign_roles_api_is_403(world: Any, api: Any) -> None:
    target = world.a.people["target"]
    res = api.call(
        world.person("office_admin"),
        "PUT",
        f"{USERS}/{target.user_id}/roles",
        json={"roles": ["teacher"]},
    )
    assert res.status_code == 403


def test_US_102_roles_and_permissions_catalog(world: Any, api: Any) -> None:
    owner = world.person("owner")
    roles = api.call(owner, "GET", "/api/v1/roles", params={"limit": 4})
    assert roles.status_code == 200
    got = roles.json()["data"]
    nxt = api.call(owner, "GET", "/api/v1/roles", params={"cursor": roles.json()["next_cursor"]})
    keys = [r["key"] for r in got + nxt.json()["data"]]
    assert sorted(keys) == sorted(W.ROLES)
    by_key = {r["key"]: r for r in got + nxt.json()["data"]}
    assert by_key["class_teacher"]["name_te"] == "తరగతి ఉపాధ్యాయులు"
    assert by_key["owner"]["is_system"] is True
    perms = api.call(owner, "GET", "/api/v1/permissions").json()["data"]
    perm_keys = {p["key"] for p in perms}
    assert "user.manage" in perm_keys
    assert not any(k.startswith("platform.") for k in perm_keys)
    assert "session.authenticated" not in perm_keys


def test_US_102_role_assign_guard_limits_principal(world: Any, api: Any) -> None:
    """role.assign cannot hand out a role with permissions the assigner lacks (owner excepted)."""
    target = world.a.people["target"]
    principal = world.person("principal")
    url = f"{USERS}/{target.user_id}/roles"
    res = api.call(principal, "PUT", url, json={"roles": ["teacher", "owner"]})
    assert res.status_code == 403
    assert res.json()["code"] == "role_not_grantable"
    res = api.call(principal, "PUT", url, json={"roles": ["teacher", "office_admin"]})
    assert res.status_code == 200, res.text
    res = api.call(world.person("owner"), "PUT", url, json={"roles": ["teacher"]})
    assert res.status_code == 200
    assert res.json()["roles"] == ["teacher"]


def test_US_102_office_admin_cannot_invite_privileged_roles(world: Any, api: Any) -> None:
    for role in ("principal", "office_admin"):
        res = api.call(world.person("office_admin"), "POST", USERS, json=invite_body(roles=[role]))
        assert res.status_code == 403, role
