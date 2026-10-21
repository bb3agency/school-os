"""Portal calendar routes and reminders (US-3101..US-3104, FR-RDY-002..010, ADR-0053, D31,
D45; invariants 2, 3, 5, 7). Each test builds its own synthetic school so the calendar, tasks
and notifications it checks are its own. "Today" is fixed at 2026-10-21. Synthetic data only.
"""

from __future__ import annotations

import datetime as dt
import json
import sys
import uuid
from typing import Any

import pytest
from sqlalchemy import Engine, text

from app.core.db import platform_session, tenant_session
from app.dq import calendar_service, portal_tasks
from app.dq import tasks as dq_tasks
from app.tenancy import service as tenancy

pytestmark = pytest.mark.db
W = sys.modules["sos_test_api_world"]
DS = sys.modules["sos_test_dq_support"]
TODAY = dt.date(2026, 10, 21)
BASE = "/api/v1/dq/calendar"
SSC_EVENT = "bseap_ssc_2027_nominal_roll"
CBSE_EVENT = "cbse_registration_2027"
FILE_EVENTS_2026 = {
    "udise_plus_2026_freeze",
    "apaar_2026_generation_drive",
    "cbse_registration_2027",
    "cbse_loc_2027",
    "cisce_registration_2027",
    "bseap_ssc_2027_nominal_roll",
}
ROLES = ("principal", "office_admin", "exam_coordinator", "class_teacher", "teacher", "accountant")


@pytest.fixture(autouse=True)
def _today(monkeypatch: pytest.MonkeyPatch, keyring: None) -> None:
    monkeypatch.setattr(calendar_service, "today_ist", lambda now=None: TODAY)
    monkeypatch.setattr(portal_tasks, "today_ist", lambda now=None: TODAY)


def school(admin: Engine, *, boards: tuple[str, ...] = ()) -> Any:
    """A fresh synthetic school: 2026-27 (current) and 2025-26, classes IX and X, one member per
    role used here; ``boards`` declared when given."""
    s = W.School(W.provision_school())
    owner = W.add_member(admin, s.tenant_id, ["owner"])
    s.people["owner"] = owner
    W.build_structure(s, owner)
    for role in ROLES:
        scopes: list[tuple[str, uuid.UUID | None]] = []
        if role == "class_teacher":
            scopes = [("section", s.ids["section_9a"])]
        elif role == "teacher":
            scopes = [("class", s.ids["class_x"])]
        s.people[role] = W.add_member(admin, s.tenant_id, [role], scopes=scopes)
    if boards:
        with admin.begin() as c:
            c.execute(
                text("UPDATE core.tenants SET boards = CAST(:b AS text[]) WHERE id = :t"),
                {"b": "{" + ",".join(boards) + "}", "t": s.tenant_id},
            )
    return s


def events(api: Any, who: Any, **params: Any) -> dict[str, dict[str, Any]]:
    res = api.call(who, "GET", BASE, params=params)
    assert res.status_code == 200, res.text
    return {e["event_key"]: e for e in res.json()["events"]}


def confirm(
    api: Any, who: Any, s: Any, key: str, *, version: int | None = None, **over: Any
) -> Any:
    body = {
        "academic_year_id": str(s.ids["year"]),
        "opens_on": "2027-02-02",
        "closes_on": "2027-02-04",
        "source_kind": "portal",
        **over,
    }
    headers = {"If-Match": f'W/"{version}"'} if version is not None else {}
    return api.call(who, "PUT", f"{BASE}/{key}", json=body, headers=headers)


def notifications(admin: Engine, tenant_id: uuid.UUID) -> list[dict[str, Any]]:
    with admin.connect() as c:
        return [
            dict(r._mapping)
            for r in c.execute(
                text(
                    "SELECT recipient_membership_id, template_key, params, resource_type, "
                    "resource_id, dedupe_key FROM ops.notifications WHERE tenant_id = :t "
                    "AND template_key = 'portal_calendar.reminder' ORDER BY created_at"
                ),
                {"t": tenant_id},
            )
        ]


def portal_tasks_of(admin: Engine, tenant_id: uuid.UUID) -> list[dict[str, Any]]:
    with admin.connect() as c:
        return [
            dict(r._mapping)
            for r in c.execute(
                text(
                    "SELECT id, title, owner_membership_id, due_on, status, source, "
                    "portal_event_key FROM ops.tasks WHERE tenant_id = :t "
                    "AND source = 'portal_calendar' ORDER BY created_at"
                ),
                {"t": tenant_id},
            )
        ]


def remind(s: Any, day: dt.date) -> int:
    with tenant_session(s.tenant_id) as db:
        return portal_tasks.send_reminders(db, today=day)


# --- the calendar (US-3101, FR-RDY-002, FR-RDY-003) --------------------------------------------


def test_US_3101_AC1_events_apply_by_board_and_class_with_expected_dates(
    admin_engine: Engine, api: Any
) -> None:
    s = school(admin_engine)
    office = s.people["office_admin"]
    listed = events(api, office)
    # No board declared yet: every board's events for its classes IX and X (FR-TEN-020).
    assert set(listed) == FILE_EVENTS_2026
    ssc = listed[SSC_EVENT]
    assert ssc["state"] == "expected"
    assert ssc["confirmed"] is False
    assert ssc["verified"] is False
    assert ssc["sources"]
    assert ssc["expected"] == {
        "opens_on": "2027-02-01",
        "closes_on": "2027-02-05",
        "opens_approximate": True,
        "closes_approximate": True,
    }
    assert ssc["version"] is None
    assert ssc["owner"] is None
    assert [r["profile_key"] for r in ssc["readiness"]] == ["bseap-ssc-2027"]
    # Soonest closing first.
    closes = [e["closes_on"] for e in api.call(office, "GET", BASE).json()["events"]]
    assert closes == sorted(closes)
    # The other academic year has its own events.
    old = events(api, office, academic_year_id=str(s.ids["old_year"]))
    assert set(old) == set()  # no 2025-26 events are shipped

    ap = school(admin_engine, boards=("BSEAP",))
    listed = events(api, ap.people["office_admin"])
    assert SSC_EVENT in listed
    assert not {k for k in listed if k.startswith(("cbse", "cisce"))}
    assert {"udise_plus_2026_freeze", "apaar_2026_generation_drive"} <= set(listed)

    cbse = school(admin_engine, boards=("CBSE",))
    listed = events(api, cbse.people["office_admin"])
    assert {"cbse_registration_2027", "cbse_loc_2027"} <= set(listed)
    assert SSC_EVENT not in listed


def test_FR_RDY_003_readiness_counts_are_the_readers_scope(admin_engine: Engine, api: Any) -> None:
    s = school(admin_engine)
    DS.student(s, section_key="section_9a")
    DS.student(s, section_key="section_10a")
    office = events(api, s.people["office_admin"])[SSC_EVENT]["readiness"][0]
    assert office["students"] == 2
    assert 0 <= office["ready"] <= 2
    teacher = events(api, s.people["class_teacher"])[SSC_EVENT]["readiness"][0]
    assert teacher["students"] == 1  # section 9A only (SEC-015)


# --- confirming and own events (US-3102, FR-RDY-004, FR-RDY-005) -------------------------------


def test_US_3102_AC1_confirm_dates_source_document_and_owner(
    admin_engine: Engine, api: Any
) -> None:
    s = school(admin_engine)
    principal, coordinator = s.people["principal"], s.people["exam_coordinator"]
    res = confirm(api, principal, s, SSC_EVENT, owner_membership_id=str(coordinator.membership_id))
    assert res.status_code == 200, res.text
    body = res.json()
    assert res.headers["ETag"] == 'W/"1"'
    assert body["confirmed"] is True
    assert body["state"] == "confirmed"
    assert (body["opens_on"], body["closes_on"]) == ("2027-02-02", "2027-02-04")
    assert body["source_kind"] == "portal"
    assert body["owner"]["membership_id"] == str(coordinator.membership_id)
    assert body["confirmed_by"]["membership_id"] == str(principal.membership_id)
    assert body["expected"]["closes_on"] == "2027-02-05"  # the file's guess stays visible
    # Changing a confirmation needs If-Match (lost-update guard).
    again = confirm(api, principal, s, SSC_EVENT, closes_on="2027-02-06")
    assert again.status_code == 400
    assert again.json()["code"] == "if_match_required"
    stale = confirm(api, principal, s, SSC_EVENT, version=7, closes_on="2027-02-06")
    assert stale.status_code == 412
    ok = confirm(api, coordinator, s, SSC_EVENT, version=1, closes_on="2027-02-06")
    assert ok.status_code == 200, ok.text
    assert ok.json()["closes_on"] == "2027-02-06"
    assert ok.json()["version"] == 2
    # A confirmed window that has started is open now.
    later = confirm(
        api,
        principal,
        s,
        CBSE_EVENT,
        opens_on="2026-10-01",
        closes_on="2026-10-30",
        source_kind="circular",
    )
    assert later.json()["state"] == "open"
    assert later.json()["days_left"] == 9
    audits = W.audit_events(admin_engine, s.tenant_id, "readiness.calendar.confirmed")
    assert len(audits) == 3
    assert audits[0]["summary"]["event_key"] == SSC_EVENT
    assert audits[0]["summary"]["created"] is True


def test_FR_RDY_004_bad_confirmations_are_refused(admin_engine: Engine, api: Any) -> None:
    s = school(admin_engine)
    principal = s.people["principal"]
    other = W.add_member(admin_engine, s.tenant_id, ["teacher"], status="removed")
    cases: list[tuple[dict[str, Any], int]] = [
        ({"closes_on": "2027-01-01"}, 422),
        ({"source_kind": "rumour"}, 422),
        ({"owner_membership_id": str(other.membership_id)}, 422),
        ({"owner_membership_id": str(uuid.uuid4())}, 422),
        ({"document_id": str(uuid.uuid4())}, 422),
    ]
    for over, status in cases:
        res = confirm(api, principal, s, SSC_EVENT, **over)
        assert res.status_code == status, (over, res.text)
    assert confirm(api, principal, s, "no_such_event").status_code == 404
    ap = school(admin_engine, boards=("BSEAP",))
    # A CBSE window does not concern an AP State Board school.
    assert confirm(api, ap.people["principal"], ap, CBSE_EVENT).status_code == 404


def test_FR_RDY_005_own_event_add_list_and_remove(admin_engine: Engine, api: Any) -> None:
    s = school(admin_engine)
    office = s.people["office_admin"]
    body = {
        "academic_year_id": str(s.ids["year"]),
        "label": "Mandal UDISE+ review meeting",
        "opens_on": "2026-11-10",
        "closes_on": "2026-11-12",
        "profile_keys": ["apaar"],
        "source_kind": "phone",
        "owner_membership_id": str(office.membership_id),
    }
    res = api.call(office, "POST", BASE, json=body)
    assert res.status_code == 201, res.text
    own = res.json()
    key = own["event_key"]
    assert key.startswith("school:mandal-udise-review-meeting-")
    assert own["own"] is True
    assert own["portal"] == "school"
    assert own["label_en"] == "Mandal UDISE+ review meeting"
    assert own["expected"] is None
    assert [r["profile_key"] for r in own["readiness"]] == ["apaar"]
    assert key in events(api, office)
    audit = W.audit_events(admin_engine, s.tenant_id, "readiness.calendar.event_added")
    assert audit[0]["summary"]["event_key"] == key
    assert "Mandal" not in json.dumps(audit[0]["summary"])  # the label is never audited
    # Refused: an Aadhaar-like number, an unknown profile.
    bad = api.call(office, "POST", BASE, json={**body, "label": "Meeting 2345 6789 0124"})
    assert bad.status_code == 422
    bad = api.call(office, "POST", BASE, json={**body, "profile_keys": ["no-such"]})
    assert bad.status_code == 422
    # Remove: If-Match required; then gone.
    year = {"academic_year_id": str(s.ids["year"])}
    assert api.call(office, "DELETE", f"{BASE}/{key}", params=year).status_code == 400
    gone = api.call(office, "DELETE", f"{BASE}/{key}", params=year, headers={"If-Match": 'W/"1"'})
    assert gone.status_code == 204
    assert key not in events(api, office)
    again = api.call(office, "DELETE", f"{BASE}/{key}", params=year, headers={"If-Match": 'W/"1"'})
    assert again.status_code == 404


def test_FR_RDY_004_undoing_a_confirmation_brings_the_expected_dates_back(
    admin_engine: Engine, api: Any
) -> None:
    s = school(admin_engine)
    principal = s.people["principal"]
    assert confirm(api, principal, s, SSC_EVENT).status_code == 200
    year = {"academic_year_id": str(s.ids["year"])}
    res = api.call(
        principal, "DELETE", f"{BASE}/{SSC_EVENT}", params=year, headers={"If-Match": 'W/"1"'}
    )
    assert res.status_code == 204
    ssc = events(api, principal)[SSC_EVENT]
    assert ssc["confirmed"] is False
    assert ssc["closes_on"] == "2027-02-05"
    assert W.audit_events(admin_engine, s.tenant_id, "readiness.calendar.removed")


# --- authorization (invariant 2, SEC-003; other school 404) ------------------------------------


def test_FR_RDY_004_only_calendar_managers_change_it_readers_read(
    admin_engine: Engine, api: Any
) -> None:
    s = school(admin_engine)
    for role in ("principal", "office_admin", "exam_coordinator", "class_teacher"):
        assert api.call(s.people[role], "GET", BASE).status_code == 200, role
    for role in ("teacher", "accountant"):
        assert api.call(s.people[role], "GET", BASE).status_code == 403, role
    for role in ("class_teacher", "teacher", "accountant"):
        who = s.people[role]
        assert confirm(api, who, s, SSC_EVENT).status_code == 403, role
        assert api.call(who, "GET", f"{BASE}/assignees").status_code == 403, role
        body = {
            "academic_year_id": str(s.ids["year"]),
            "label": "Synthetic event",
            "opens_on": "2026-11-10",
            "closes_on": "2026-11-12",
            "source_kind": "other",
        }
        assert api.call(who, "POST", BASE, json=body).status_code == 403, role
    assignees = api.call(s.people["exam_coordinator"], "GET", f"{BASE}/assignees")
    assert assignees.status_code == 200
    assert str(s.people["principal"].membership_id) in {
        a["membership_id"] for a in assignees.json()
    }


def test_SEC_003_other_school_gets_404(admin_engine: Engine, api: Any) -> None:
    s = school(admin_engine)
    other = school(admin_engine)
    principal = s.people["principal"]
    assert confirm(api, principal, s, SSC_EVENT).status_code == 200
    stranger = other.people["principal"]
    # Their own school's calendar, never this one's: our year is unknown to them.
    year = {"academic_year_id": str(s.ids["year"])}
    assert api.call(stranger, "GET", BASE, params=year).status_code == 404
    assert confirm(api, stranger, s, SSC_EVENT, version=1).status_code == 404
    res = api.call(
        stranger, "DELETE", f"{BASE}/{SSC_EVENT}", params=year, headers={"If-Match": 'W/"1"'}
    )
    assert res.status_code == 404
    # Their own calendar does not show our confirmation.
    assert events(api, stranger)[SSC_EVENT]["confirmed"] is False
    # Our owner choice cannot name their staff.
    res = confirm(
        api,
        principal,
        s,
        SSC_EVENT,
        version=1,
        owner_membership_id=str(stranger.membership_id),
    )
    assert res.status_code == 422


# --- next window on readiness (US-3104, FR-RDY-009) --------------------------------------------


def test_US_3104_AC1_readiness_shows_the_next_window(admin_engine: Engine, api: Any) -> None:
    s = school(admin_engine)
    office = s.people["office_admin"]
    res = api.call(office, "GET", "/api/v1/dq/readiness/bseap-ssc-2027")
    assert res.status_code == 200, res.text
    window = res.json()["next_window"]
    assert window["event_key"] == SSC_EVENT
    assert window["state"] == "expected"
    assert window["confirmed"] is False
    assert window["days_left"] == (dt.date(2027, 2, 5) - TODAY).days
    assert confirm(api, s.people["principal"], s, SSC_EVENT).status_code == 200
    overview = api.call(office, "GET", "/api/v1/dq/readiness", params={"profile_keys": ["apaar"]})
    apaar = overview.json()["summaries"][0]["next_window"]
    assert apaar["event_key"] == "udise_plus_2026_freeze"  # closes before the APAAR drive
    ssc = api.call(office, "GET", "/api/v1/dq/readiness/bseap-ssc-2027").json()["next_window"]
    assert ssc["confirmed"] is True
    assert ssc["state"] == "confirmed"
    assert ssc["closes_on"] == "2027-02-04"


# --- linking a circular's task (FR-RDY-010) ----------------------------------------------------


def _task(admin: Engine, s: Any, *, source: str, due: str = "2027-02-03") -> uuid.UUID:
    """A task as M4 stores it; a circular task keeps its citation (the circular itself is not
    needed for the link)."""
    task = uuid.uuid4()
    owner = s.people["office_admin"]
    citation = None
    if source == "circular":
        citation = json.dumps({"source": f"sos://doc/{uuid.uuid4()}/v1#p1", "quote": "synthetic"})
    with admin.begin() as c:
        c.execute(
            text(
                "INSERT INTO ops.tasks (id, tenant_id, title, owner_membership_id, due_on, source, "
                "citation, created_by, created_by_membership) VALUES (:i, :t, 'Synthetic task', "
                ":m, :d, :s, CAST(:c AS jsonb), :u, :m)"
            ),
            {
                "i": task,
                "t": s.tenant_id,
                "m": owner.membership_id,
                "d": due,
                "s": source,
                "c": citation,
                "u": owner.user_id,
            },
        )
    return task


def test_FR_RDY_010_a_circular_task_confirms_the_closing_date(
    admin_engine: Engine, api: Any
) -> None:
    s = school(admin_engine)
    principal = s.people["principal"]
    task = _task(admin_engine, s, source="circular")
    path = f"/api/v1/tasks/{task}/portal-event"
    res = api.call(principal, "POST", path, json={"event_key": SSC_EVENT})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["confirmed"] is True
    assert body["closes_on"] == "2027-02-03"
    assert body["opens_on"] == "2027-02-01"
    assert body["source_kind"] == "circular"
    assert body["owner"]["membership_id"] == str(s.people["office_admin"].membership_id)
    audit = W.audit_events(admin_engine, s.tenant_id, "readiness.calendar.confirmed")
    assert audit[-1]["summary"]["task_id"] == str(task)
    # A manual task was not read from a circular.
    manual = _task(admin_engine, s, source="manual")
    res = api.call(
        principal, "POST", f"/api/v1/tasks/{manual}/portal-event", json={"event_key": SSC_EVENT}
    )
    assert res.status_code == 422
    # The exam coordinator manages the calendar but not tasks.
    res = api.call(s.people["exam_coordinator"], "POST", path, json={"event_key": SSC_EVENT})
    assert res.status_code == 403
    # Another school's task: 404.
    other = school(admin_engine)
    res = api.call(other.people["principal"], "POST", path, json={"event_key": SSC_EVENT})
    assert res.status_code == 404


# --- reminders (US-3103, FR-RDY-006..008, D45, D31) --------------------------------------------


def test_US_3103_AC1_one_task_and_counts_only_notifications_per_lead_day(
    admin_engine: Engine, api: Any
) -> None:
    s = school(admin_engine, boards=("BSEAP",))
    DS.student(s, section_key="section_10a")
    principal, coordinator = s.people["principal"], s.people["exam_coordinator"]
    owner = str(coordinator.membership_id)
    assert confirm(api, principal, s, SSC_EVENT, owner_membership_id=owner).status_code == 200
    closes = dt.date(2027, 2, 4)
    messages_before = _messages(admin_engine, s.tenant_id)

    assert remind(s, closes - dt.timedelta(days=31)) == 0  # before the first lead day
    first = remind(s, closes - dt.timedelta(days=14))
    assert first > 0
    tasks = portal_tasks_of(admin_engine, s.tenant_id)
    assert len(tasks) == 1
    task = tasks[0]
    assert task["portal_event_key"] == SSC_EVENT
    assert task["due_on"] == closes
    assert str(task["owner_membership_id"]) == owner
    assert task["title"] == "AP SSC March 2027 nominal roll corrections"
    sent = notifications(admin_engine, s.tenant_id)
    recipients = {str(n["recipient_membership_id"]) for n in sent}
    # The owner and the holders of dq.readiness.manage; each once.
    assert owner in recipients
    assert str(s.people["office_admin"].membership_id) in recipients
    assert str(s.people["accountant"].membership_id) not in recipients
    assert len(recipients) == len(sent)
    for n in sent:
        params = n["params"]
        assert set(params) == {"portal", "days", "ready", "students"}  # counts only
        assert params["portal"] == "bseap"
        assert params["days"] == 14
        assert params["students"] == 1
        assert n["resource_type"] == "portal_calendar"
        assert n["resource_id"] == s.ids["year"]

    # A rerun the same day (or the next) creates nothing new.
    assert remind(s, closes - dt.timedelta(days=14)) == 0
    assert remind(s, closes - dt.timedelta(days=13)) == 0
    assert len(notifications(admin_engine, s.tenant_id)) == len(sent)

    # The 7-day step moves the same task (one task per event) to the new closing date.
    year = str(s.ids["year"])
    later = confirm(
        api, principal, s, SSC_EVENT, version=1, closes_on="2027-02-05", owner_membership_id=owner
    )
    assert later.status_code == 200, later.text
    assert remind(s, dt.date(2027, 2, 5) - dt.timedelta(days=7)) > 0
    tasks = portal_tasks_of(admin_engine, s.tenant_id)
    assert [t["id"] for t in tasks] == [task["id"]]
    assert tasks[0]["due_on"] == dt.date(2027, 2, 5)
    with admin_engine.connect() as c:
        leads: list[int] = list(
            c.execute(
                text(
                    "SELECT lead_day FROM ops.portal_reminders WHERE tenant_id = :t "
                    "AND event_key = :k AND academic_year_id = :y ORDER BY lead_day DESC"
                ),
                {"t": s.tenant_id, "k": SSC_EVENT, "y": year},
            )
            .scalars()
            .all()
        )
    assert leads == [14, 7]
    # Never to parents (D45): no WhatsApp message was queued.
    assert _messages(admin_engine, s.tenant_id) == messages_before
    assert W.audit_events(admin_engine, s.tenant_id, "readiness.calendar.reminded")
    # The owner sees the task in their list.
    mine = api.call(coordinator, "GET", "/api/v1/tasks")
    assert mine.status_code == 200
    row = next(t for t in mine.json()["data"] if t["id"] == str(task["id"]))
    assert row["source"] == "portal_calendar"
    assert row["portal_event_key"] == SSC_EVENT


def _messages(admin: Engine, tenant_id: uuid.UUID) -> int:
    with admin.connect() as c:
        return int(
            c.execute(
                text("SELECT count(*) FROM msg.messages WHERE tenant_id = :t"), {"t": tenant_id}
            ).scalar_one()
        )


def test_FR_RDY_006_without_an_owner_the_calendar_managers_are_told(
    admin_engine: Engine,
) -> None:
    s = school(admin_engine, boards=("CBSE",))
    # cbse_registration_2027 is expected to close on 2026-10-31: 14 days before is 2026-10-17.
    assert remind(s, dt.date(2026, 10, 17)) > 0
    assert portal_tasks_of(admin_engine, s.tenant_id) == []  # no owner, no task
    sent = notifications(admin_engine, s.tenant_id)
    recipients = {n["recipient_membership_id"] for n in sent}
    for role in ("principal", "office_admin", "exam_coordinator"):
        assert s.people[role].membership_id in recipients, role
    assert all(n["params"]["students"] == 0 for n in sent)  # CBSE profiles: no readiness check


def test_D31_a_suspended_school_gets_no_reminders(
    admin_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    active = school(admin_engine, boards=("CBSE",))
    paused = school(admin_engine, boards=("CBSE",))
    with platform_session() as pdb:
        tenancy.suspend_tenant(pdb, paused.tenant_id)
    mine = {active.tenant_id, paused.tenant_id}

    class _Tenancy:
        @staticmethod
        def list_tenant_ids(session: Any, statuses: Any) -> list[uuid.UUID]:
            return [t for t in tenancy.list_tenant_ids(session, statuses) if t in mine]

    monkeypatch.setattr(dq_tasks, "tenancy", _Tenancy)
    totals = dq_tasks.remind_all(today=dt.date(2026, 10, 17))
    assert totals["tenants"] == 1
    assert totals["failed"] == 0
    assert notifications(admin_engine, active.tenant_id)
    assert notifications(admin_engine, paused.tenant_id) == []
    with admin_engine.connect() as c:
        count: int = c.execute(
            text("SELECT count(*) FROM ops.portal_reminders WHERE tenant_id = :t"),
            {"t": paused.tenant_id},
        ).scalar_one()
    assert count == 0


# --- logs (invariant 5) ------------------------------------------------------------------------


def test_SEC_008_own_event_labels_are_not_logged(
    admin_engine: Engine, api: Any, capsys: pytest.CaptureFixture[str]
) -> None:
    s = school(admin_engine)
    office = s.people["office_admin"]
    label = "Synthetica Ravikiran parents meeting"
    capsys.readouterr()
    res = api.call(
        office,
        "POST",
        BASE,
        json={
            "academic_year_id": str(s.ids["year"]),
            "label": label,
            "opens_on": "2026-10-25",
            "closes_on": "2026-10-28",
            "source_kind": "other",
            "owner_membership_id": str(office.membership_id),
        },
    )
    assert res.status_code == 201, res.text
    events(api, office)
    remind(s, dt.date(2026, 10, 27))
    out = capsys.readouterr()
    assert "Ravikiran" not in out.out + out.err
