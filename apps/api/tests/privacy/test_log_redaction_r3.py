"""No consent note, withdrawal detail, breach description, containment or close reason in logs
or audit summaries, also from the daily privacy and breach jobs (R3-2F; FR-DPDP-007,
FR-DPDP-021, FR-DPDP-033; invariant 5, SEC-008). Synthetic data only."""

from __future__ import annotations

import sys
from typing import Any

import pytest
from sqlalchemy import Engine

from app.privacy import breach_tasks, tasks

pytestmark = pytest.mark.db
P = sys.modules["sos_test_privacy_support"]
CP = sys.modules["sos_test_consent_support"]
W = sys.modules["sos_test_api_world"]

NOTE = "Kothapalli Synthetica signed at the gate"
REQUEST = "Kothapalli Synthetica withdraws the photo consent"
DESCRIPTION = "Kothapalli register photographed on the bus"
CONTAINMENT = "Kothapalli phone wiped by the depot"
REASON = "Kothapalli register was found unopened"


def test_SEC_008_consent_and_breach_texts_never_reach_logs_or_audit(
    world: Any, api: Any, admin_engine: Engine, capsys: pytest.CaptureFixture[str]
) -> None:
    office, principal, owner = (
        world.person("office_admin"),
        world.person("principal"),
        world.person("owner"),
    )
    pid = CP.shared_purpose(api, world.a, admin_engine)
    sid = CP.student(world.a)
    doc = CP.signed_form(admin_engine, world.a, office)
    capsys.readouterr()
    assert CP.record(api, office, sid, CP.given(pid, doc, note=NOTE)).status_code == 201
    api.call(office, "GET", f"/api/v1/students/{sid}/consents")
    request = P.logged(api, office, P.body("consent_withdrawal", student_id=sid, text_=REQUEST))
    res = api.call(
        principal,
        "POST",
        f"{P.BASE}/{request['id']}/consent-withdrawal",
        json={"purpose_id": str(pid), "relationship": "mother", "decided_on": "2026-10-01"},
    )
    assert res.status_code == 200, res.text
    breach = CP.create_breach(api, owner, description=DESCRIPTION, containment=CONTAINMENT)
    closed = api.call(
        owner,
        "PATCH",
        f"/api/v1/privacy/breaches/{breach['id']}",
        json={"status": "closed", "close_reason": REASON},
        headers={"If-Match": 'W/"1"'},
    )
    assert closed.status_code == 200, closed.text
    api.call(owner, "GET", "/api/v1/privacy/breaches")
    api.call(owner, "GET", f"/api/v1/privacy/breaches/{breach['id']}")
    tasks.notify_all()
    breach_tasks.sweep_all()
    logs = capsys.readouterr()
    text = logs.out + logs.err
    audit = " ".join(str(e["summary"]) for e in W.audit_events(admin_engine, world.a.tenant_id))
    for secret in (NOTE, REQUEST, DESCRIPTION, CONTAINMENT, REASON, "Kothapalli"):
        assert secret not in text, secret
        assert secret not in audit, secret
