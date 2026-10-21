"""No request text, note, response, requester or nominee detail in logs or audit summaries
(R3-1A; FR-DPDP-007, PRV-033; invariant 5, SEC-008). Synthetic data only."""

from __future__ import annotations

import sys
import uuid
from typing import Any

import pytest
from sqlalchemy import Engine

pytestmark = pytest.mark.db
P = sys.modules["sos_test_privacy_support"]
W = sys.modules["sos_test_api_world"]

WORDS = "Kothapalli Synthetica asked about the bus register entry"
NOTE = "Rang back Kothapalli on the landline"
ANSWER = "We checked the bus register with Kothapalli Synthetica"
NOMINEE = "Kothapalli Nomineeamma"


def test_PRV_033_request_texts_never_reach_logs_or_audit(
    school: Any, api: Any, admin_engine: Engine, capsys: pytest.CaptureFixture[str]
) -> None:
    admin = school.people["office_admin"]
    principal = school.people["principal"]
    sid = P.student(school)
    gid = P.guardian(school, sid)
    capsys.readouterr()
    out = P.logged(api, admin, P.body("grievance", student_id=sid, text_=WORDS))
    path = f"{P.BASE}/{out['id']}"
    res = api.call(
        admin, "PATCH", path, json={"notes": NOTE}, headers={"If-Match": f'W/"{out["version"]}"'}
    )
    assert res.status_code == 200, res.text
    api.call(admin, "GET", path)
    api.call(admin, "GET", f"{path}/letters/acknowledgement")
    closed = api.call(principal, "POST", f"{path}/close", json={"response_text": ANSWER})
    assert closed.status_code == 200, closed.text
    api.call(admin, "GET", f"{path}/letters/grievance")
    P.logged(
        api,
        admin,
        P.body(
            "nomination", guardian_id=gid, nominee_name=NOMINEE, nominee_contact="+919000000003"
        ),
    )
    logs = capsys.readouterr()
    text = logs.out + logs.err
    audit = str(W.audit_events(admin_engine, school.tenant_id))
    for secret in (WORDS, NOTE, ANSWER, NOMINEE, "Synthetica Requester", "+919000000003"):
        assert secret not in text, secret
        assert secret not in audit, secret
    assert str(uuid.UUID(out["id"])) in text  # ids are fine
