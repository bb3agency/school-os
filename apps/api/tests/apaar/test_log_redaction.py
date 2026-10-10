"""No personal data in logs from the APAAR consent register and the PEN guard (SEC-008,
invariant 5, PRV-020, PRV-021, docs/12 §4.6).

The new personal fields (the consent note, the printed names and dates, the PEN and APAAR ID in
checks and searches) pass through every route while stdout/stderr are captured; none may appear
in the log lines, nor in audit summaries.
"""

from __future__ import annotations

import sys
import uuid
from typing import Any

import pytest
from sqlalchemy import Engine

from app.devtools.fake_ids import synthetic_apaar_id

pytestmark = pytest.mark.db
A = sys.modules["sos_test_apaar_support"]
W = sys.modules["sos_test_api_world"]

NAME = "Kothapalli Synthetica Lavanyamma"
NOTE = "Kothapalli mother returned the form at the gate"
PEN = "7" + str(uuid.uuid4().int)[:10]


def test_SEC_008_consent_and_pen_calls_do_not_log_personal_data(
    world: Any, api: Any, admin_engine: Engine, capsys: pytest.CaptureFixture[str]
) -> None:
    import random

    apaar = synthetic_apaar_id(random.Random(77))
    admin = world.person("office_admin")
    sid = A.student(world.a, name=NAME)
    capsys.readouterr()
    doc = A.signed_form(admin_engine, world.a, admin)
    assert A.record(api, admin, sid, A.given(doc, note=NOTE)).status_code == 201
    api.call(admin, "GET", f"/api/v1/students/{sid}/apaar-consent")
    api.call(admin, "GET", f"/api/v1/students/{sid}/apaar-consent/form")
    api.call(admin, "GET", "/api/v1/apaar/consents", params={"status": "given"})
    pen = api.call(
        admin,
        "POST",
        f"/api/v1/students/{sid}/values",
        json={"attribute_key": "udise_pen", "source": "udise_plus", "value": PEN},
    )
    assert pen.status_code == 201, pen.text
    clash = api.call(
        admin,
        "POST",
        "/api/v1/students",
        json={
            "values": [
                {"attribute_key": "full_name", "source": "admission_register", "value": NAME},
                {"attribute_key": "udise_pen", "source": "tc_incoming", "value": PEN},
            ],
            "admission_kind": "transfer_in",
        },
    )
    assert clash.status_code == 422
    api.call(
        admin,
        "POST",
        "/api/v1/students/national-id-check",
        json={"udise_pen": PEN, "apaar_id": apaar},
    )
    api.call(admin, "POST", "/api/v1/students/search", json={"udise_pen": PEN})
    out = capsys.readouterr()
    logs = out.out + out.err
    for secret in (NAME, "Kothapalli", NOTE, PEN, apaar):
        assert secret not in logs
    summaries = " ".join(str(e["summary"]) for e in W.audit_events(admin_engine, world.a.tenant_id))
    for secret in (NAME, NOTE, PEN):
        assert secret not in summaries
