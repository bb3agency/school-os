"""No personal data in logs from exports (SEC-008, invariant 5, docs/12 §4.6).

The export files hold names, dates of birth, parents' names and (when included) restricted
values; requesting, building, downloading and purging an export must log IDs, codes and counts
only.
"""

from __future__ import annotations

import datetime as dt
import sys
from typing import Any

import pytest

from app.exports import service as exports
from app.students.schemas import ValueIn

pytestmark = pytest.mark.db
EX = sys.modules["sos_test_exports_objects"]

NAME = "Kothapalli Synthetica Harshavardhini"
FATHER = "Kothapalli Synthetica Ramachandra"
NOTE = "Synthetic Kothapalli asthma inhaler note"


def test_SEC_008_export_flow_does_not_log_personal_data(
    school: Any, api: Any, capsys: pytest.CaptureFixture[str]
) -> None:
    from app.core.db import tenant_session

    sid = EX.student(
        school,
        name=NAME,
        dob="2011-07-09",
        extra=[ValueIn(attribute_key="health_notes", source="parent_form", value=NOTE)],
    )
    with tenant_session(school.tenant_id, school.people["owner"].user_id) as db:
        from app.students import service as students

        students.record_value(
            db, EX.SW.admin_ctx(school), sid, "father_name", "parent_form", FATHER
        )
    capsys.readouterr()
    principal = school.people["principal"]
    pre = api.call(
        principal,
        "POST",
        "/api/v1/exports",
        json={
            "profile_key": "udise-plus",
            "scope": {"section_ids": [str(school.ids["section_9a"])]},
            "format": ["xlsx", "pdf"],
            "language": "te",
        },
    )
    assert pre.status_code == 202, pre.text
    lst = api.call(
        principal,
        "POST",
        "/api/v1/exports/student-list",
        json={"columns": ["full_name", "dob", "father_name", "health_notes"], "format": "csv"},
    )
    assert lst.status_code == 202, lst.text
    owner = school.people["owner"]
    for body in (pre.json(), lst.json()):
        assert EX.run(school, body["id"]) == "ready"
        res = api.call(principal, "GET", f"/api/v1/exports/{body['id']}/download-url")
        assert res.status_code == 200, res.text
        # ADR-0021: another member's export (details with the requester's name, download).
        detail = api.call(owner, "GET", f"/api/v1/exports/{body['id']}")
        assert detail.json()["requested_by"]["display_name"] == principal.display_name
        res = api.call(owner, "GET", f"/api/v1/exports/{body['id']}/download-url")
        assert res.status_code == 200, res.text
    listed = api.call(owner, "GET", "/api/v1/exports", params={"requested_by": "all"})
    assert listed.status_code == 200
    exports.purge_expired(school.tenant_id, now=dt.datetime.now(dt.UTC) + dt.timedelta(days=8))
    out = capsys.readouterr()
    logs = out.out + out.err
    assert "exports.completed" in logs, "log lines were captured"
    assert "exports.downloaded" in logs
    for secret in (NAME, "Kothapalli", "Harshavardhini", FATHER, NOTE, "2011-07-09", "09/07/2011"):
        assert secret not in logs, secret
    # Staff names shown as the requester (ADR-0021) never reach the logs either.
    assert principal.display_name not in logs
