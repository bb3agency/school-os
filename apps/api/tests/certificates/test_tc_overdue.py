"""Daily overdue TC request reminder (FR-CERT-045; FO-09 follow-up). Synthetic data only.

Holders of ``certificate.approve`` and ``certificate.issue`` get one in-app notification a day
with the overdue count their own list shows (count only); a re-run the same day creates nothing;
one system audit event per school and day.
"""

from __future__ import annotations

import datetime as dt
import json
import sys
import uuid
from typing import Any

import pytest
from sqlalchemy import Engine, text

from app.certificates import tasks, tc_overdue, tc_requests

pytestmark = pytest.mark.db
C = sys.modules["sos_test_certificates_support"]
BASE = "/api/v1/tc-requests"


def _overdue_request(api: Any, school: Any, section: str) -> dict[str, Any]:
    days = tc_requests.settings().overdue_after_days
    res = api.call(
        school.people["office_admin"],
        "POST",
        BASE,
        json={
            "student_id": str(C.student(school, section)),
            "received_on": (C.today() - dt.timedelta(days=days + 2)).isoformat(),
            "channel": "paper",
        },
    )
    assert res.status_code == 201, res.text
    return dict(res.json())


def _overdue_count(api: Any, school: Any, role: str) -> int:
    res = api.call(school.people[role], "GET", BASE, params={"overdue": "true"})
    assert res.status_code == 200, res.text
    return int(res.json()["overdue_count"])


def _notes(admin: Engine, tenant_id: uuid.UUID, day: dt.date) -> dict[uuid.UUID, dict[str, Any]]:
    with admin.connect() as c:
        rows = c.execute(
            text(
                "SELECT recipient_membership_id, params, resource_id FROM ops.notifications "
                "WHERE tenant_id = :t AND template_key = :k AND dedupe_key = :d"
            ),
            {"t": tenant_id, "k": tc_overdue.TEMPLATE, "d": f"{tc_overdue.TEMPLATE}:{day}"},
        )
        out: dict[uuid.UUID, dict[str, Any]] = {}
        for r in rows:
            assert r.recipient_membership_id not in out, "one notification per recipient"
            assert r.resource_id is None
            out[r.recipient_membership_id] = dict(r.params)
        return out


def _audits(admin: Engine, tenant_id: uuid.UUID) -> list[dict[str, Any]]:
    with admin.connect() as c:
        rows = c.execute(
            text(
                "SELECT actor_type, summary FROM audit.events WHERE tenant_id = :t "
                "AND action = :a ORDER BY seq"
            ),
            {"t": tenant_id, "a": tc_overdue.AUDIT_ACTION},
        )
        return [{"actor_type": r.actor_type, **dict(r.summary)} for r in rows]


def test_FR_CERT_045_overdue_count_to_principal_and_office_once_a_day(
    school: Any, api: Any, admin_engine: Engine
) -> None:
    _overdue_request(api, school, "section_10a")
    _overdue_request(api, school, "section_9a")
    today = C.today()
    expected = {
        role: _overdue_count(api, school, role)
        for role in ("owner", "principal", "office_admin", "office_staff", "dual", "scoped_clerk")
    }
    assert expected["scoped_clerk"] < expected["principal"]  # 9A only

    created = tc_overdue.send_reminders(school.tenant_id, day=today)
    notes = _notes(admin_engine, school.tenant_id, today)
    assert created == len(notes)
    days = tc_requests.settings().overdue_after_days
    for role, count in expected.items():
        member = school.people[role].membership_id
        assert notes[member] == {"count": count, "days": days}, role
    # No certificate.approve / certificate.issue: not told.
    for role in ("accountant", "auditor_readonly"):
        assert school.people[role].membership_id not in notes, role
    for params in notes.values():
        assert "Synthetica" not in json.dumps(params)

    audits = _audits(admin_engine, school.tenant_id)
    assert audits[-1] == {"actor_type": "system", "day": today.isoformat(), "notified": created}

    # Idempotent: a second run the same day creates nothing and is not audited again.
    assert tc_overdue.send_reminders(school.tenant_id, day=today) == 0
    assert _notes(admin_engine, school.tenant_id, today) == notes
    assert len(_audits(admin_engine, school.tenant_id)) == len(audits)


def test_FR_CERT_045_nothing_overdue_means_no_notification(
    school: Any, admin_engine: Engine
) -> None:
    long_ago = dt.date(2001, 6, 1)  # every request was received after this day
    before = len(_audits(admin_engine, school.tenant_id))
    assert tc_overdue.send_reminders(school.tenant_id, day=long_ago) == 0
    assert _notes(admin_engine, school.tenant_id, long_ago) == {}
    assert len(_audits(admin_engine, school.tenant_id)) == before


def test_FR_CERT_045_job_runs_every_active_school_and_is_in_the_beat(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[uuid.UUID] = []
    ok, broken = uuid.uuid4(), uuid.uuid4()

    def fake_ids(session: Any, statuses: Any) -> list[uuid.UUID]:
        assert tuple(statuses) == ("active",)
        return [broken, ok]

    def fake_send(tenant_id: uuid.UUID, *, day: dt.date | None = None) -> int:
        seen.append(tenant_id)
        if tenant_id == broken:
            raise RuntimeError("database unavailable")
        return 2

    monkeypatch.setattr(tasks.tenancy, "list_tenant_ids", fake_ids)
    monkeypatch.setattr(tc_overdue, "send_reminders", fake_send)
    assert tasks.tc_overdue_all() == {"tenants": 2, "notified": 2, "failed": 1}
    assert seen == [broken, ok]
    entry = tasks.beat_schedule()["certificates-tc-overdue-reminders"]
    assert entry["task"] == tasks.TC_OVERDUE_TASK == "certificates.tc_overdue_reminders"
