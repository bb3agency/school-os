"""Staff profile edits, role hints, the staff directory and session settings (US-102,
FR-IAM-010..014, FR-TEN-010, FR-TEN-012, invariants 2, 5 and 7). Synthetic data only."""

from __future__ import annotations

import sys
import uuid
from typing import Any

import pytest
from sqlalchemy import Engine, text

from app.authz.catalog import system_roles

pytestmark = pytest.mark.db
W = sys.modules["sos_test_api_world"]
USERS = "/api/v1/users"
NAME = "Pothuraju Synthetica Renamed"
EMAIL = "renamed.synthetica@example.test"


def _etag(api: Any, who: Any, user_id: uuid.UUID) -> str:
    res = api.call(who, "GET", f"{USERS}/{user_id}")
    assert res.status_code == 200, res.text
    value: str = res.headers["ETag"]
    return value


def _patch(api: Any, who: Any, user_id: uuid.UUID, body: dict[str, Any], etag: str) -> Any:
    return api.call(who, "PATCH", f"{USERS}/{user_id}", json=body, headers={"If-Match": etag})


# --- PATCH /users/{id}: profile fields ----------------------------------------------------------


def test_US_102_patch_profile_fields_audits_names_only(
    world: Any, api: Any, admin_engine: Engine, capsys: pytest.CaptureFixture[str]
) -> None:
    admin = world.person("office_admin")
    member = W.add_member(admin_engine, world.a.tenant_id, ["teacher"], email="old@example.test")
    etag = _etag(api, admin, member.user_id)
    capsys.readouterr()
    res = _patch(
        api,
        admin,
        member.user_id,
        {"display_name": f"  {NAME} ", "email": EMAIL, "preferred_language": "te"},
        etag,
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["display_name"] == NAME
    assert body["email"] == EMAIL
    assert body["preferred_language"] == "te"
    assert body["status"] == "active"
    assert res.headers["ETag"] != etag
    logs = "".join(capsys.readouterr())
    assert "http.request" in logs, "access log lines were captured"
    for secret in (NAME, "Pothuraju", EMAIL, "old@example.test"):
        assert secret not in logs, secret

    events = [
        e
        for e in W.audit_events(admin_engine, world.a.tenant_id, "user.profile_updated")
        if e["resource_id"] == member.user_id
    ]
    assert len(events) == 1
    assert events[0]["summary"] == {
        "membership_id": str(member.membership_id),
        "fields": ["display_name", "email", "preferred_language"],
    }
    assert events[0]["actor_id"] == admin.user_id
    assert not [
        e
        for e in W.audit_events(admin_engine, world.a.tenant_id, "membership.status_changed")
        if e["resource_id"] == member.membership_id
    ]

    # Same values again: nothing to change, no event, ETag unchanged.
    again = _patch(api, admin, member.user_id, {"email": EMAIL.upper()}, res.headers["ETag"])
    assert again.status_code == 200
    assert again.headers["ETag"] == res.headers["ETag"]
    # Clearing the email is allowed.
    cleared = _patch(api, admin, member.user_id, {"email": None}, res.headers["ETag"])
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["email"] is None
    assert (
        len(
            [
                e
                for e in W.audit_events(admin_engine, world.a.tenant_id, "user.profile_updated")
                if e["resource_id"] == member.user_id
            ]
        )
        == 2
    )


def test_US_102_patch_status_and_profile_together(
    world: Any, api: Any, admin_engine: Engine
) -> None:
    owner = world.person("owner")
    member = W.add_member(admin_engine, world.a.tenant_id, ["office_staff"])
    res = _patch(
        api,
        owner,
        member.user_id,
        {"status": "suspended", "preferred_language": "te"},
        _etag(api, owner, member.user_id),
    )
    assert res.status_code == 200, res.text
    assert res.json()["status"] == "suspended"
    assert res.json()["preferred_language"] == "te"
    assert res.json()["version"] == W.version_of(
        admin_engine, "core.memberships", member.membership_id
    )
    actions = [
        e["action"]
        for e in W.audit_events(admin_engine, world.a.tenant_id)
        if e["resource_id"] in (member.user_id, member.membership_id)
    ]
    assert actions == ["user.profile_updated", "membership.status_changed"]
    assert api.call(member, "GET", "/api/v1/me").status_code == 403


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"display_name": None},
        {"preferred_language": None},
        {"status": None},
        {"display_name": ""},
        {"email": "not-an-email"},
        {"preferred_language": "hi"},
        {"idp_subject": "sub-other"},
        {"roles": ["owner"]},
    ],
)
def test_US_102_patch_rejects_bad_bodies(
    world: Any, api: Any, admin_engine: Engine, body: dict[str, Any]
) -> None:
    owner = world.person("owner")
    member = W.add_member(admin_engine, world.a.tenant_id, ["teacher"])
    before = len(W.audit_events(admin_engine, world.a.tenant_id))
    res = _patch(api, owner, member.user_id, body, _etag(api, owner, member.user_id))
    assert res.status_code == 422, res.text
    assert len(W.audit_events(admin_engine, world.a.tenant_id)) == before


def test_US_102_removed_member_profile_is_409_and_stale_is_412(
    world: Any, api: Any, admin_engine: Engine
) -> None:
    owner = world.person("owner")
    member = W.add_member(admin_engine, world.a.tenant_id, ["teacher"], status="removed")
    etag = _etag(api, owner, member.user_id)
    res = _patch(api, owner, member.user_id, {"display_name": NAME}, etag)
    assert res.status_code == 409
    assert res.json()["code"] == "invalid_state"
    active = W.add_member(admin_engine, world.a.tenant_id, ["teacher"])
    stale = _patch(api, owner, active.user_id, {"display_name": NAME}, 'W/"99"')
    assert stale.status_code == 412
    missing = api.call(owner, "PATCH", f"{USERS}/{active.user_id}", json={"display_name": NAME})
    assert missing.status_code == 400
    assert missing.json()["code"] == "if_match_required"


def test_US_102_AC2_profile_patch_needs_user_manage_and_step_up(
    world: Any, api: Any, admin_engine: Engine
) -> None:
    member = W.add_member(admin_engine, world.a.tenant_id, ["teacher"])
    etag = _etag(api, world.person("owner"), member.user_id)
    for role in ("office_staff", "class_teacher", "accountant"):
        res = _patch(api, world.person(role), member.user_id, {"display_name": NAME}, etag)
        assert res.status_code == 403, role
    stale_auth = api.call(
        world.person("owner"),
        "PATCH",
        f"{USERS}/{member.user_id}",
        json={"display_name": NAME},
        headers={"If-Match": etag},
        auth_age_s=600,
    )
    assert stale_auth.status_code == 428
    other = _patch(api, world.b.people["owner"], member.user_id, {"display_name": NAME}, etag)
    assert other.status_code == 404
    assert (
        api.call(world.person("owner"), "GET", f"{USERS}/{member.user_id}").json()["display_name"]
        == member.display_name
    )


# --- roles: grantable / scoped / empty list ------------------------------------------------------


def _roles(api: Any, who: Any) -> dict[str, dict[str, Any]]:
    res = api.call(who, "GET", "/api/v1/roles", params={"limit": 200})
    assert res.status_code == 200, res.text
    return {r["key"]: r for r in res.json()["data"]}


def test_FR_IAM_011_roles_report_scoped_from_roles_yaml(world: Any, api: Any) -> None:
    roles = _roles(api, world.person("owner"))
    for key, template in system_roles().items():
        assert roles[key]["scoped"] is any(g.scoped for g in template.grants), key
    assert roles["class_teacher"]["scoped"] is True
    assert roles["principal"]["scoped"] is False


def test_US_102_roles_grantable_for_owner_is_everything(world: Any, api: Any) -> None:
    roles = _roles(api, world.person("owner"))
    assert all(r["grantable"] for r in roles.values())
    assert "platform_support" not in roles


@pytest.mark.parametrize("inviter", ["office_admin", "principal"])
def test_US_102_grantable_matches_what_the_server_accepts(
    world: Any, api: Any, inviter: str
) -> None:
    """``grantable`` is exactly the server rule: the invite succeeds for every grantable role
    and is refused (403 role_not_grantable) for every other one."""
    who = world.person(inviter)
    roles = _roles(api, who)
    for key, role in sorted(roles.items()):
        body = {
            "idp_subject": f"sub-{uuid.uuid4().hex}",
            "display_name": "Synthetic Grantable Probe",
            "roles": [key],
        }
        res = api.call(who, "POST", USERS, json=body)
        if role["grantable"]:
            assert res.status_code == 201, (inviter, key, res.text)
        else:
            assert res.status_code == 403, (inviter, key, res.text)
            assert res.json()["code"] == "role_not_grantable"
    if inviter == "office_admin":
        assert roles["teacher"]["grantable"] is True
        assert roles["principal"]["grantable"] is False
        assert roles["owner"]["grantable"] is False
    else:
        assert roles["owner"]["grantable"] is False
        assert roles["office_admin"]["grantable"] is True


def test_US_102_principal_role_changes_follow_grantable(
    world: Any, api: Any, admin_engine: Engine
) -> None:
    principal = world.person("principal")
    roles = _roles(api, principal)
    member = W.add_member(admin_engine, world.a.tenant_id, ["teacher"])
    for key, role in sorted(roles.items()):
        if key == "teacher":
            continue
        res = api.call(
            principal, "PUT", f"{USERS}/{member.user_id}/roles", json={"roles": ["teacher", key]}
        )
        expected = 200 if role["grantable"] else 403
        assert res.status_code == expected, (key, res.text)
        if expected == 200:
            back = api.call(
                principal, "PUT", f"{USERS}/{member.user_id}/roles", json={"roles": ["teacher"]}
            )
            assert back.status_code == 200, back.text


def test_US_102_empty_role_list_is_422_roles_required(
    world: Any, api: Any, admin_engine: Engine
) -> None:
    member = W.add_member(admin_engine, world.a.tenant_id, ["teacher"])
    before = len(W.audit_events(admin_engine, world.a.tenant_id))
    res = api.call(
        world.person("owner"), "PUT", f"{USERS}/{member.user_id}/roles", json={"roles": []}
    )
    assert res.status_code == 422
    assert res.json()["code"] == "roles_required"
    assert res.json()["errors"][0]["field"] == "roles"
    assert len(W.audit_events(admin_engine, world.a.tenant_id)) == before
    user = api.call(world.person("owner"), "GET", f"{USERS}/{member.user_id}").json()
    assert user["roles"] == ["teacher"]


# --- staff directory -----------------------------------------------------------------------------


def test_FR_TEN_010_staff_directory_for_structure_managers(
    world: Any, api: Any, admin_engine: Engine
) -> None:
    suspended = W.add_member(admin_engine, world.a.tenant_id, ["teacher"], status="suspended")
    removed = W.add_member(admin_engine, world.a.tenant_id, ["teacher"], status="removed")
    invited = W.add_member(
        admin_engine, world.a.tenant_id, ["class_teacher"], status="invited", email="i@example.test"
    )
    for role in ("owner", "principal", "office_admin"):
        res = api.call(world.person(role), "GET", "/api/v1/staff", auth_age_s=3600)
        assert res.status_code == 200, (role, res.text)
        data = res.json()["data"]
        assert all(set(m) == {"membership_id", "display_name", "roles"} for m in data)
        by_id = {m["membership_id"]: m for m in data}
        teacher = world.person("teacher")
        assert by_id[str(teacher.membership_id)] == {
            "membership_id": str(teacher.membership_id),
            "display_name": teacher.display_name,
            "roles": ["teacher"],
        }
        assert str(invited.membership_id) in by_id
        assert str(suspended.membership_id) not in by_id
        assert str(removed.membership_id) not in by_id
        b_ids = {str(p.membership_id) for p in world.b.people.values()}
        assert not set(by_id) & b_ids
        assert "@" not in res.text


@pytest.mark.parametrize(
    "role", ["office_staff", "accountant", "exam_coordinator", "class_teacher", "teacher"]
)
def test_FR_TEN_010_staff_directory_is_403_without_permission(
    world: Any, api: Any, role: str
) -> None:
    assert api.call(world.person(role), "GET", "/api/v1/staff").status_code == 403


def test_FR_TEN_010_staff_directory_pages(world: Any, api: Any) -> None:
    owner = world.person("owner")
    first = api.call(owner, "GET", "/api/v1/staff", params={"limit": 2}).json()
    assert len(first["data"]) == 2
    assert first["next_cursor"]
    rest = api.call(
        owner, "GET", "/api/v1/staff", params={"limit": 200, "cursor": first["next_cursor"]}
    ).json()
    ids = [m["membership_id"] for m in first["data"] + rest["data"]]
    assert len(ids) == len(set(ids))


def test_FR_TEN_010_scoped_structure_grant_cannot_read_directory(
    api: Any, admin_engine: Engine
) -> None:
    """A custom role granting tenant.structure.manage is scoped (not in roles.yaml), so the
    directory refuses it: 403, not a partial list. Uses its own school (custom role)."""
    school = W.School(W.provision_school())
    school.people["owner"] = W.add_member(admin_engine, school.tenant_id, ["owner"])
    W.build_structure(school, school.people["owner"])
    with admin_engine.begin() as c:
        role_id = uuid.uuid4()
        c.execute(
            text(
                "INSERT INTO core.roles (id, tenant_id, key, name_en, name_te, is_system) "
                "VALUES (:r, :t, 'timetable', 'Timetable', 'టైమ్‌టేబుల్', false)"
            ),
            {"r": role_id, "t": school.tenant_id},
        )
        c.execute(
            text(
                "INSERT INTO core.role_permissions (tenant_id, role_id, permission_key) "
                "VALUES (:t, :r, 'tenant.structure.manage')"
            ),
            {"t": school.tenant_id, "r": role_id},
        )
    member = W.add_member(
        admin_engine,
        school.tenant_id,
        ["timetable"],
        scopes=[("class", school.ids["class_x"])],
    )
    assert api.call(member, "GET", "/api/v1/staff").status_code == 403
    roles = api.call(school.people["owner"], "GET", "/api/v1/roles", params={"limit": 200})
    custom = next(r for r in roles.json()["data"] if r["key"] == "timetable")
    assert custom["scoped"] is True
    assert custom["grantable"] is True, "the owner may give every role"


# --- session settings in /me (FR-TEN-012) --------------------------------------------------------


def test_FR_TEN_012_me_carries_session_settings(api: Any, admin_engine: Engine) -> None:
    tid = W.provision_school()
    owner = W.add_member(admin_engine, tid, ["owner"])
    staff = W.add_member(admin_engine, tid, ["office_staff"])
    me = api.call(staff, "GET", "/api/v1/me").json()
    assert me["settings"] == {
        "idle_timeout_minutes": 15,
        "date_format": "DD/MM/YYYY",
        "languages": ["en", "te"],
    }
    etag = api.call(owner, "GET", "/api/v1/tenant").headers["ETag"]
    res = api.call(
        owner,
        "PATCH",
        "/api/v1/tenant",
        json={"idle_timeout_minutes": 7, "date_format": "YYYY-MM-DD", "languages": ["te"]},
        headers={"If-Match": etag},
    )
    assert res.status_code == 200, res.text
    me = api.call(staff, "GET", "/api/v1/me").json()
    assert me["settings"] == {
        "idle_timeout_minutes": 7,
        "date_format": "YYYY-MM-DD",
        "languages": ["te"],
    }
    chosen = api.call(staff, "POST", "/api/v1/me/active-tenant", json={"tenant_id": str(tid)})
    assert chosen.status_code == 200, chosen.text
    assert chosen.json()["settings"]["idle_timeout_minutes"] == 7
