"""The school-side pull of SchoolOS incident notices logs IDs and counts only (R3-1B; ADR-0050
decision 2; FR-DPDP-032; invariant 5, SEC-008). Synthetic data only.

``app.privacy.breach_sync.sync_school`` (run for every school by the ``privacy.breach_sweep``
beat task) stores a confirmed incident in the school's breach register, notifies the owner and
principal and reports the pull to the control plane. None of its log lines may carry the
incident summary, the school's name, staff or operator names, e-mail addresses or any run of six
or more digits (the affected-people count, phone- or Aadhaar-like numbers).
"""

from __future__ import annotations

import logging
import re
import sys
import uuid
from typing import Any

import pytest
from sqlalchemy import Engine, text

from app.core.db import platform_session
from app.privacy import breach_sync

pytestmark = pytest.mark.db
W = sys.modules["sos_test_api_world"]

SUMMARY = "Kothapalli Synthetica fee register copied to an unlisted share by Vemuri"
APPROX_PEOPLE = 654321
OPERATOR_NAME = "Synthetica Vemuri Operator"
UUID_RE = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
SIX_DIGITS = re.compile(r"(?<!\d)\d{6,}(?!\d)")
EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")


def _confirmed_incident(tenant_id: uuid.UUID) -> tuple[uuid.UUID, list[str]]:
    """A P1 incident affecting ``tenant_id``, confirmed by two synthetic operators (written as
    sos_platform, like the control plane). Returns the incident id and the operator e-mails."""
    ops = [uuid.uuid4(), uuid.uuid4()]
    emails = [f"vemuri.{oid.hex[:10]}@example.test" for oid in ops]
    incident = uuid.uuid4()
    with platform_session() as s:
        for oid, email in zip(ops, emails, strict=True):
            s.execute(
                text(
                    "INSERT INTO platform.operators (id, idp_subject, email, display_name, status, "
                    "mfa_enrolled) VALUES (:i, :s, :e, :n, 'active', true)"
                ),
                {"i": oid, "s": f"op-{oid}", "e": email, "n": OPERATOR_NAME},
            )
        s.execute(
            text(
                "INSERT INTO platform.incidents (id, opened_at, severity, summary, "
                "cert_in_required, lead_operator_id, created_by, confirmed_by, "
                "first_confirmed_at, second_confirmed_by, confirmed_at) VALUES (:i, "
                "now() - interval '3 hours', 'P1', :summary, true, :a, :a, :a, now(), :b, now())"
            ),
            {"i": incident, "a": ops[0], "b": ops[1], "summary": SUMMARY},
        )
        s.execute(
            text(
                "INSERT INTO platform.incident_schools (id, incident_id, tenant_id, categories, "
                "approx_people, window_from, window_to) VALUES (:i, :n, :t, "
                "ARRAY['financial','contact'], :p, now() - interval '2 days', now())"
            ),
            {"i": uuid.uuid4(), "n": incident, "t": tenant_id, "p": APPROX_PEOPLE},
        )
    return incident, emails


def _school_texts(admin: Engine, tenant_id: uuid.UUID) -> tuple[str, list[str], list[str]]:
    """The school's name and its members' names and e-mails (all synthetic)."""
    with admin.connect() as c:
        name: str = c.execute(
            text("SELECT name FROM core.tenants WHERE id = :t"), {"t": tenant_id}
        ).scalar_one()
        people = c.execute(
            text(
                "SELECT u.display_name, u.email FROM core.users u JOIN core.memberships m "
                "ON m.user_id = u.id WHERE m.tenant_id = :t"
            ),
            {"t": tenant_id},
        ).all()
    return (
        name,
        [p.display_name for p in people if p.display_name],
        [p.email for p in people if p.email],
    )


def test_SEC_008_breach_sync_logs_no_summary_names_emails_or_numbers(
    school: Any,
    admin_engine: Engine,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    tenant_id = school.tenant_id
    incident, operator_emails = _confirmed_incident(tenant_id)
    school_name, staff_names, staff_emails = _school_texts(admin_engine, tenant_id)
    assert staff_names, "the synthetic school has members to leak"
    capsys.readouterr()
    caplog.clear()
    with caplog.at_level(logging.DEBUG):
        assert breach_sync.sync_school(tenant_id) >= 1
        assert breach_sync.sync_school(tenant_id) == 0  # the idempotent second pass logs too
    out = capsys.readouterr()
    logged = "\n".join([out.out, out.err, caplog.text, *(str(r.msg) for r in caplog.records)])
    assert "privacy.breach.notices_received" in logged, "the pull's log line was captured"
    with admin_engine.connect() as c:
        stored: int = c.execute(
            text("SELECT count(*) FROM ops.breach_records WHERE platform_incident_id = :i"),
            {"i": incident},
        ).scalar_one()
    assert stored == 1
    for secret in (SUMMARY, "Kothapalli", "Vemuri", OPERATOR_NAME, school_name, *staff_names):
        assert secret not in logged, secret
    for email in (*operator_emails, *staff_emails):
        assert email not in logged, email
    without_ids = UUID_RE.sub(" ", logged)
    assert EMAIL.findall(without_ids) == []
    assert SIX_DIGITS.findall(without_ids) == []
    assert str(APPROX_PEOPLE) not in logged
