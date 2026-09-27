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
    b_owner = world.b.people["owner"]
    for path in (f"{BASE}/{export_id}", f"{BASE}/{export_id}/download-url"):
        assert api.call(b_owner, "GET", path).status_code == 404
        assert api.call(school.people["office_admin"], "GET", path).status_code == 404
        assert api.call(school.people["principal"], "GET", path).status_code == 200


def test_export_profiles_route(school: Any, api: Any) -> None:
    res = api.call(school.people["exam_coordinator"], "GET", "/api/v1/export-profiles")
    assert res.status_code == 200
    keys = {p["key"] for p in res.json()}
    assert keys == {"cisce-registration-2026", "udise-plus"}
