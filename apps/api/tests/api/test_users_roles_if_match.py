"""PUT /users/{id}/roles and /scopes honour If-Match (audit 2026-10-05 A-17, 2026-10-06 R-03;
docs/09 §2, FR-IAM-014, SEC-003). Synthetic data only.

Both routes replace a whole set. Without a concurrency check, admin B saving a stale list put
back a role admin A had just revoked. The routes now take an optional ``If-Match`` (the web does
not send one yet, so it stays optional for backward compatibility): when sent it must equal the
membership version (412 otherwise), and every change of roles or scopes moves the version on, so
the ETag read before the change no longer matches.
"""

from __future__ import annotations

import sys
from typing import Any

import pytest
from sqlalchemy import Engine

pytestmark = pytest.mark.db
W = sys.modules["sos_test_api_world"]
USERS = "/api/v1/users"


def _etag(api: Any, who: Any, user_id: Any) -> str:
    res = api.call(who, "GET", f"{USERS}/{user_id}")
    assert res.status_code == 200, res.text
    return str(res.headers["ETag"])


def test_SEC_003_a_stale_role_list_does_not_bring_back_a_revoked_role(
    api: Any, admin_engine: Engine
) -> None:
    tid = W.provision_school()
    owner = W.add_member(admin_engine, tid, ["owner"])
    member = W.add_member(admin_engine, tid, ["office_staff", "accountant"])
    stale = _etag(api, owner, member.user_id)

    # Admin A revokes "accountant" with the current ETag.
    first = api.call(
        owner,
        "PUT",
        f"{USERS}/{member.user_id}/roles",
        json={"roles": ["office_staff"]},
        headers={"If-Match": stale},
    )
    assert first.status_code == 200, first.text
    assert first.headers["ETag"] != stale

    # Admin B saves the list they loaded before: refused, the revoked role stays revoked.
    second = api.call(
        owner,
        "PUT",
        f"{USERS}/{member.user_id}/roles",
        json={"roles": ["office_staff", "accountant"]},
        headers={"If-Match": stale},
    )
    assert second.status_code == 412, second.text
    assert api.call(owner, "GET", f"{USERS}/{member.user_id}").json()["roles"] == ["office_staff"]

    # Without If-Match the route still works as before (the web does not send it yet).
    plain = api.call(
        owner, "PUT", f"{USERS}/{member.user_id}/roles", json={"roles": ["office_staff"]}
    )
    assert plain.status_code == 200, plain.text


def test_SEC_003_a_stale_scope_list_is_refused(api: Any, admin_engine: Engine) -> None:
    tid = W.provision_school()
    owner = W.add_member(admin_engine, tid, ["owner"])
    member = W.add_member(admin_engine, tid, ["teacher"])
    stale = _etag(api, owner, member.user_id)
    ok = api.call(
        owner,
        "PUT",
        f"{USERS}/{member.user_id}/scopes",
        json={"scopes": [{"type": "school"}]},
        headers={"If-Match": stale},
    )
    assert ok.status_code == 200, ok.text
    assert ok.headers["ETag"] != stale
    again = api.call(
        owner,
        "PUT",
        f"{USERS}/{member.user_id}/scopes",
        json={"scopes": []},
        headers={"If-Match": stale},
    )
    assert again.status_code == 412, again.text
    bad = api.call(
        owner,
        "PUT",
        f"{USERS}/{member.user_id}/scopes",
        json={"scopes": []},
        headers={"If-Match": "nonsense"},
    )
    assert bad.status_code == 400, bad.text
