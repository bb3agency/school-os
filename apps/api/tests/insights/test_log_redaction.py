"""No personal data in logs from attendance, marks, notes, flags and timelines (SEC-008,
invariant 5, docs/12 §4.6).

The new personal fields of M5 (behaviour-note text, action notes, closing notes, marks, subject
names, the student names on rosters and timelines) pass through every route while stdout and
stderr are captured; none may appear in the log lines, and neither may a refused Aadhaar number.
"""

from __future__ import annotations

import sys
from typing import Any

import pytest
from sqlalchemy import Engine

from app.core.redaction import verhoeff_check_digit

pytestmark = pytest.mark.db
S = sys.modules["sos_test_insights_support"]

NOTE = "Pedapudi Synthetica cried during the maths period"
ACTION = "Spoke to Pedapudi Ramalakshmi at the gate"
CLOSING = "Pedapudi family says the fever is over"
SUBJECT = "Pedapudi special subject"


def test_SEC_008_m5_calls_do_not_log_personal_data(
    school: Any, api: Any, admin_engine: Engine, capsys: pytest.CaptureFixture[str]
) -> None:
    body = "65432109876"
    aadhaar = body + verhoeff_check_digit(body)
    ct, principal = school.people["ct"], school.people["principal"]
    a1 = school.ids["a1"]
    section = school.ids["section_9a"]
    day = S.school_days(1)[0].isoformat()
    exam_id = S.exam(school, "Synthetic log test", S.school_days(1)[0])
    capsys.readouterr()
    res = api.call(
        ct,
        "POST",
        f"/api/v1/sections/{section}/attendance",
        json={"entries": [{"student_id": str(a1), "on_date": day, "status": "absent"}]},
    )
    assert res.status_code == 200, res.text
    res = api.call(
        ct,
        "POST",
        f"/api/v1/sections/{section}/exams/{exam_id}/marks",
        json={
            "entries": [
                {"student_id": str(a1), "subject": SUBJECT, "max_marks": "50", "marks": "17.5"}
            ]
        },
    )
    assert res.status_code == 200, res.text
    res = api.call(
        ct,
        "POST",
        f"/api/v1/students/{a1}/behaviour-notes",
        json={"category": "concern", "text": NOTE},
    )
    assert res.status_code == 201, res.text
    refused = api.call(
        ct,
        "POST",
        f"/api/v1/students/{a1}/behaviour-notes",
        json={"category": "concern", "text": f"{NOTE} {aadhaar}"},
    )
    assert refused.status_code == 422
    flag = api.call(
        ct, "POST", f"/api/v1/students/{a1}/flags", json={"indicator": "behaviour", "note": NOTE}
    )
    assert flag.status_code == 201, flag.text
    flag_id = flag.json()["id"]
    acted = api.call(
        ct,
        "POST",
        f"/api/v1/insights/flags/{flag_id}/actions",
        json={"kind": "met_parent", "note": ACTION},
    )
    assert acted.status_code == 201, acted.text
    closed = api.call(
        ct,
        "POST",
        f"/api/v1/insights/flags/{flag_id}/close",
        json={"reason": "improved", "note": CLOSING},
        headers={"If-Match": f'W/"{acted.json()["version"]}"'},
    )
    assert closed.status_code == 200, closed.text
    for path in (
        f"/api/v1/students/{a1}/behaviour-notes",
        f"/api/v1/students/{a1}/timeline",
        f"/api/v1/insights/flags/{flag_id}",
        "/api/v1/insights/flags?view=all",
        f"/api/v1/sections/{section}/attendance?date={day}",
        f"/api/v1/sections/{section}/exams/{exam_id}/marks",
    ):
        assert api.call(ct, "GET", path).status_code == 200, path
    assert api.call(principal, "GET", "/api/v1/insights/summary").status_code == 200
    S.evaluate(school, [a1])
    out = capsys.readouterr()
    logs = out.out + out.err
    assert "http.request" in logs, "access log lines were captured"
    assert "academics.attendance_recorded" in logs
    for secret in (NOTE, ACTION, CLOSING, SUBJECT, "Pedapudi", aadhaar, "Synthetica A1"):
        assert secret not in logs, secret
    # Marks never appear as values (timestamps may contain the digits, so match JSON tokens).
    for token in ('"17.5"', '"17.50"', ":17.5,", '"marks"', '"text"', '"note"'):
        assert token not in logs, token
