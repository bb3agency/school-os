"""No personal data in logs from change requests (SEC-008, invariant 5, docs/12 §4.6).

New personal fields of this module (requested values, reasons, decision notes, the memo page)
pass through every route while stdout/stderr are captured; none may appear in the log lines.
"""

from __future__ import annotations

import sys
import uuid
from typing import Any

import pytest
from sqlalchemy import Engine

from app.core.redaction import verhoeff_check_digit

pytestmark = pytest.mark.db
CR = sys.modules["sos_test_changes_objects"]
BASE = "/api/v1/change-requests"

NEW_NAME = "Kothapalli Synthetica Harshavardhan"
REASON = "Birth certificate of Kothapalli family spells it differently"
NOTE = "Verified against Kothapalli original certificate"
REJECT = "Kothapalli evidence scan unreadable"


def test_SEC_008_change_request_calls_do_not_log_personal_data(
    school: Any, api: Any, admin_engine: Engine, capsys: pytest.CaptureFixture[str]
) -> None:
    body = "67890123456"
    aadhaar = body + verhoeff_check_digit(body)
    admin, principal = school.people["office_admin"], school.people["principal"]
    sids = [CR.student(school), CR.student(school)]
    docs = [CR.evidence(admin_engine, school, admin) for _ in sids]
    capsys.readouterr()
    ids = []
    for sid, doc in zip(sids, docs, strict=True):
        res = api.call(
            admin,
            "POST",
            BASE,
            json={
                "student_id": str(sid),
                "attribute_key": "full_name",
                "new_value": NEW_NAME,
                "reason": REASON,
                "evidence_document_id": str(doc),
            },
        )
        assert res.status_code == 201, res.text
        ids.append(res.json()["id"])
    # Refused input must not be echoed into logs either.
    api.call(
        admin,
        "POST",
        BASE,
        json={
            "student_id": str(sids[0]),
            "attribute_key": "full_name",
            "new_value": f"{NEW_NAME} {aadhaar}",
            "reason": f"{REASON} {aadhaar}",
            "evidence_document_id": str(uuid.uuid4()),
        },
    )
    h = {"If-Match": 'W/"1"'}
    api.call(principal, "POST", f"{BASE}/{ids[0]}/approve", json={"note": NOTE}, headers=h)
    api.call(principal, "POST", f"{BASE}/{ids[1]}/reject", json={"reason": REJECT}, headers=h)
    for rid in ids:
        api.call(principal, "GET", f"{BASE}/{rid}")
        api.call(principal, "GET", f"{BASE}/{rid}/memo")
    api.call(admin, "GET", BASE)
    out = capsys.readouterr()
    logs = out.out + out.err
    assert "http.request" in logs, "access log lines were captured"
    assert "change_request.approved" in logs
    for secret in (NEW_NAME, "Kothapalli", "Harshavardhan", REASON, NOTE, REJECT, aadhaar):
        assert secret not in logs, secret
