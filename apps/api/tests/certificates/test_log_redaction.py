"""No personal data in logs from certificates and registers (SEC-008, invariant 5, docs/12
§4.6).

New personal fields of this module (the printed names and dates, inputs, reasons, the print
pages and registers) pass through every route while stdout/stderr are captured; none may appear
in the log lines, and neither may a refused Aadhaar number.
"""

from __future__ import annotations

import sys
from typing import Any

import pytest
from sqlalchemy import Engine

from app.core.redaction import verhoeff_check_digit

pytestmark = pytest.mark.db
C = sys.modules["sos_test_certificates_support"]

NAME = "Kothapalli Synthetica Harshavardhan"
NOTE = "Kothapalli family moving to Vijayawada"
CANCEL = "Kothapalli certificate printed with the wrong purpose"
DUPLICATE = "Kothapalli original lost in the rain"


def test_SEC_008_certificate_calls_do_not_log_personal_data(
    school: Any, api: Any, admin_engine: Engine, capsys: pytest.CaptureFixture[str]
) -> None:
    body = "78901234567"
    aadhaar = body + verhoeff_check_digit(body)
    admin, principal = school.people["office_admin"], school.people["principal"]
    sids = [
        C.SW.create(school, name=NAME, section_key="section_9a", admission_no=f"LR/{n}")
        for n in range(3)
    ]
    capsys.readouterr()
    base = "/api/v1/students/{}/certificates"
    bonafide = api.call(
        admin,
        "POST",
        base.format(sids[0]),
        json={"certificate_type": "bonafide", "inputs": {"purpose": "other", "purpose_note": NOTE}},
    )
    assert bonafide.status_code == 201, bonafide.text
    # Refused input must not be echoed into logs either.
    api.call(
        admin,
        "POST",
        base.format(sids[1]),
        json={
            "certificate_type": "bonafide",
            "inputs": {"purpose": "other", "purpose_note": f"{NOTE} {aadhaar}"},
        },
    )
    tc = api.call(
        admin,
        "POST",
        base.format(sids[2]),
        json={"certificate_type": "transfer", "inputs": {**C.tc_inputs(), "remarks": NOTE}},
    )
    assert tc.status_code == 201, tc.text
    h = {"If-Match": 'W/"1"'}
    approved = api.call(
        principal,
        "POST",
        f"/api/v1/certificates/{tc.json()['id']}/approve",
        json={"note": NOTE},
        headers=h,
    )
    assert approved.status_code == 200, approved.text
    cid = bonafide.json()["id"]
    api.call(admin, "GET", f"/api/v1/certificates/{cid}/print")
    api.call(admin, "GET", "/api/v1/certificates")
    api.call(admin, "POST", f"/api/v1/certificates/{cid}/duplicates", json={"reason": DUPLICATE})
    api.call(
        principal,
        "POST",
        f"/api/v1/certificates/{cid}/cancel",
        json={"reason": CANCEL},
        headers={"If-Match": f'W/"{bonafide.json()["version"]}"'},
    )
    for path in (
        "/api/v1/registers/transfer-certificates",
        "/api/v1/registers/certificates",
        "/api/v1/registers/admission-withdrawal",
    ):
        assert api.call(admin, "GET", path).status_code == 200
    C.render(school, C.call(school, admin, "office_admin", C.certificates.get_certificate, cid))
    out = capsys.readouterr()
    logs = out.out + out.err
    assert "http.request" in logs, "access log lines were captured"
    assert "certificate.issued" in logs
    for secret in (
        NAME,
        "Kothapalli",
        "Harshavardhan",
        NOTE,
        CANCEL,
        DUPLICATE,
        aadhaar,
        "14/03/2012",
        "2012-03-14",
    ):
        assert secret not in logs, secret
