"""Admin routes over HTTP (docs/09 Exports, audit, admin; US-1201 AC1; FR-ADM-001, FR-ADM-002;
SEC-005 step-up; 428/403/404/409/412/422 problem details). Synthetic data only."""

from __future__ import annotations

import sys
import uuid
from typing import Any

import pytest
from sqlalchemy import Engine

pytestmark = pytest.mark.db
AD = sys.modules["sos_test_admin_support"]
PATH = "/api/v1/admin/tenant-export"


def test_FR_ADM_001_full_export_over_http(school: Any, api: Any, admin_engine: Engine) -> None:
    AD.settle(admin_engine, school)
    owner = school.people["owner"]
    res = api.call(owner, "POST", PATH, json={})
    assert res.status_code == 202, res.text
    body = res.json()
    assert res.headers["Location"] == f"{PATH}/{body['id']}"
    assert body["status"] == "queued"
    assert body["requested_by"]["membership_id"] == str(owner.membership_id)
    assert body["requested_by"]["display_name"] == owner.display_name
    again = api.call(owner, "POST", PATH, json={})
    assert again.status_code == 409
    assert again.json()["code"] == "tenant_export_in_progress"
    early = api.call(owner, "GET", f"{PATH}/{body['id']}/download-url")
    assert early.status_code == 409
    assert early.json()["code"] == "export_not_ready"

    assert AD.run(school, uuid.UUID(body["id"])) == "ready"
    detail = api.call(owner, "GET", f"{PATH}/{body['id']}")
    assert detail.status_code == 200
    assert detail.json()["status"] == "ready"
    assert detail.json()["can_download"] is True
    assert detail.json()["counts"]["tables"]["students"] >= 0
    listed = api.call(owner, "GET", PATH)
    assert listed.status_code == 200
    assert body["id"] in {e["id"] for e in listed.json()["data"]}
    link = api.call(owner, "GET", f"{PATH}/{body['id']}/download-url")
    assert link.status_code == 200, link.text
    out = link.json()
    assert out["content_type"] == "application/zip"
    assert out["size_bytes"] > 0
    assert "X-Amz-Expires=300" in out["url"] or "X-Amz-Expires=" in out["url"]
    # Another owner of the school may download it too (the school's export, not a person's).
    other = api.call(school.people["owner_2"], "GET", f"{PATH}/{body['id']}/download-url")
    assert other.status_code == 200, other.text
    assert api.call(school.people["owner_2"], "GET", f"{PATH}/{body['id']}").json()["own"] is False


def test_SEC_005_request_and_download_need_step_up_but_reading_does_not(
    school: Any, api: Any, admin_engine: Engine
) -> None:
    export_id = AD.ready_export(admin_engine, school)
    owner = school.people["owner"]
    for method, path in (("POST", PATH), ("GET", f"{PATH}/{export_id}/download-url")):
        res = api.call(owner, method, path, json={} if method == "POST" else None, auth_age_s=301)
        assert res.status_code == 428, res.text
        assert res.json()["code"] == "step_up_required"
    for path in (PATH, f"{PATH}/{export_id}"):
        assert api.call(owner, "GET", path, auth_age_s=3600).status_code == 200


@pytest.mark.parametrize("role", ["principal", "office_admin", "accountant", "auditor_readonly"])
def test_FR_ADM_001_other_roles_are_refused(school: Any, api: Any, role: str) -> None:
    person = school.people[role]
    assert api.call(person, "POST", PATH, json={}).status_code == 403
    assert api.call(person, "GET", PATH).status_code == 403
    assert api.call(person, "GET", f"{PATH}/{uuid.uuid4()}").status_code == 403


def test_FR_ADM_001_unknown_ids_are_404(school: Any, api: Any) -> None:
    owner = school.people["owner"]
    for suffix in ("", "/download-url"):
        res = api.call(owner, "GET", f"{PATH}/{uuid.uuid4()}{suffix}")
        assert res.status_code == 404, res.text


def test_FR_ADM_001_include_sensitive_and_idempotency_key(
    school: Any, api: Any, admin_engine: Engine
) -> None:
    AD.settle(admin_engine, school)
    owner = school.people["owner"]
    key = f"idem-{uuid.uuid4().hex}"
    first = api.call(
        owner, "POST", PATH, json={"include_sensitive": True}, headers={"Idempotency-Key": key}
    )
    assert first.status_code == 202, first.text
    assert first.json()["include_sensitive"] is True
    replay = api.call(
        owner, "POST", PATH, json={"include_sensitive": True}, headers={"Idempotency-Key": key}
    )
    assert replay.status_code == 202
    assert replay.headers["Location"] == first.headers["Location"]
    bad = api.call(owner, "POST", PATH, json={"include_sensitive": "yes please"})
    assert bad.status_code == 422
    unknown = api.call(owner, "POST", PATH, json={"everything": True})
    assert unknown.status_code == 422
    AD.settle(admin_engine, school)


def test_FR_ADM_002_retention_over_http_with_etag_and_step_up(school: Any, api: Any) -> None:
    owner = school.people["owner"]
    got = api.call(owner, "GET", "/api/v1/admin/retention")
    assert got.status_code == 200, got.text
    etag = got.headers["ETag"]
    version = got.json()["version"]
    assert etag == f'W/"{version}"'
    body = {"rules": {"import_raw_files": 60}}
    stale = api.call(
        owner,
        "PUT",
        "/api/v1/admin/retention",
        json=body,
        headers={"If-Match": etag},
        auth_age_s=301,
    )
    assert stale.status_code == 428
    missing = api.call(owner, "PUT", "/api/v1/admin/retention", json=body)
    assert missing.status_code == 400
    assert missing.json()["code"] == "if_match_required"
    put = api.call(owner, "PUT", "/api/v1/admin/retention", json=body, headers={"If-Match": etag})
    assert put.status_code == 200, put.text
    assert put.headers["ETag"] == f'W/"{version + 1}"'
    assert {c["key"]: c["days"] for c in put.json()["categories"]}["import_raw_files"] == 60
    conflict = api.call(
        owner, "PUT", "/api/v1/admin/retention", json=body, headers={"If-Match": etag}
    )
    assert conflict.status_code == 412
    bad = api.call(
        owner,
        "PUT",
        "/api/v1/admin/retention",
        json={"rules": {"import_raw_files": 500}},
        headers={"If-Match": put.headers["ETag"]},
    )
    assert bad.status_code == 422
    assert bad.json()["errors"][0]["code"] == "out_of_bounds"
    back = api.call(
        owner,
        "PUT",
        "/api/v1/admin/retention",
        json={"rules": {}},
        headers={"If-Match": put.headers["ETag"]},
    )
    assert back.status_code == 200
    denied = api.call(school.people["office_admin"], "GET", "/api/v1/admin/retention")
    assert denied.status_code == 403
