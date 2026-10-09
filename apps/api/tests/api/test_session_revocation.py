"""A member's next request fails once they are suspended, removed or lose a role (FR-IAM-014,
SEC-006; ASVS 3.3.4, 4.1.3; audit 2026-10-05 platform).

The BFF session and the IdP token outlive these changes (the API is stateless and access tokens
live up to 10 minutes), so the API must decide on every request. Membership status is read from
``core.resolve_login`` per request (never cached), and a role change drops the cached permission
snapshot when it commits, so a still-valid token loses the access at once, not after the 60 s
cache TTL. The permission cache here is the in-memory KV of the test client, primed by a first
request. Synthetic data only.
"""

from __future__ import annotations

import sys
from typing import Any

import pytest
from sqlalchemy import Engine

pytestmark = pytest.mark.db
W = sys.modules["sos_test_api_world"]
USERS = "/api/v1/users"


def _set_status(api: Any, who: Any, user_id: Any, status: str) -> None:
    etag = api.call(who, "GET", f"{USERS}/{user_id}").headers["ETag"]
    res = api.call(
        who, "PATCH", f"{USERS}/{user_id}", json={"status": status}, headers={"If-Match": etag}
    )
    assert res.status_code == 200, res.text


@pytest.mark.parametrize("status", ["suspended", "removed"])
def test_SEC_006_a_suspended_or_removed_member_is_refused_on_the_next_request(
    api: Any, admin_engine: Engine, status: str
) -> None:
    tid = W.provision_school()
    owner = W.add_member(admin_engine, tid, ["owner"])
    teacher = W.add_member(admin_engine, tid, ["teacher"])
    assert api.call(teacher, "GET", "/api/v1/me").status_code == 200  # primes the cache

    _set_status(api, owner, teacher.user_id, status)

    res = api.call(teacher, "GET", "/api/v1/me")
    assert res.status_code in (401, 403), res.text
    assert api.call(teacher, "GET", "/api/v1/me", tenant=tid).status_code in (401, 403)


def test_FR_IAM_014_a_removed_role_stops_working_on_the_next_request(
    api: Any, admin_engine: Engine
) -> None:
    tid = W.provision_school()
    owner = W.add_member(admin_engine, tid, ["owner"])
    office_admin = W.add_member(admin_engine, tid, ["office_admin"])
    # user.manage (office_admin): allowed, and the permission snapshot is now cached.
    assert api.call(office_admin, "GET", USERS).status_code == 200

    res = api.call(
        owner, "PUT", f"{USERS}/{office_admin.user_id}/roles", json={"roles": ["teacher"]}
    )
    assert res.status_code == 200, res.text

    res = api.call(office_admin, "GET", USERS)
    assert res.status_code == 403, res.text


def test_FR_IAM_014_a_reactivated_member_gets_access_back(api: Any, admin_engine: Engine) -> None:
    tid = W.provision_school()
    owner = W.add_member(admin_engine, tid, ["owner"])
    teacher = W.add_member(admin_engine, tid, ["teacher"])
    _set_status(api, owner, teacher.user_id, "suspended")
    assert api.call(teacher, "GET", "/api/v1/me").status_code in (401, 403)
    _set_status(api, owner, teacher.user_id, "active")
    assert api.call(teacher, "GET", "/api/v1/me").status_code == 200
