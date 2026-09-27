"""PATCH /users/{id}: no status change on your own membership (``own_account``) and profiles
shared across schools (``profile_shared``; ADR-0028, owner decision 2026-09-27). US-102,
FR-IAM-010, FR-IAM-014, invariants 1 and 7. Synthetic data only."""

from __future__ import annotations

import sys
import uuid
from typing import Any

import pytest
from sqlalchemy import Engine, text

pytestmark = pytest.mark.db
W = sys.modules["sos_test_api_world"]
USERS = "/api/v1/users"


def _etag(api: Any, who: Any, user_id: uuid.UUID) -> str:
    res = api.call(who, "GET", f"{USERS}/{user_id}")
    assert res.status_code == 200, res.text
    value: str = res.headers["ETag"]
    return value


def _patch(api: Any, who: Any, user_id: uuid.UUID, body: dict[str, Any]) -> Any:
    return api.call(
        who,
        "PATCH",
        f"{USERS}/{user_id}",
        json=body,
        headers={"If-Match": _etag(api, who, user_id)},
    )


def _events(admin: Engine, tenant: uuid.UUID, action: str, resource: uuid.UUID) -> list[Any]:
    return [e for e in W.audit_events(admin, tenant, action) if e["resource_id"] == resource]


def _membership_status(admin: Engine, membership_id: uuid.UUID) -> str:
    with admin.connect() as c:
        return str(
            c.execute(
                text("SELECT status FROM core.memberships WHERE id = :m"), {"m": membership_id}
            ).scalar_one()
        )


def _also_member_of(admin: Engine, tenant: uuid.UUID, user_id: uuid.UUID, status: str) -> None:
    """Give an existing person a membership in another school (out of band, test setup)."""
    with admin.begin() as c:
        c.execute(
            text(
                "INSERT INTO core.memberships (id, tenant_id, user_id, status) "
                "VALUES (gen_random_uuid(), :t, :u, :s)"
            ),
            {"t": tenant, "u": user_id, "s": status},
        )


def _profile(admin: Engine, user_id: uuid.UUID) -> tuple[str, str | None, str]:
    with admin.connect() as c:
        row = c.execute(
            text("SELECT display_name, email, preferred_language FROM core.users WHERE id = :u"),
            {"u": user_id},
        ).one()
    return (row.display_name, row.email, row.preferred_language)


# --- own_account ----------------------------------------------------------------------------


@pytest.mark.parametrize("status", ["suspended", "removed"])
def test_US_102_member_cannot_change_status_of_own_membership(
    status: str, api: Any, admin_engine: Engine
) -> None:
    tid = W.provision_school()
    me = W.add_member(admin_engine, tid, ["owner"])
    W.add_member(admin_engine, tid, ["owner"])  # not the last owner: only own_account applies
    res = _patch(api, me, me.user_id, {"status": status})
    assert res.status_code == 409, res.text
    assert res.json()["code"] == "own_account"
    assert _membership_status(admin_engine, me.membership_id) == "active"
    assert _events(admin_engine, tid, "membership.status_changed", me.membership_id) == []


def test_US_102_status_change_of_another_member_still_allowed(
    api: Any, admin_engine: Engine
) -> None:
    tid = W.provision_school()
    me = W.add_member(admin_engine, tid, ["owner"])
    other = W.add_member(admin_engine, tid, ["teacher"])
    res = _patch(api, me, other.user_id, {"status": "suspended"})
    assert res.status_code == 200, res.text
    assert res.json()["status"] == "suspended"
    events = _events(admin_engine, tid, "membership.status_changed", other.membership_id)
    assert [(e["summary"]["from"], e["summary"]["to"]) for e in events] == [("active", "suspended")]


def test_US_102_own_profile_and_unchanged_status_are_not_refused(
    api: Any, admin_engine: Engine
) -> None:
    tid = W.provision_school()
    me = W.add_member(admin_engine, tid, ["owner"])
    res = _patch(api, me, me.user_id, {"status": "active", "preferred_language": "te"})
    assert res.status_code == 200, res.text
    assert res.json()["preferred_language"] == "te"


def test_US_102_last_owner_guard_still_applies(api: Any, admin_engine: Engine) -> None:
    tid = W.provision_school()
    owner = W.add_member(admin_engine, tid, ["owner"])
    principal = W.add_member(admin_engine, tid, ["principal"])
    res = _patch(api, principal, owner.user_id, {"status": "suspended"})
    assert res.status_code == 409, res.text
    assert res.json()["code"] == "last_owner"


# --- profile_shared (ADR-0028) --------------------------------------------------------------


@pytest.mark.parametrize("other_status", ["active", "invited", "suspended", "removed"])
def test_ADR_0028_profile_of_person_in_two_schools_is_409_profile_shared(
    other_status: str, api: Any, admin_engine: Engine
) -> None:
    a, b = W.provision_school(), W.provision_school()
    admin = W.add_member(admin_engine, a, ["owner"])
    person = W.add_member(admin_engine, a, ["teacher"], email="shared@example.test")
    _also_member_of(admin_engine, b, person.user_id, other_status)
    before = _profile(admin_engine, person.user_id)
    for body in (
        {"display_name": "Synthetic Renamed Teacher"},
        {"email": "changed@example.test"},
        {"preferred_language": "te"},
        {"status": "suspended", "display_name": "Synthetic Renamed Teacher"},
    ):
        res = _patch(api, admin, person.user_id, body)
        assert res.status_code == 409, (body, res.text)
        assert res.json()["code"] == "profile_shared"
    assert _profile(admin_engine, person.user_id) == before
    assert _events(admin_engine, a, "user.profile_updated", person.user_id) == []
    assert _membership_status(admin_engine, person.membership_id) == "active"


def test_ADR_0028_status_change_of_shared_person_is_allowed(api: Any, admin_engine: Engine) -> None:
    a, b = W.provision_school(), W.provision_school()
    admin = W.add_member(admin_engine, a, ["owner"])
    person = W.add_member(admin_engine, a, ["teacher"])
    _also_member_of(admin_engine, b, person.user_id, "active")
    name = _profile(admin_engine, person.user_id)[0]
    # Unchanged profile values are ignored, so only the status changes.
    res = _patch(api, admin, person.user_id, {"status": "suspended", "display_name": name})
    assert res.status_code == 200, res.text
    assert res.json()["status"] == "suspended"
    events = _events(admin_engine, a, "membership.status_changed", person.membership_id)
    assert len(events) == 1


def test_ADR_0028_single_school_profile_is_editable(api: Any, admin_engine: Engine) -> None:
    tid = W.provision_school()
    admin = W.add_member(admin_engine, tid, ["owner"])
    person = W.add_member(admin_engine, tid, ["teacher"])
    res = _patch(api, admin, person.user_id, {"display_name": "Synthetic Single School"})
    assert res.status_code == 200, res.text
    assert _profile(admin_engine, person.user_id)[0] == "Synthetic Single School"
    assert len(_events(admin_engine, tid, "user.profile_updated", person.user_id)) == 1


def test_ADR_0028_other_school_cannot_reach_the_person(api: Any, admin_engine: Engine) -> None:
    a, b = W.provision_school(), W.provision_school()
    person = W.add_member(admin_engine, a, ["teacher"])
    admin_b = W.add_member(admin_engine, b, ["owner"])
    res = api.call(
        admin_b,
        "PATCH",
        f"{USERS}/{person.user_id}",
        json={"display_name": "Synthetic Intruder"},
        headers={"If-Match": 'W/"1"'},
    )
    assert res.status_code == 404
