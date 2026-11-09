"""Weekly management brief over HTTP and the Monday job (FA-05; FR-BRF-001..FR-BRF-015; lead
decision D85). Synthetic data only.

``brief.read`` (owner, principal) reads; other roles get 403; another school's stored week is
404. The Monday job stores the week once, notifies the holders who did not turn the notice off,
and audits in the same transaction. Preferences are each member's own, audited, with If-Match.
"""

from __future__ import annotations

import datetime as dt
import sys
import uuid
from typing import Any

import pytest
from sqlalchemy import Engine, text

from app.audit import service as audit_service
from app.brief import service, tasks
from app.core.db import tenant_session

pytestmark = pytest.mark.db
W = sys.modules["sos_test_api_world"]
BASE = "/api/v1/brief"
MONDAY = dt.date(2026, 11, 9)


def _build(school: Any, today: dt.date) -> int:
    with tenant_session(school.tenant_id) as db:
        return service.build_week(db, today=today)


def _notices(admin: Engine, tenant_id: uuid.UUID) -> list[dict[str, Any]]:
    with admin.connect() as c:
        rows = c.execute(
            text(
                "SELECT recipient_membership_id, params, resource_id, dedupe_key "
                "FROM ops.notifications WHERE tenant_id = :t AND template_key = :k"
            ),
            {"t": tenant_id, "k": service.TEMPLATE},
        )
        return [dict(r._mapping) for r in rows]


def test_FR_BRF_002_only_owner_and_principal_read_the_brief(school: Any, api: Any) -> None:
    for who in (school.owner, school.principal):
        res = api.call(who, "GET", f"{BASE}/weekly")
        assert res.status_code == 200, res.text
    for who in (school.office_admin, school.teacher):
        assert api.call(who, "GET", f"{BASE}/weekly").status_code == 403
        assert api.call(who, "GET", f"{BASE}/preferences").status_code == 403
        assert api.call(who, "GET", f"{BASE}/weekly/history").status_code == 403


def test_FR_BRF_013_before_the_first_job_a_live_preview_is_shown(
    fresh_school: Any, admin_engine: Engine, api: Any
) -> None:
    res = api.call(fresh_school.owner, "GET", f"{BASE}/weekly")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["stored"] is False
    assert body["id"] is None
    assert body["small_cell_min"] == 5
    assert body["findings"] == {"blockers": 0, "warnings": 0, "students_with_blockers": 0}
    assert body["changes_waiting"] == 0
    assert body["certificates_issued"] == 0
    assert body["fees"] is None  # fees are off for a new school
    assert body["ask"]["answers"] == 0
    with admin_engine.connect() as c:
        stored: int = c.execute(
            text("SELECT count(*) FROM ops.weekly_briefs WHERE tenant_id = :t"),
            {"t": fresh_school.tenant_id},
        ).scalar_one()
    assert stored == 0


def test_FR_BRF_010_the_monday_job_stores_once_notifies_and_audits(
    school: Any, other: Any, api: Any, admin_engine: Engine
) -> None:
    assert _build(school, MONDAY) == 2  # owner and principal; nobody else holds brief.read
    assert _build(school, MONDAY) == 0  # the week is stored already
    notices = _notices(admin_engine, school.tenant_id)
    assert {n["recipient_membership_id"] for n in notices} == {
        school.owner.membership_id,
        school.principal.membership_id,
    }
    # No counts in the notice: it says no more than the reader's page (FR-BRF-010).
    assert notices[0]["params"] == {}
    brief_id = notices[0]["resource_id"]
    (event,) = W.audit_events(admin_engine, school.tenant_id, service.BUILT_EVENT)
    assert event["resource_id"] == brief_id
    assert event["summary"]["week_start"] == "2026-11-02"
    assert event["summary"]["notified"] == 2

    latest = api.call(school.owner, "GET", f"{BASE}/weekly").json()
    assert latest["stored"] is True
    assert latest["id"] == str(brief_id)
    assert latest["week_start"] == "2026-11-02"
    assert latest["week_end"] == "2026-11-08"
    one = api.call(school.principal, "GET", f"{BASE}/weekly/{brief_id}")
    assert one.status_code == 200, one.text
    history = api.call(school.owner, "GET", f"{BASE}/weekly/history").json()
    assert [h["id"] for h in history["items"]] == [str(brief_id)]

    # Another school's owner holds brief.read in their own school only: 404, not 403.
    assert api.call(other.owner, "GET", f"{BASE}/weekly/{brief_id}").status_code == 404
    assert api.call(school.owner, "GET", f"{BASE}/weekly/{uuid.uuid4()}").status_code == 404


def test_FR_BRF_011_preferences_are_own_audited_and_honour_if_match(
    school: Any, api: Any, admin_engine: Engine
) -> None:
    who = school.principal
    first = api.call(who, "GET", f"{BASE}/preferences")
    assert first.status_code == 200
    assert first.json() == {
        "in_app": True,
        "email": False,
        "email_available": first.json()["email_available"],
        "version": 0,
    }
    assert first.headers["ETag"] == 'W/"0"'
    body = {"in_app": False, "email": False}
    saved = api.call(who, "PUT", f"{BASE}/preferences", json=body, headers={"If-Match": 'W/"0"'})
    assert saved.status_code == 200, saved.text
    assert saved.json()["version"] == 1
    stale = api.call(who, "PUT", f"{BASE}/preferences", json=body, headers={"If-Match": 'W/"0"'})
    assert stale.status_code == 412
    events = [
        e
        for e in W.audit_events(admin_engine, school.tenant_id, service.PREFS_EVENT)
        if e["resource_id"] == who.membership_id
    ]
    assert events[-1]["summary"] == {"in_app": False, "digest_message": False}
    # The owner's choice is untouched.
    assert api.call(school.owner, "GET", f"{BASE}/preferences").json()["version"] == 0
    extra = api.call(who, "PUT", f"{BASE}/preferences", json={**body, "member": "x"})
    assert extra.status_code == 422

    # The next week's notice skips the principal, who turned it off.
    assert _build(school, MONDAY + dt.timedelta(days=7)) == 1
    week2 = [n for n in _notices(admin_engine, school.tenant_id) if n["dedupe_key"].endswith("09")]
    assert [n["recipient_membership_id"] for n in week2] == [school.owner.membership_id]
    latest = api.call(school.owner, "GET", f"{BASE}/weekly").json()
    assert latest["previous_week_start"] == "2026-11-02"


def test_FR_BRF_011_preference_and_audit_commit_together(
    school: Any, api: Any, admin_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*_a: Any, **_k: Any) -> None:
        raise RuntimeError("audit down")

    monkeypatch.setattr(audit_service, "record", boom)
    with pytest.raises(RuntimeError):
        api.call(school.owner, "PUT", f"{BASE}/preferences", json={"in_app": True, "email": True})
    monkeypatch.undo()
    assert api.call(school.owner, "GET", f"{BASE}/preferences").json()["version"] == 0


def test_FR_BRF_010_job_loop_survives_a_failing_school(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[int] = []

    def flaky(_session: Any, *, today: dt.date | None = None) -> int:
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("synthetic failure")
        return 0

    monkeypatch.setattr(service, "build_week", flaky)
    totals = tasks.build_all(today=MONDAY)
    assert totals["failed"] == 1
    assert totals["tenants"] == len(calls)


def test_SEC_008_brief_logs_and_audit_hold_no_names(
    fresh_school: Any, admin_engine: Engine, api: Any, capsys: pytest.CaptureFixture[str]
) -> None:
    s = fresh_school
    capsys.readouterr()
    api.call(s.owner, "GET", f"{BASE}/weekly")
    _build(s, MONDAY)
    api.call(s.owner, "PUT", f"{BASE}/preferences", json={"in_app": True, "email": True})
    out = capsys.readouterr()
    for name in (s.owner.display_name, s.principal.display_name):
        assert name not in out.out + out.err
    for event in W.audit_events(admin_engine, s.tenant_id):
        assert s.owner.display_name not in str(event["summary"])
