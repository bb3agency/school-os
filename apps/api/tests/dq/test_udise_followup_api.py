"""Follow-ups on fixes outside the school (FO-01) and the UDISE+ to-do list (FO-03):
routes, scope, audit and the live closing (FR-UDI-021..FR-UDI-040; invariants 2-7).
"Today" is fixed at 2026-11-09. Synthetic data only."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import sys
import uuid
from typing import Any

import pytest
from sqlalchemy import Engine, text

from app.core.db import tenant_session
from app.dq import calendar_service
from app.students import service as students
from app.students.schemas import ValueIn

pytestmark = pytest.mark.db
DS = sys.modules["sos_test_dq_support"]
SW = sys.modules["sos_test_student_world"]
W = sys.modules["sos_test_api_world"]
SSC = "bseap-ssc-2027"
FOLLOW = f"/api/v1/dq/readiness/{SSC}/follow-ups"
TODO = "/api/v1/dq/udise-todo"
TODAY = dt.date(2026, 11, 9)


@pytest.fixture(autouse=True)
def _setup(monkeypatch: pytest.MonkeyPatch, keyring: None) -> None:
    monkeypatch.setattr(calendar_service, "today_ist", lambda now=None: TODAY)


def _key() -> dict[str, str]:
    return {"Idempotency-Key": str(uuid.uuid4())}


def parent_student(school: Any, *, section_key: str = "section_9a") -> uuid.UUID:
    """A Class IX student whose Aadhaar-as-printed name differs (fix owner: the parent)."""
    name = str(DS.unique_name("Kommineni Venkata")) + " Sai Kumar"
    extra = [
        *DS.aadhaar(
            name=name.replace(" Sai Kumar", " Saikumar"),
            dob="2012-03-14",
            gender="male",
            last4="4821",
        ),
        ValueIn(attribute_key="dob", source="udise_plus", value="2012-03-14"),
        ValueIn(attribute_key="gender", source="udise_plus", value="male"),
        ValueIn(attribute_key="full_name", source="udise_plus", value=name),
    ]
    sid: uuid.UUID = DS.student(school, name=name, section_key=section_key, extra=extra)
    return sid


def item_of(body: dict[str, Any], sid: uuid.UUID, owner: str = "parent_aadhaar") -> Any:
    found = [i for i in body["items"] if i["student"]["id"] == str(sid) and i["fix_owner"] == owner]
    return found[0] if found else None


def step(api: Any, who: Any, *sids: uuid.UUID, **over: Any) -> Any:
    body = {
        "student_ids": [str(s) for s in sids],
        "fix_owner": "parent_aadhaar",
        "event": "slip_given",
        **over,
    }
    return api.call(who, "POST", FOLLOW, json=body, headers=_key())


def audits(admin: Engine, tenant_id: uuid.UUID, action: str) -> list[dict[str, Any]]:
    with admin.connect() as c:
        return [
            dict(r._mapping)
            for r in c.execute(
                text(
                    "SELECT resource_type, resource_id, summary FROM audit.events "
                    "WHERE tenant_id = :t AND action = :a ORDER BY seq"
                ),
                {"t": tenant_id, "a": action},
            )
        ]


# --- FO-01 follow-ups -----------------------------------------------------------------------------


def test_FR_UDI_023_FR_UDI_025_follow_up_states_and_audit(
    world: Any, api: Any, admin_engine: Engine
) -> None:
    sid = parent_student(world.a)
    office = world.person("office_admin")
    res = api.call(office, "GET", FOLLOW)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["overdue_after_days"] == 14
    assert body["today"] == TODAY.isoformat()
    item = item_of(body, sid)
    assert item["state"] == "not_started"
    assert item["readiness_status"] == "needs_parent"
    assert item["steps"] == 0
    parent_totals = next(t for t in body["totals"] if t["fix_owner"] == "parent_aadhaar")
    assert parent_totals["counts"]["not_started"] >= 1

    # One click "slips given" (FR-UDI-026), then a contact by phone.
    res = step(api, office, sid, occurred_on="2026-11-02")
    assert res.status_code == 201, res.text
    assert res.json() == {"recorded": 1}
    item = item_of(api.call(office, "GET", FOLLOW).json(), sid)
    assert (item["state"], item["last_event"], item["last_on"]) == (
        "waiting",
        "slip_given",
        "2026-11-02",
    )

    # A promise that has passed makes it overdue (FR-UDI-024).
    res = step(
        api,
        office,
        sid,
        event="promised_by",
        occurred_on="2026-10-20",
        promised_on="2026-11-05",
    )
    assert res.status_code == 201, res.text
    item = item_of(api.call(office, "GET", FOLLOW, params={"state": "overdue"}).json(), sid)
    assert item["state"] == "overdue"
    assert item["overdue_reason"] == "promise_passed"
    assert item["promised_on"] == "2026-11-05"

    # The parent reports the update: the office now records the value and re-checks.
    res = step(api, office, sid, event="update_reported")
    assert res.status_code == 201, res.text
    item = item_of(api.call(office, "GET", FOLLOW).json(), sid)
    assert item["state"] == "update_reported"

    history = api.call(office, "GET", f"{FOLLOW}/{sid}")
    assert history.status_code == 200, history.text
    events = [s["event"] for s in history.json()["steps"]]
    assert events == ["promised_by", "slip_given", "update_reported"]
    assert history.json()["open"][0]["fix_owner"] == "parent_aadhaar"

    # Audited in the same transaction, ids and codes only (invariants 5, 7).
    rows = audits(admin_engine, world.a.tenant_id, "dq.followup.recorded")
    mine = [r for r in rows if str(sid) in json.dumps(r["summary"])]
    assert len(mine) == 3
    first = mine[0]
    assert first["resource_type"] == "dq_followup"
    assert first["summary"]["event"] == "slip_given"
    assert first["summary"]["count"] == 1
    assert "Kommineni" not in json.dumps(mine, default=str)


def test_FR_UDI_027_steps_are_validated(world: Any, api: Any) -> None:
    sid = parent_student(world.a)
    office = world.person("office_admin")
    cases = [
        ({"event": "parent_contacted"}, "channel_required"),
        ({"event": "slip_given", "channel": "phone"}, "channel_not_allowed"),
        ({"event": "request_sent", "channel": "email"}, "event_not_for_owner"),
        ({"occurred_on": "2026-11-10"}, "future_date"),
        ({"occurred_on": "2026-01-01"}, "too_old"),
        ({"event": "promised_by"}, "required"),
        ({"event": "promised_by", "promised_on": "2026-11-01"}, "before_step"),
        ({"promised_on": "2026-12-01"}, "not_allowed"),
    ]
    for over, code in cases:
        res = step(api, office, sid, **over)
        assert res.status_code == 422, (over, res.text)
        assert code in res.text, (over, res.text)
    # A student whose UDISE+ is fine has no school_udise follow-up to record.
    res = step(api, office, sid, fix_owner="school_udise", event="request_sent", channel="email")
    assert res.status_code == 422
    assert "no_open_fix" in res.text
    # Repeated ids are refused by the schema.
    assert step(api, office, sid, sid).status_code == 422
    ok = step(api, office, sid, event="parent_contacted", channel="whatsapp")
    assert ok.status_code == 201, ok.text


def test_FR_UDI_022_follow_up_closes_when_the_value_matches(world: Any, api: Any) -> None:
    sid = parent_student(world.a)
    office = world.person("office_admin")
    assert step(api, office, sid).status_code == 201
    assert item_of(api.call(office, "GET", FOLLOW).json(), sid) is not None
    with tenant_session(world.a.tenant_id, world.a.people["owner"].user_id) as db:
        name = students.canonical_values(db, [sid], ["full_name"])[sid]["full_name"].value
        assert name is not None
        students.record_value(
            db, SW.admin_ctx(world.a), sid, "aadhaar_name_as_printed", "aadhaar_as_printed", name
        )
    # Nothing was corrected automatically; the follow-up simply has nothing left to wait for.
    assert item_of(api.call(office, "GET", FOLLOW).json(), sid) is None
    history = api.call(office, "GET", f"{FOLLOW}/{sid}").json()
    assert history["open"] == []
    assert [s["event"] for s in history["steps"]] == ["slip_given"]


def test_FR_UDI_029_follow_up_authz_scope_and_tenant(
    world: Any, api: Any, admin_engine: Engine
) -> None:
    sid = parent_student(world.a)
    other_section = parent_student(world.a, section_key="section_9c")
    # Allowed roles read (200); a role without dq.readiness.read gets 403.
    for role in ("office_admin", "exam_coordinator", "principal", "class_teacher"):
        assert api.call(world.person(role), "GET", FOLLOW).status_code == 200, role
    assert api.call(world.person("accountant"), "GET", FOLLOW).status_code == 403
    # Class teachers see their own section only; another section is 404 (SEC-015).
    teacher = world.person("class_teacher")
    body = api.call(teacher, "GET", FOLLOW).json()
    assert item_of(body, other_section) is None
    assert item_of(body, sid) is not None
    res = api.call(teacher, "GET", FOLLOW, params={"section_id": str(world.a.ids["section_9c"])})
    assert res.status_code == 404
    assert api.call(teacher, "GET", f"{FOLLOW}/{other_section}").status_code == 404
    # Recording needs dq.readiness.manage.
    assert step(api, teacher, sid).status_code == 403
    assert step(api, world.person("accountant"), sid).status_code == 403
    # Another school: 404 for the student, never a hint that it exists.
    outsider = world.b.people["owner"]
    manager_b = W.add_member(admin_engine, world.b.tenant_id, ["office_admin"])
    assert step(api, manager_b, sid).status_code == 404
    assert api.call(outsider, "GET", f"{FOLLOW}/{sid}").status_code == 404
    assert item_of(api.call(outsider, "GET", FOLLOW).json(), sid) is None
    # Unknown profiles: 404.
    missing = api.call(world.person("office_admin"), "GET", "/api/v1/dq/readiness/x/follow-ups")
    assert missing.status_code == 404


# --- FO-03 UDISE+ to-do list ----------------------------------------------------------------------


def fresh_school(admin: Engine) -> Any:
    s = W.School(W.provision_school())
    s.people["owner"] = W.add_member(admin, s.tenant_id, ["owner"])
    W.build_structure(s, s.people["owner"])
    for role in ("office_admin", "accountant"):
        s.people[role] = W.add_member(admin, s.tenant_id, [role])
    s.people["class_teacher"] = W.add_member(
        admin, s.tenant_id, ["class_teacher"], scopes=[("section", s.ids["section_9a"])]
    )
    return s


def admitted(s: Any, on: str, *, pen: str | None = None, section_key: str = "section_9a") -> Any:
    extra = [ValueIn(attribute_key="admission_date", source="admission_register", value=on)]
    if pen:
        extra.append(ValueIn(attribute_key="udise_pen", source="tc_incoming", value=pen))
    return SW.create(s, name=DS.unique_name(), section_key=section_key, extra=extra)


def set_left(admin: Engine, s: Any, sid: uuid.UUID) -> None:
    with admin.begin() as c:
        c.execute(
            text("UPDATE sis.students SET status = 'left' WHERE tenant_id = :t AND id = :s"),
            {"t": s.tenant_id, "s": sid},
        )


def issue_tc(admin: Engine, s: Any, sid: uuid.UUID) -> uuid.UUID:
    cid = uuid.uuid4()
    m = s.people["owner"].membership_id
    with admin.begin() as c:
        c.execute(
            text(
                "INSERT INTO sis.certificates (id, tenant_id, student_id, certificate_type, "
                "status, academic_year_id, serial_no, serial, content, content_sha256, "
                "template_version, requested_by, issued_by, issued_at) VALUES (:i, :t, :s, "
                "'transfer', 'issued', :y, 1, 'TC/2026-27/0001', '{}', :sha, 'v1', :m, :m, "
                "TIMESTAMPTZ '2026-10-01 10:00+05:30')"
            ),
            {
                "i": cid,
                "t": s.tenant_id,
                "s": sid,
                "y": s.ids["year"],
                "sha": hashlib.sha256(b"tc").digest(),
                "m": m,
            },
        )
    return cid


def promotion(admin: Engine, s: Any) -> uuid.UUID:
    rid = uuid.uuid4()
    with admin.begin() as c:
        c.execute(
            text(
                "INSERT INTO sis.promotion_runs (id, tenant_id, from_academic_year_id, "
                "to_academic_year_id, promoted_count, held_back_count, graduated_count, "
                "plan_fingerprint, committed_by, committed_at) VALUES (:i, :t, :f, :y, 30, 2, "
                "0, :fp, :u, TIMESTAMPTZ '2026-06-05 10:00+05:30')"
            ),
            {
                "i": rid,
                "t": s.tenant_id,
                "f": s.ids["old_year"],
                "y": s.ids["year"],
                "u": s.people["owner"].user_id,
                "fp": hashlib.sha256(rid.bytes).hexdigest(),
            },
        )
    return rid


def test_FR_UDI_031_FR_UDI_034_todo_items_from_records(api: Any, admin_engine: Engine) -> None:
    s = fresh_school(admin_engine)
    new_child = admitted(s, "2026-10-30")
    with_pen = admitted(s, "2026-07-01", pen="21345678901")
    old = admitted(s, "2019-06-12")
    left = admitted(s, "2018-06-12")
    set_left(admin_engine, s, left)
    transferred = admitted(s, "2017-06-12")
    set_left(admin_engine, s, transferred)
    cid = issue_tc(admin_engine, s, transferred)
    run_id = promotion(admin_engine, s)

    res = api.call(s.people["office_admin"], "GET", TODO)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["verified"] is False
    assert body["source"]
    by_key = {i["item_key"]: i for i in body["items"]}
    assert by_key[f"admission:{new_child}"]["action"] == "admission_without_pen"
    assert by_key[f"admission:{new_child}"]["overdue"] is False
    assert by_key[f"admission:{with_pen}"]["action"] == "admission_with_pen"
    assert by_key[f"admission:{with_pen}"]["overdue"] is True  # waited > 14 days
    assert f"admission:{old}" not in by_key
    assert by_key[f"left_without_tc:{left}"]["kind"] == "left_without_tc"
    tc = by_key[f"transfer_out:{cid}"]
    assert tc["certificate_serial"] == "TC/2026-27/0001"
    assert tc["event_on"] == "2026-10-01"
    assert f"left_without_tc:{transferred}" not in by_key
    prog = by_key[f"progression:{run_id}"]
    assert prog["student"] is None
    assert prog["progression"] == {"promoted": 30, "held_back": 2, "graduated": 0}
    assert body["totals"]["open"] == 5
    assert all(i["state"] == "open" for i in body["items"])
    # Open items, longest waiting first.
    assert body["items"][0]["item_key"] == f"progression:{run_id}"


def test_FR_UDI_035_FR_UDI_036_tick_and_undo(api: Any, admin_engine: Engine) -> None:
    s = fresh_school(admin_engine)
    sid = admitted(s, "2026-10-30")
    key = f"admission:{sid}"
    office = s.people["office_admin"]
    res = api.call(office, "PUT", f"{TODO}/{key}", json={"state": "done"})
    assert res.status_code == 200, res.text
    assert res.json()["state"] == "done"
    assert res.headers["ETag"] == 'W/"1"'
    # Changing a tick needs If-Match.
    again = api.call(office, "PUT", f"{TODO}/{key}", json={"state": "not_needed"})
    assert again.status_code == 400
    stale = api.call(
        office, "PUT", f"{TODO}/{key}", json={"state": "not_needed"}, headers={"If-Match": 'W/"9"'}
    )
    assert stale.status_code == 412
    changed = api.call(
        office, "PUT", f"{TODO}/{key}", json={"state": "not_needed"}, headers={"If-Match": 'W/"1"'}
    )
    assert changed.status_code == 200, changed.text
    assert changed.json()["version"] == 2
    listed = api.call(office, "GET", TODO, params={"state": "not_needed"}).json()
    assert [i["item_key"] for i in listed["items"]] == [key]
    assert listed["totals"]["not_needed"] == 1
    undo = api.call(office, "DELETE", f"{TODO}/{key}", headers={"If-Match": 'W/"2"'})
    assert undo.status_code == 204, undo.text
    reopened = api.call(office, "GET", TODO, params={"state": "open"}).json()
    assert key in {i["item_key"] for i in reopened["items"]}
    assert (
        api.call(office, "DELETE", f"{TODO}/{key}", headers={"If-Match": 'W/"2"'}).status_code
        == 404
    )
    rows = audits(admin_engine, s.tenant_id, "dq.udise_todo.marked")
    assert [r["summary"]["to"] for r in rows] == ["done", "not_needed"]
    assert rows[0]["summary"]["kind"] == "admission"
    assert rows[0]["resource_id"] == sid
    assert len(audits(admin_engine, s.tenant_id, "dq.udise_todo.reopened")) == 1
    # An item that does not exist (or is not derived from a record): 404; a bad key: 422.
    assert (
        api.call(
            office, "PUT", f"{TODO}/admission:{uuid.uuid4()}", json={"state": "done"}
        ).status_code
        == 404
    )
    assert api.call(office, "PUT", f"{TODO}/nope", json={"state": "done"}).status_code == 422


def test_FR_UDI_039_todo_authz_scope_and_tenant(world: Any, api: Any, admin_engine: Engine) -> None:
    s = fresh_school(admin_engine)
    mine = admitted(s, "2026-10-30")
    elsewhere = admitted(s, "2026-10-30", section_key="section_9c")
    run_id = promotion(admin_engine, s)
    assert api.call(s.people["accountant"], "GET", TODO).status_code == 403
    teacher = s.people["class_teacher"]
    res = api.call(teacher, "GET", TODO)
    assert res.status_code == 200, res.text
    keys = {i["item_key"] for i in res.json()["items"]}
    assert f"admission:{mine}" in keys
    assert f"admission:{elsewhere}" not in keys
    assert f"progression:{run_id}" not in keys  # school-wide readers only
    # Ticking needs dq.readiness.manage.
    assert (
        api.call(teacher, "PUT", f"{TODO}/admission:{mine}", json={"state": "done"}).status_code
        == 403
    )
    # Another school never sees or ticks this school's items.
    outsider = W.add_member(admin_engine, world.b.tenant_id, ["office_admin"])
    other = api.call(outsider, "GET", TODO)
    assert other.status_code == 200
    assert f"admission:{mine}" not in {i["item_key"] for i in other.json()["items"]}
    assert (
        api.call(outsider, "PUT", f"{TODO}/admission:{mine}", json={"state": "done"}).status_code
        == 404
    )
    assert (
        api.call(
            outsider, "DELETE", f"{TODO}/progression:{run_id}", headers={"If-Match": 'W/"1"'}
        ).status_code
        == 404
    )
