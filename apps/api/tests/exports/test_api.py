"""Export routes over HTTP (docs/09 Exports; FR-EXP-002..004, SEC-003, SEC-005, SEC-015).

Allowed roles get 2xx, other roles 403, stale sign-ins 428 where step-up applies, other schools'
ids 404 (the full role x route matrix and BOLA sweep live in tests/security)."""

from __future__ import annotations

import sys
import uuid
from typing import Any

import pytest
from sqlalchemy import Engine

pytestmark = pytest.mark.db
EX = sys.modules["sos_test_exports_objects"]
BASE = "/api/v1/exports"


def _precheck_body(school: Any, **kw: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "profile_key": "cisce-registration-2026",
        "scope": {"section_ids": [str(school.ids["section_9a"])]},
        "format": ["xlsx"],
    }
    body.update(kw)
    return body


def test_FR_EXP_002_request_poll_download(school: Any, api: Any, admin_engine: Engine) -> None:
    EX.student(school)
    coordinator = school.people["exam_coordinator"]
    res = api.call(
        coordinator,
        "POST",
        BASE,
        json=_precheck_body(school),
        headers={"Idempotency-Key": f"exp-{uuid.uuid4().hex}"},
    )
    assert res.status_code == 202, res.text
    body = res.json()
    assert res.headers["Location"] == f"{BASE}/{body['id']}"
    assert body["status"] == "queued"
    assert body["files"] == []
    assert "student_ids" not in body, "the API returns counts, not the student list"
    export_id = uuid.UUID(body["id"])

    res = api.call(coordinator, "GET", f"{BASE}/{export_id}/download-url")
    assert res.status_code == 409
    assert res.json()["code"] == "export_not_ready"

    assert EX.run(school, export_id) == "ready"
    res = api.call(coordinator, "GET", f"{BASE}/{export_id}")
    assert res.status_code == 200
    assert res.json()["status"] == "ready"
    assert [f["format"] for f in res.json()["files"]] == ["xlsx"]
    res = api.call(
        coordinator, "GET", f"{BASE}/{export_id}/download-url", params={"format": "xlsx"}
    )
    assert res.status_code == 200, res.text
    link = res.json()
    assert link["format"] == "xlsx"
    assert link["filename"].endswith(".xlsx")
    listed = api.call(coordinator, "GET", BASE).json()["data"]
    assert str(export_id) in {e["id"] for e in listed}


def test_FR_EXP_004_student_list_needs_recent_mfa(school: Any, api: Any) -> None:
    EX.student(school)
    body = {"columns": ["admission_no", "full_name"], "format": "csv"}
    owner = school.people["owner"]
    stale = api.call(owner, "POST", f"{BASE}/student-list", json=body, auth_age_s=301)
    assert stale.status_code == 428
    assert stale.json()["code"] == "step_up_required"
    res = api.call(owner, "POST", f"{BASE}/student-list", json=body)
    assert res.status_code == 202, res.text
    assert res.json()["kind"] == "student_list"


@pytest.mark.parametrize("role", ["office_staff", "accountant", "class_teacher", "teacher"])
def test_SEC_003_roles_without_export_permissions_get_403(school: Any, api: Any, role: str) -> None:
    who = school.people[role]
    assert api.call(who, "POST", BASE, json=_precheck_body(school)).status_code == 403
    assert api.call(who, "GET", BASE).status_code == 403
    assert api.call(who, "GET", "/api/v1/export-profiles").status_code == 403
    res = api.call(who, "POST", f"{BASE}/student-list", json={"columns": ["full_name"]})
    assert res.status_code == 403


def test_owner_can_list_students_but_not_run_prechecks(school: Any, api: Any) -> None:
    """docs/07 §6.2: the owner holds student.export but not export.board/portal."""
    owner = school.people["owner"]
    assert api.call(owner, "POST", BASE, json=_precheck_body(school)).status_code == 403
    assert api.call(owner, "GET", BASE).status_code == 200


def test_validation_errors(school: Any, api: Any) -> None:
    admin = school.people["office_admin"]
    res = api.call(admin, "POST", BASE, json=_precheck_body(school, profile_key="nope"))
    assert res.status_code == 422
    assert res.json()["errors"][0]["code"] == "unknown_profile"
    res = api.call(admin, "POST", BASE, json=_precheck_body(school, format=["docx"]))
    assert res.status_code == 422
    res = api.call(
        admin,
        "POST",
        f"{BASE}/student-list",
        json={"columns": ["full_name", "aadhaar_dob_as_printed"]},
    )
    assert res.status_code == 422
    assert res.json()["errors"][0]["code"] == "column_not_exportable"


def test_SEC_001_other_school_and_other_member_get_404(school: Any, world: Any, api: Any) -> None:
    EX.student(school)
    export_id = EX.ready_export(school, "principal")
    b_owner = world.b.people["owner"]  # holds export.read_all and export.download_any in B
    for path in (f"{BASE}/{export_id}", f"{BASE}/{export_id}/download-url"):
        assert api.call(b_owner, "GET", path).status_code == 404
        # ADR-0021: a member without export.read_all / export.download_any.
        assert api.call(school.people["exam_coordinator"], "GET", path).status_code == 404
        assert api.call(school.people["principal"], "GET", path).status_code == 200
    listed = api.call(b_owner, "GET", BASE, params={"requested_by": "all", "limit": 200})
    assert listed.status_code == 200
    assert str(export_id) not in {e["id"] for e in listed.json()["data"]}


# --- ADR-0021 decision 1: step-up for every export (FR-EXP-004, SEC-005) ---------------------


@pytest.mark.parametrize(("mfa", "age"), [(True, 301), (False, 60)], ids=["stale-auth", "no-mfa"])
def test_ADR_0021_every_precheck_needs_recent_mfa(
    school: Any, api: Any, mfa: bool, age: int
) -> None:
    EX.student(school)
    coordinator = school.people["exam_coordinator"]  # not an MFA-required role
    for profile in ("cisce-registration-2026", "udise-plus"):
        body = _precheck_body(school, profile_key=profile)
        res = api.call(coordinator, "POST", BASE, json=body, mfa=mfa, auth_age_s=age)
        assert res.status_code == 428, res.text
        assert res.json()["code"] == "step_up_required"
        assert api.call(coordinator, "POST", BASE, json=body).status_code == 202


# --- ADR-0021 decision 2: UDISE+ category (C3) needs opt-in + permission + step-up -----------


def test_ADR_0021_udise_category_opt_in_over_http(
    school: Any, api: Any, admin_engine: Engine
) -> None:
    EX.student(school)
    body = _precheck_body(school, profile_key="udise-plus", include_sensitive=True)
    coordinator = school.people["exam_coordinator"]  # export.portal, no student.read_sensitive
    res = api.call(coordinator, "POST", BASE, json=body)
    assert res.status_code == 403
    assert res.json()["code"] == "sensitive_not_allowed"
    admin = school.people["office_admin"]
    stale = api.call(admin, "POST", BASE, json=body, auth_age_s=301)
    assert stale.status_code == 428
    assert stale.json()["code"] == "step_up_required"
    res = api.call(admin, "POST", BASE, json=body)
    assert res.status_code == 202, res.text
    assert res.json()["include_sensitive"] is True
    requested = EX.audit_rows(admin_engine, school.tenant_id, uuid.UUID(res.json()["id"]))[0]
    assert requested["summary"]["sensitive_columns"] == ["category", "religion", "disability"]


# --- ADR-0021 decision 3: who sees whose exports ---------------------------------------------


def _coordinator_export(school: Any) -> str:
    EX.student(school)
    return str(EX.ready_export(school, "exam_coordinator"))


def test_ADR_0021_read_all_sees_every_export_but_not_its_files(school: Any, api: Any) -> None:
    export_id = _coordinator_export(school)
    coordinator = school.people["exam_coordinator"]
    for role in ("principal", "office_admin", "owner"):  # export.read_all
        who = school.people[role]
        mine = api.call(who, "GET", BASE, params={"limit": 200}).json()["data"]
        assert export_id not in {e["id"] for e in mine}, "default: your own exports only"
        res = api.call(who, "GET", BASE, params={"requested_by": "all", "limit": 200})
        assert res.status_code == 200, (role, res.text)
        item = next(e for e in res.json()["data"] if e["id"] == export_id)
        assert item["requested_by"] == {
            "membership_id": str(coordinator.membership_id),
            "display_name": coordinator.display_name,
        }
        assert item["own"] is False
        assert "student_ids" not in item
        detail = api.call(who, "GET", f"{BASE}/{export_id}")
        assert detail.status_code == 200
        assert detail.json()["requested_by"]["membership_id"] == str(coordinator.membership_id)
    for role in ("principal", "office_admin"):  # no export.download_any
        who = school.people[role]
        item = api.call(who, "GET", f"{BASE}/{export_id}").json()
        assert item["can_download"] is False
        res = api.call(who, "GET", f"{BASE}/{export_id}/download-url")
        assert res.status_code == 403
        assert res.json()["code"] == "not_own_export"
    own = api.call(coordinator, "GET", f"{BASE}/{export_id}").json()
    assert (own["own"], own["can_download"]) == (True, True)


@pytest.mark.parametrize("role", ["exam_coordinator", "coordinator_2"])
def test_ADR_0021_requested_by_all_needs_read_all(school: Any, api: Any, role: str) -> None:
    res = api.call(school.people[role], "GET", BASE, params={"requested_by": "all"})
    assert res.status_code == 403
    assert res.json()["code"] == "forbidden"
    bad = api.call(school.people["principal"], "GET", BASE, params={"requested_by": "everyone"})
    assert bad.status_code == 422


def test_ADR_0021_download_any_needs_step_up_and_is_audited(
    school: Any, api: Any, admin_engine: Engine
) -> None:
    export_id = _coordinator_export(school)
    owner = school.people["owner"]  # export.download_any (school_step_up); not export.board
    item = api.call(owner, "GET", f"{BASE}/{export_id}").json()
    assert (item["own"], item["can_download"]) == (False, True)
    path = f"{BASE}/{export_id}/download-url"
    stale = api.call(owner, "GET", path, auth_age_s=301)
    assert stale.status_code == 428
    assert stale.json()["code"] == "step_up_required"
    res = api.call(owner, "GET", path)
    assert res.status_code == 200, res.text
    assert res.json()["format"] == "xlsx"
    mine = api.call(school.people["exam_coordinator"], "GET", path, auth_age_s=301)
    assert mine.status_code == 200, "own pre-check download without restricted values: unchanged"
    downloads = [
        r["summary"]
        for r in EX.audit_rows(admin_engine, school.tenant_id, uuid.UUID(export_id))
        if r["action"] == "export.downloaded"
    ]
    assert [d["own_export"] for d in downloads] == [False, True]
    assert all(
        d["requested_by_membership"] == str(school.people["exam_coordinator"].membership_id)
        for d in downloads
    )
    others = api.call(school.people["coordinator_2"], "GET", path)
    assert others.status_code == 404, "no export.read_all / download_any: existence hidden"


def test_export_profiles_route(school: Any, api: Any) -> None:
    res = api.call(school.people["exam_coordinator"], "GET", "/api/v1/export-profiles")
    assert res.status_code == 200
    keys = {p["key"] for p in res.json()}
    assert keys == {"cisce-registration-2026", "udise-plus"}
