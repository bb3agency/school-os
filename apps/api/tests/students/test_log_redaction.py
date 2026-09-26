"""No student personal data in logs (SEC-008, invariant 5, docs/12 §4.6).

Every new personal field of the student module (names, DOB, parent names, guardian phone and
address, C3 values, search text, a pasted Aadhaar number) passes through the API while stdout
and stderr are captured; none of it may appear in the log lines.
"""

from __future__ import annotations

import sys
from typing import Any

import pytest

from app.core.redaction import verhoeff_check_digit

pytestmark = pytest.mark.db
SW = sys.modules["sos_test_student_world"]

NAME = "Jonnalagadda Synthetica Venkatesh"
FATHER = "Jonnalagadda Syntheticus Rao"
DOB = "2011-11-23"
PHONE = "9812345670"
ADDRESS = "Synthetic Colony Lane 42"
HEALTH = "Synthetic peanut allergy"
LAST4 = "7319"


def test_SEC_008_student_calls_do_not_log_personal_data(
    world: Any, api: Any, capsys: pytest.CaptureFixture[str]
) -> None:
    admin = world.person("office_admin")
    body = "45678901234"
    aadhaar = body + verhoeff_check_digit(body)
    capsys.readouterr()
    res = api.call(
        admin,
        "POST",
        "/api/v1/students",
        json={
            "values": [
                {"attribute_key": "full_name", "source": "admission_register", "value": NAME},
                {"attribute_key": "dob", "source": "admission_register", "value": DOB},
                {"attribute_key": "father_name", "source": "admission_register", "value": FATHER},
                {"attribute_key": "health_notes", "source": "parent_form", "value": HEALTH},
                {"attribute_key": "aadhaar_last4", "source": "aadhaar_as_printed", "value": LAST4},
            ],
            "section_id": str(world.a.ids["section_9a"]),
        },
    )
    assert res.status_code == 201, res.text
    sid = res.json()["id"]
    g = api.call(
        admin,
        "POST",
        f"/api/v1/students/{sid}/guardians",
        json={"relationship": "father", "full_name": FATHER, "phone": PHONE, "address": ADDRESS},
    )
    assert g.status_code == 201
    api.call(admin, "GET", "/api/v1/students", params={"query": "jonnalagadda venkatesh"})
    api.call(admin, "GET", f"/api/v1/students/{sid}")
    api.call(
        admin,
        "POST",
        f"/api/v1/students/{sid}/sensitive-reveal",
        json={"attribute_key": "health_notes"},
    )
    api.call(
        admin,
        "POST",
        f"/api/v1/students/{sid}/sensitive-reveal",
        json={"attribute_key": "guardian_phone", "guardian_id": g.json()["id"]},
    )
    # Rejected input (full Aadhaar, bad date, identity change) must not be echoed into logs.
    api.call(
        admin,
        "POST",
        f"/api/v1/students/{sid}/values",
        json={"attribute_key": "caste", "source": "parent_form", "value": f"{aadhaar} {NAME}"},
    )
    api.call(
        admin,
        "POST",
        f"/api/v1/students/{sid}/values",
        json={"attribute_key": "dob", "source": "admission_register", "value": DOB},
    )
    api.call(admin, "GET", "/api/v1/students", params={"query": aadhaar})
    out = capsys.readouterr()
    logs = out.out + out.err
    assert "http.request" in logs, "access log lines were captured"
    for secret in (
        NAME,
        "Jonnalagadda",
        "Venkatesh",
        FATHER,
        DOB,
        PHONE,
        "98123",
        ADDRESS,
        HEALTH,
        "peanut",
        aadhaar,
    ):
        assert secret not in logs, secret
    assert f"XXXX XXXX {LAST4}" not in logs
