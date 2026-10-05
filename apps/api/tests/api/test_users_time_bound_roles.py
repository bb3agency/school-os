"""A time-bound role keeps its window when it is given later (docs/07 §6.2 "auditor_readonly
memberships are time-bound", FR-IAM-010, SEC-003; audit 2026-10-05 A-02). Synthetic data only.

An invitation with ``auditor_readonly`` expires after the role's TTL (14 days). Before this rule
``PUT /users/{id}/roles`` could add the same role to an existing, open-ended membership, and the
external auditor's access then never expired.
"""

from __future__ import annotations

import datetime as dt
import sys
import uuid
from typing import Any

import pytest
from sqlalchemy import Engine

pytestmark = pytest.mark.db
W = sys.modules["sos_test_api_world"]
USERS = "/api/v1/users"


def _expires(res: Any) -> dt.datetime | None:
    raw = res.json()["expires_at"]
    return None if raw is None else dt.datetime.fromisoformat(raw)


def test_SEC_003_adding_a_time_bound_role_starts_its_window(
    world: Any, api: Any, admin_engine: Engine
) -> None:
    owner = world.person("owner")
    member = W.add_member(admin_engine, world.a.tenant_id, ["teacher"])
    before = api.call(owner, "GET", f"{USERS}/{member.user_id}")
    assert _expires(before) is None

    res = api.call(
        owner,
        "PUT",
        f"{USERS}/{member.user_id}/roles",
        json={"roles": ["auditor_readonly"]},
    )
    assert res.status_code == 200, res.text
    expires = _expires(res)
    assert expires is not None, "auditor_readonly must not become an open-ended membership"
    delta = expires - dt.datetime.now(dt.UTC)
    assert dt.timedelta(days=13, hours=23) < delta <= dt.timedelta(days=14)
    changed = [
        e
        for e in W.audit_events(admin_engine, world.a.tenant_id, "membership.expiry_set")
        if e["resource_id"] == member.membership_id
    ]
    assert len(changed) == 1


def test_SEC_003_an_earlier_expiry_is_kept_and_other_roles_change_nothing(
    world: Any, api: Any, admin_engine: Engine
) -> None:
    owner = world.person("owner")
    invited = api.call(
        owner,
        "POST",
        USERS,
        json={
            "idp_subject": f"sub-{uuid.uuid4().hex}",
            "display_name": "Synthetic Auditor",
            "email": "auditor.synthetic@example.test",
            "preferred_language": "en",
            "roles": ["auditor_readonly"],
            "scopes": [],
        },
    )
    assert invited.status_code == 201, invited.text
    first = _expires(invited)
    assert first is not None
    user_id = invited.json()["id"]

    # Adding a role without a window keeps the auditor's expiry; it is never extended.
    res = api.call(
        owner, "PUT", f"{USERS}/{user_id}/roles", json={"roles": ["auditor_readonly", "teacher"]}
    )
    assert res.status_code == 200, res.text
    assert _expires(res) == first

    # An open-ended member given a role without a window stays open-ended.
    member = W.add_member(admin_engine, world.a.tenant_id, ["teacher"])
    res = api.call(
        owner, "PUT", f"{USERS}/{member.user_id}/roles", json={"roles": ["teacher", "accountant"]}
    )
    assert res.status_code == 200, res.text
    assert _expires(res) is None
