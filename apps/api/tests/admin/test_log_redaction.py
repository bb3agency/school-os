"""No personal data in logs from the full export and retention settings (SEC-008, invariant 5,
docs/12 §4.6; FR-ADM-001, FR-ADM-002).

The archive holds names, dates of birth, guardian phone numbers and (when included) restricted
values; requesting, building, downloading and purging it, and changing retention, must log IDs,
codes and counts only.
"""

from __future__ import annotations

import datetime as dt
import sys
import uuid
from typing import Any

import pytest
from sqlalchemy import Engine

from app.admin import service as admin
from app.students.schemas import ValueIn

pytestmark = pytest.mark.db
AD = sys.modules["sos_test_admin_support"]

NAME = "Kothapalli Synthetica Lakshmiprasanna"
NOTE = "Synthetic Kothapalli epilepsy medication note"
PHONE = "9123405678"
ADDRESS = "Synthetic Kothapalli Lane 7, Vijayawada"


def test_SEC_008_full_export_and_retention_do_not_log_personal_data(
    school: Any, api: Any, admin_engine: Engine, capsys: pytest.CaptureFixture[str]
) -> None:
    sid = AD.SW.create(
        school,
        name=NAME,
        section_key="section_9c",
        extra=[ValueIn(attribute_key="health_notes", source="parent_form", value=NOTE)],
    )
    AD.SW.add_guardian(
        school, sid, full_name="Kothapalli Synthetica Guardian", phone=PHONE, address=ADDRESS
    )
    AD.settle(admin_engine, school)
    capsys.readouterr()
    owner = school.people["owner"]
    res = api.call(owner, "POST", "/api/v1/admin/tenant-export", json={"include_sensitive": True})
    assert res.status_code == 202, res.text
    export_id = uuid.UUID(res.json()["id"])
    assert AD.run(school, export_id) == "ready"
    link = api.call(owner, "GET", f"/api/v1/admin/tenant-export/{export_id}/download-url")
    assert link.status_code == 200, link.text
    assert api.call(owner, "GET", "/api/v1/admin/tenant-export").status_code == 200
    admin.purge_expired(school.tenant_id, now=dt.datetime.now(dt.UTC) + dt.timedelta(hours=25))
    got = api.call(owner, "GET", "/api/v1/admin/retention")
    put = api.call(
        owner,
        "PUT",
        "/api/v1/admin/retention",
        json={"rules": {"exports": 5}},
        headers={"If-Match": got.headers["ETag"]},
    )
    assert put.status_code == 200, put.text
    api.call(
        owner,
        "PUT",
        "/api/v1/admin/retention",
        json={"rules": {}},
        headers={"If-Match": put.headers["ETag"]},
    )
    out = capsys.readouterr()
    logs = out.out + out.err
    assert "admin.export.completed" in logs, "log lines were captured"
    assert "admin.export.downloaded" in logs
    assert "admin.retention.updated" in logs
    for secret in (NAME, "Kothapalli", "Lakshmiprasanna", NOTE, PHONE, ADDRESS, "2012-03-14"):
        assert secret not in logs, secret
    assert owner.display_name not in logs
    assert link.json()["url"] not in logs, "presigned URLs are never logged"
