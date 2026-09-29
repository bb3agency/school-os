"""No ledger names, amounts, codes or secrets in logs from the Tally connector (SEC-008,
invariant 5, docs/12 §4.6; ADR-0032 §4).

Every agent and staff route runs while stdout/stderr are captured, including refused requests
(an unselected group, a bad signature, a wrong code); none of the personal or secret values may
appear in the log lines.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy import Engine

from app.tally import agent_auth

from .conftest import T

pytestmark = pytest.mark.db

LEDGER = "Kothapalli Synthetica Harshavardhan 9A"
STAFF_LEDGER = "Kothapalli Synthetic Staff Advance"
AMOUNT = "48213.75"


def test_SEC_008_tally_calls_do_not_log_personal_data_or_secrets(
    api: Any, admin_engine: Engine, capsys: pytest.CaptureFixture[str]
) -> None:
    agent_auth.get_agent_stores.cache_clear()
    school = T.fresh_school(admin_engine)
    student = T.SW.create(school, name="Kothapalli Synthetica", section_key="section_9a")
    capsys.readouterr()
    code = T.new_code(api, school)["code"]
    bad_code = T.enrol_with(api, school.tenant_id, "QQQQ-QQQQ-QQQQ")
    assert bad_code.status_code == 401
    enrolled = T.enrol_with(api, school.tenant_id, code)
    assert enrolled.status_code == 201
    secret = enrolled.json()["secret"]
    agent = T.Agent(
        tenant_id=school.tenant_id,
        device_id=uuid.UUID(enrolled.json()["device_id"]),
        key_id=enrolled.json()["key_id"],
        secret=T.b64(secret),
    )
    T.catalog(api, agent, [("Sundry Debtors", None), ("Staff Advances", None)])
    T.select(api, school, ["Sundry Debtors"])
    refused = T.snapshot(api, agent, [T.party(STAFF_LEDGER, AMOUNT, group="Staff Advances")])
    assert refused.status_code == 422
    agent_auth.get_agent_stores.cache_clear()
    ok = T.snapshot(api, agent, [T.party(LEDGER, AMOUNT)])
    assert ok.status_code == 200, ok.text
    agent_auth.get_agent_stores.cache_clear()
    assert agent.call(api, "GET", "/api/v1/edge/tally/config", secret=bytes(32)).status_code == 401
    accountant = school.people["accountant"]
    party_id = api.call(accountant, "GET", "/api/v1/tally/parties").json()["data"][0]["id"]
    api.call(
        accountant,
        "POST",
        f"/api/v1/tally/parties/{party_id}/links",
        json={"student_id": str(student)},
    )
    api.call(accountant, "POST", "/api/v1/tally/parties/search", json={"query": "Kothapalli"})
    api.call(accountant, "GET", "/api/v1/tally/dues")
    api.call(accountant, "GET", f"/api/v1/tally/parties/{party_id}")
    out, err = capsys.readouterr()
    logged = out + err
    assert "tally.sync.received" in logged or "http.request" in logged  # something was logged
    for value in (LEDGER, STAFF_LEDGER, "Kothapalli", AMOUNT, "48213", code, secret):
        assert value not in logged, value
