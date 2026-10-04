"""PATCH /users/{id} ``status`` on a member with more access than the caller (US-102 AC2,
FR-IAM-014, SEC-003; invariant 7). Synthetic data only.

Suspending, removing or reactivating someone takes away or gives back every role they hold, so it
follows the invite rule (``_guard_invite_roles``): changing the status of a member who holds a
privileged role (owner, principal, office admin) or a custom role needs ``role.assign`` and only
reaches roles whose permissions the caller holds; the owner role (``assign_any_role``) reaches
everyone. Before this rule an office admin (``user.manage`` without ``role.assign``) could
suspend the principal and the owners, or reactivate a principal the owner had suspended.
"""

from __future__ import annotations

import sys
import uuid
from typing import Any

import pytest
from sqlalchemy import Engine

pytestmark = pytest.mark.db
W = sys.modules["sos_test_api_world"]
USERS = "/api/v1/users"


def _status(api: Any, who: Any, user_id: uuid.UUID, status: str) -> Any:
    etag = api.call(who, "GET", f"{USERS}/{user_id}").headers["ETag"]
    return api.call(
        who, "PATCH", f"{USERS}/{user_id}", json={"status": status}, headers={"If-Match": etag}
    )


def _actions(admin: Engine, tenant: uuid.UUID, membership_id: uuid.UUID) -> list[str]:
    return [
        e["action"]
        for e in W.audit_events(admin, tenant, "membership.status_changed")
        if e["resource_id"] == membership_id
    ]


def test_SEC_003_office_admin_cannot_suspend_or_reactivate_a_principal(
    api: Any, admin_engine: Engine
) -> None:
    tid = W.provision_school()
    owner = W.add_member(admin_engine, tid, ["owner"])
    office_admin = W.add_member(admin_engine, tid, ["office_admin"])
    principal = W.add_member(admin_engine, tid, ["principal"])

    res = _status(api, office_admin, principal.user_id, "suspended")
    assert res.status_code == 403, res.text
    assert res.json()["code"] == "role_not_grantable"
    assert _actions(admin_engine, tid, principal.membership_id) == []

    # The owner suspends the principal; the office admin cannot bring them back.
    assert _status(api, owner, principal.user_id, "suspended").status_code == 200
    res = _status(api, office_admin, principal.user_id, "active")
    assert res.status_code == 403, res.text
    assert res.json()["code"] == "role_not_grantable"
    res = _status(api, office_admin, principal.user_id, "removed")
    assert res.status_code == 403, res.text
    assert len(_actions(admin_engine, tid, principal.membership_id)) == 1


def test_SEC_003_office_admin_cannot_suspend_an_owner_or_another_office_admin(
    api: Any, admin_engine: Engine
) -> None:
    tid = W.provision_school()
    W.add_member(admin_engine, tid, ["owner"])
    second_owner = W.add_member(admin_engine, tid, ["owner"])
    office_admin = W.add_member(admin_engine, tid, ["office_admin"])
    other_admin = W.add_member(admin_engine, tid, ["office_admin"])
    for target in (second_owner, other_admin):
        res = _status(api, office_admin, target.user_id, "suspended")
        assert res.status_code == 403, res.text
        assert res.json()["code"] == "role_not_grantable"


def test_SEC_003_status_changes_within_the_callers_reach_still_work(
    api: Any, admin_engine: Engine
) -> None:
    tid = W.provision_school()
    owner = W.add_member(admin_engine, tid, ["owner"])
    second_owner = W.add_member(admin_engine, tid, ["owner"])
    principal = W.add_member(admin_engine, tid, ["principal"])
    office_admin = W.add_member(admin_engine, tid, ["office_admin"])
    teacher = W.add_member(admin_engine, tid, ["class_teacher"])

    # user.manage alone still manages non-privileged staff (US-102 AC1).
    assert _status(api, office_admin, teacher.user_id, "suspended").status_code == 200
    assert _status(api, office_admin, teacher.user_id, "active").status_code == 200
    # role.assign reaches roles whose permissions the caller holds...
    assert _status(api, principal, office_admin.user_id, "suspended").status_code == 200
    # ...but not the owner role.
    res = _status(api, principal, second_owner.user_id, "suspended")
    assert res.status_code == 403
    assert res.json()["code"] == "role_not_grantable"
    # The owner reaches everyone.
    assert _status(api, owner, second_owner.user_id, "suspended").status_code == 200
    assert _status(api, owner, office_admin.user_id, "active").status_code == 200


def test_SEC_003_profile_edits_are_not_changed_by_the_status_rule(
    api: Any, admin_engine: Engine
) -> None:
    tid = W.provision_school()
    W.add_member(admin_engine, tid, ["owner"])
    office_admin = W.add_member(admin_engine, tid, ["office_admin"])
    teacher = W.add_member(admin_engine, tid, ["teacher"])
    etag = api.call(office_admin, "GET", f"{USERS}/{teacher.user_id}").headers["ETag"]
    res = api.call(
        office_admin,
        "PATCH",
        f"{USERS}/{teacher.user_id}",
        json={"display_name": "Synthetic Renamed"},
        headers={"If-Match": etag},
    )
    assert res.status_code == 200, res.text
