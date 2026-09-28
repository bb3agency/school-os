"""Fields the school console needs so it stops copying server rules (US-102,
FR-IAM-002, FR-IAM-010, FR-IAM-014, FR-TEN-010, ADR-0018, ADR-0028; invariants 1 and 2):

- ``RoleOut.needs_mfa`` (roles.yaml ``mfa_required``),
- ``UserOut.profile_shared`` (the person also belongs to another school),
- ``StaffMemberOut.status`` (active or invited).

Synthetic data only.
"""

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


# --- RoleOut.needs_mfa ----------------------------------------------------------------------


def test_FR_IAM_002_roles_report_needs_mfa_from_roles_yaml(world: Any, api: Any) -> None:
    res = api.call(world.person("owner"), "GET", "/api/v1/roles", params={"limit": 200})
    assert res.status_code == 200, res.text
    roles = {r["key"]: r for r in res.json()["data"]}
    for key, template in system_roles().items():
        if key in roles:
            assert roles[key]["needs_mfa"] is template.mfa_required, key
    assert roles["owner"]["needs_mfa"] is True
    assert roles["principal"]["needs_mfa"] is True
    assert roles["office_admin"]["needs_mfa"] is True
    assert roles["teacher"]["needs_mfa"] is False


# --- UserOut.profile_shared -----------------------------------------------------------------


def _also_member_of(admin: Engine, tenant: uuid.UUID, user_id: uuid.UUID) -> None:
    with admin.begin() as c:
        c.execute(
            text(
                "INSERT INTO core.memberships (id, tenant_id, user_id, status) "
                "VALUES (gen_random_uuid(), :t, :u, 'active')"
            ),
            {"t": tenant, "u": user_id},
        )


def test_ADR_0028_user_out_says_when_the_profile_is_shared(api: Any, admin_engine: Engine) -> None:
    a, b = W.provision_school(), W.provision_school()
    admin = W.add_member(admin_engine, a, ["owner"])
    single = W.add_member(admin_engine, a, ["teacher"])
    shared = W.add_member(admin_engine, a, ["teacher"])
    _also_member_of(admin_engine, b, shared.user_id)

    one = api.call(admin, "GET", f"{USERS}/{shared.user_id}")
    assert one.status_code == 200, one.text
    assert one.json()["profile_shared"] is True
    assert api.call(admin, "GET", f"{USERS}/{single.user_id}").json()["profile_shared"] is False

    listed = api.call(admin, "GET", USERS, params={"limit": 200})
    assert listed.status_code == 200, listed.text
    by_id = {u["id"]: u for u in listed.json()["data"]}
    assert by_id[str(shared.user_id)]["profile_shared"] is True
    assert by_id[str(single.user_id)]["profile_shared"] is False
    assert by_id[str(admin.user_id)]["profile_shared"] is False
    # The other school is never named.
    assert str(b) not in listed.text


def test_US_102_user_list_needs_user_manage_and_hides_other_schools(world: Any, api: Any) -> None:
    assert api.call(world.person("teacher"), "GET", USERS).status_code == 403
    other = world.b.people["target"]
    res = api.call(world.person("owner"), "GET", f"{USERS}/{other.user_id}")
    assert res.status_code == 404


# --- StaffMemberOut.status ------------------------------------------------------------------


def test_FR_TEN_010_staff_directory_reports_member_status(
    world: Any, api: Any, admin_engine: Engine
) -> None:
    invited = W.add_member(admin_engine, world.a.tenant_id, ["teacher"], status="invited")
    res = api.call(world.person("owner"), "GET", "/api/v1/staff", params={"limit": 200})
    assert res.status_code == 200, res.text
    by_id = {m["membership_id"]: m for m in res.json()["data"]}
    assert by_id[str(invited.membership_id)]["status"] == "invited"
    assert by_id[str(world.person("teacher").membership_id)]["status"] == "active"
    assert {m["status"] for m in by_id.values()} <= {"active", "invited"}
    assert api.call(world.person("teacher"), "GET", "/api/v1/staff").status_code == 403
