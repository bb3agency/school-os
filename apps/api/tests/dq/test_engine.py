"""DQ engine and findings workflow on the real database (FR-DQ-002, FR-DQ-004, FR-DQ-006,
FR-DQ-020, US-501, US-502). Synthetic data only."""

from __future__ import annotations

import datetime as dt
import sys
import uuid
from typing import Any

import pytest
from sqlalchemy import Engine, text

from app.core.db import tenant_session
from app.core.errors import Conflict, NotFound, PreconditionFailed, StepUpRequired
from app.dq import service as dq
from app.dq.schemas import FindingFilters, ResolveIn, WaiveIn
from app.students import service as students
from app.students.schemas import EnrollmentIn
from app.tenancy import service as tenancy
from app.tenancy.schemas import SectionCreate

pytestmark = pytest.mark.db
DS = sys.modules["sos_test_dq_support"]
W = DS.SW.W
SECRET_NAME = "Pinnamaneni Lakshmana Chaitanya"


@pytest.fixture(autouse=True)
def _keys(keyring: None) -> None:
    return None


def _audit(admin: Engine, tenant_id: uuid.UUID, action: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = W.audit_events(admin, tenant_id, action)
    return events


# --- DQ-001 end to end, masking -------------------------------------------------------------------


def test_FR_DQ_006_DQ_001_finding_stores_masked_values_only(
    world: Any, admin_engine: Engine
) -> None:
    sid = DS.student(world.a, extra=DS.aadhaar(name=SECRET_NAME))
    out = DS.run(world.a, sid)
    assert out.status == "completed"
    assert out.stats["students"] == 1
    assert out.stats["new"] >= 1
    row = DS.one(admin_engine, sid, "DQ-001")
    assert (row["severity"], row["match_class"], row["status"]) == ("blocker", "DIFFERENT", "open")
    assert row["sources"] == ["aadhaar_as_printed", "admission_register"]
    assert row["route_codes"] == ["ROUTE-UIDAI", "ROUTE-SCHOOL-CR"]
    with admin_engine.connect() as c:
        dumped: str = c.execute(
            text(
                "SELECT string_agg(details::text || explanation_params::text, ' ') "
                "FROM sis.dq_findings WHERE student_id = :s"
            ),
            {"s": sid},
        ).scalar_one()
        stats: str = c.execute(
            text("SELECT stats::text || scope::text FROM sis.dq_runs WHERE id = :r"),
            {"r": out.id},
        ).scalar_one()
    for word in SECRET_NAME.split():
        assert word.upper() not in (dumped + stats).upper()
    assert "P••• L••• C•••" in dumped


def test_US_501_api_shows_masked_c3_and_current_c2_values(world: Any) -> None:
    name = DS.unique_name()
    sid = DS.student(world.a, name=name, extra=DS.aadhaar(name=SECRET_NAME))
    DS.run(world.a, sid)
    page = DS.call(world.a, dq.list_findings, FindingFilters(student_id=sid, rule_id=["DQ-001"]))
    (finding,) = page.data
    values = {v.source: v for v in finding.values}
    assert values["aadhaar_as_printed"].value is None
    assert values["aadhaar_as_printed"].sensitive is True
    assert values["aadhaar_as_printed"].masked == "P••• L••• C•••"
    assert values["admission_register"].value == name
    assert finding.explanation.en == "Name differs between admission register and Aadhaar."
    assert "ఆధార్" in finding.explanation.te
    assert finding.match_explanation is not None
    assert finding.match_explanation.code == "NM-DIFFERENT"
    assert [r.code for r in finding.routes] == ["ROUTE-UIDAI", "ROUTE-SCHOOL-CR"]
    assert finding.student.display_name == name
    assert finding.blocker is True
    assert SECRET_NAME.split(maxsplit=1)[0] not in finding.model_dump_json()


# --- idempotency, auto-clear, reopen (FR-DQ-004, US-502 AC2) --------------------------------------


def test_FR_DQ_004_rerun_is_idempotent(world: Any, admin_engine: Engine) -> None:
    sid = DS.student(world.a, extra=DS.aadhaar(gender="female"))
    DS.run(world.a, sid)
    first = DS.findings(admin_engine, sid)
    second_run = DS.run(world.a, sid)
    second = DS.findings(admin_engine, sid)
    assert [r["id"] for r in first] == [r["id"] for r in second]
    assert [r["version"] for r in first] == [r["version"] for r in second]
    assert second_run.stats["new"] == 0
    assert second_run.stats["unchanged"] == len(first)
    assert {r["last_seen_run_id"] for r in second} == {second_run.id}


def test_FR_DQ_004_conflict_gone_is_auto_cleared_and_returns_reopened(
    world: Any, admin_engine: Engine
) -> None:
    sid = DS.student(world.a, extra=DS.aadhaar(gender="female"))
    DS.run(world.a, sid)
    assert DS.one(admin_engine, sid, "DQ-003")["status"] == "open"
    DS.call(
        world.a,
        students.record_value,
        sid,
        "aadhaar_gender_as_printed",
        "aadhaar_as_printed",
        "male",
    )
    cleared = DS.run(world.a, sid)
    row = DS.one(admin_engine, sid, "DQ-003")
    assert (row["status"], row["resolution"]) == ("resolved", "auto_cleared")
    assert cleared.stats["cleared"] == 1
    DS.call(
        world.a,
        students.record_value,
        sid,
        "aadhaar_gender_as_printed",
        "aadhaar_as_printed",
        "female",
    )
    back = DS.run(world.a, sid)
    row = DS.one(admin_engine, sid, "DQ-003")
    assert (row["status"], row["reopened_count"]) == ("reopened", 1)
    assert back.stats["reopened"] == 1


def test_US_502_resolved_with_note_reopens_when_the_conflict_is_still_there(
    world: Any, admin_engine: Engine
) -> None:
    fid = DS.high_finding(world.a)
    out = DS.call(world.a, dq.resolve_finding, fid, ResolveIn(note="Parent will correct Aadhaar"))
    assert (out.status, out.resolution, out.resolution_note) == (
        "resolved",
        "note",
        "Parent will correct Aadhaar",
    )
    (event,) = [
        e
        for e in _audit(admin_engine, world.a.tenant_id, "dq.finding.resolved")
        if e["resource_id"] == fid
    ]
    assert event["summary"]["has_note"] is True
    assert "Parent" not in str(event["summary"])
    DS.run(world.a, out.student.id)
    row = DS.one(admin_engine, out.student.id, "DQ-003")
    assert (row["id"], row["status"], row["reopened_count"]) == (fid, "reopened", 1)


def test_US_502_waiver_holds_for_the_same_conflict_only(world: Any, admin_engine: Engine) -> None:
    fid = DS.high_finding(world.a)
    out = DS.call(world.a, dq.waive_finding, fid, WaiveIn(reason="Board accepts either form"))
    sid = out.student.id
    assert out.status == "waived"
    DS.run(world.a, sid)
    assert DS.one(admin_engine, sid, "DQ-003")["status"] == "waived"
    DS.call(
        world.a,
        students.record_value,
        sid,
        "aadhaar_gender_as_printed",
        "aadhaar_as_printed",
        "transgender",
    )
    DS.run(world.a, sid)
    row = DS.one(admin_engine, sid, "DQ-003")
    assert row["status"] == "reopened"
    assert row["waived_reason"] == "Board accepts either form"


def test_FR_DQ_020_resolve_and_waive_rules(world: Any) -> None:
    fid = DS.high_finding(world.a)
    with pytest.raises(PreconditionFailed):
        DS.call(world.a, dq.resolve_finding, fid, ResolveIn(note="x"), expected_version=99)
    cr = uuid.uuid4()
    out = DS.call(world.a, dq.resolve_finding, fid, ResolveIn(change_request_id=cr))
    assert (out.resolution, out.change_request_id) == ("change_request", cr)
    with pytest.raises(Conflict):
        DS.call(world.a, dq.resolve_finding, fid, ResolveIn(note="again"))
    with pytest.raises(Conflict):
        DS.call(world.a, dq.waive_finding, fid, WaiveIn(reason="too late"))
    with pytest.raises(ValueError, match="note or link"):
        ResolveIn()


def test_FR_DQ_020_blocker_waiver_needs_step_up(world: Any, admin_engine: Engine) -> None:
    fid = DS.blocker_finding(world.a)
    principal = DS.ctx(world.a, "principal")
    stale = type(principal)(
        **{
            **{f: getattr(principal, f) for f in principal.__dataclass_fields__},
            "auth_time": dt.datetime.now(dt.UTC) - dt.timedelta(minutes=10),
        }
    )
    with pytest.raises(StepUpRequired):
        DS.call(world.a, dq.waive_finding, fid, WaiveIn(reason="Accepted by board"), as_ctx=stale)
    fresh = type(principal)(
        **{
            **{f: getattr(principal, f) for f in principal.__dataclass_fields__},
            "auth_time": dt.datetime.now(dt.UTC),
        }
    )
    out = DS.call(world.a, dq.waive_finding, fid, WaiveIn(reason="Accepted by board"), as_ctx=fresh)
    assert out.status == "waived"
    (event,) = [
        e
        for e in _audit(admin_engine, world.a.tenant_id, "dq.finding.waived")
        if e["resource_id"] == fid
    ]
    assert event["summary"]["step_up"] is True
    assert event["summary"]["severity"] == "blocker"


# --- profiles (DQ-005, DQ-006, DQ-009) ------------------------------------------------------------


def test_US_501_profile_run_adds_profile_findings_only_for_that_profile(
    world: Any, admin_engine: Engine
) -> None:
    sid = DS.student(world.a, admission_no=f"DQP{uuid.uuid4().hex[:6]}")
    DS.run(world.a, sid, profile_key="udise-plus")
    rows = DS.findings(admin_engine, sid)
    dq5 = {
        (r["attribute_key"], r["severity"], r["explanation_code"])
        for r in rows
        if r["rule_id"] == "DQ-005"
    }
    assert ("mother_tongue", "blocker", "DQ-005") in dq5
    assert ("category", "blocker", "DQ-005") in dq5
    assert ("full_name", "low", "DQ-005-UNVERIFIED") in dq5  # register values are unverified
    assert {r["profile_key"] for r in rows if r["rule_id"] in ("DQ-005", "DQ-009")} == {
        "udise-plus"
    }
    # A run without a profile leaves the profile findings alone.
    DS.run(world.a, sid)
    assert {r["status"] for r in DS.findings(admin_engine, sid) if r["profile_key"]} == {"open"}
    # An incremental run re-checks the profiles in use: adding the value clears its finding.
    DS.call(world.a, students.record_value, sid, "mother_tongue", "parent_form", "Telugu")
    (event,) = [
        p
        for p in DS.outbox(admin_engine, world.a.tenant_id, "student.values.changed")
        if p["student_ids"] == [str(sid)] and p["attribute_keys"] == ["mother_tongue"]
    ]
    run = dq.run_for_event(world.a.tenant_id, event)
    assert run is not None
    assert run.trigger == "event"
    mt = [
        r for r in DS.findings(admin_engine, sid, "DQ-005") if r["attribute_key"] == "mother_tongue"
    ]
    assert [(r["status"], r["resolution"]) for r in mt] == [("resolved", "auto_cleared")]


def test_US_501_summary_separates_blockers_from_warnings(world: Any) -> None:
    section = tenancy_section(world)
    sid = DS.student(world.a, section_key=None, extra=DS.aadhaar(name=SECRET_NAME, gender="female"))
    DS.call(world.a, students.enrol, sid, EnrollmentIn(section_id=section))
    DS.run(world.a, sid)
    out = DS.call(world.a, dq.summary, section_ids=[section])
    assert out.blockers == 1  # DQ-001 DIFFERENT
    assert out.warnings == 1  # DQ-003 high
    assert out.students_with_blockers == 1
    assert out.by_severity["blocker"] == 1
    assert {(r.rule_id, r.severity) for r in out.by_rule} == {
        ("DQ-001", "blocker"),
        ("DQ-003", "high"),
    }


def tenancy_section(world: Any) -> uuid.UUID:
    with tenant_session(world.a.tenant_id) as db:
        section = tenancy.create_section(
            db,
            SectionCreate(
                academic_year_id=world.a.ids["year"],
                class_id=world.a.ids["class_ix"],
                name=f"S{uuid.uuid4().hex[:6].upper()}",
            ),
        )
    return section.id


# --- DQ-007, DQ-008, DQ-012 on real enrolments ----------------------------------------------------


def test_DQ_012_and_DQ_007_from_enrolments(world: Any, admin_engine: Engine) -> None:
    sid = DS.student(world.a)
    with tenant_session(world.a.tenant_id) as db:
        old = tenancy.create_section(
            db,
            SectionCreate(
                academic_year_id=world.a.ids["old_year"],
                class_id=world.a.ids["class_x"],
                name=f"O{uuid.uuid4().hex[:6].upper()}",
            ),
        )
    DS.call(world.a, students.enrol, sid, EnrollmentIn(section_id=old.id))
    DS.run(world.a, sid)
    row = DS.one(admin_engine, sid, "DQ-012")
    assert row["severity"] == "high"
    assert len(row["details"]["enrollment_ids"]) == 2
    # Born 2012-03-14: 14 on 2026-06-01, inside IX [13, 15] -> no DQ-007.
    assert DS.findings(admin_engine, sid, "DQ-007") == []
    young = DS.student(world.a, extra=[])
    DS.call(world.a, students.record_value, young, "dob", "birth_certificate", "2019-01-01")
    DS.run(world.a, young)
    assert DS.findings(admin_engine, young, "DQ-007") == []  # canonical is still the register


def test_DQ_008_duplicates_across_the_school_and_scope_masking(
    world: Any, admin_engine: Engine
) -> None:
    unique = f"Synthetica Dup{uuid.uuid4().hex[:6]}"
    in_9a = DS.student(world.a, name=f"{unique} Venkata", admission_no=f"D{uuid.uuid4().hex[:8]}")
    in_9c = DS.student(
        world.a,
        name=f"{unique} Venkata",
        section_key="section_9c",
        admission_no=f"D{uuid.uuid4().hex[:8]}",
    )
    DS.run(world.a, in_9a)
    mine, theirs = DS.one(admin_engine, in_9a, "DQ-008"), DS.one(admin_engine, in_9c, "DQ-008")
    assert mine["related_student_id"] == in_9c
    assert theirs["related_student_id"] == in_9a
    teacher = DS.ctx(world.a, "class_teacher")
    page = DS.call(
        world.a,
        dq.list_findings,
        FindingFilters(rule_id=["DQ-008"], student_id=in_9a),
        as_ctx=teacher,
    )
    (seen,) = page.data
    assert seen.explanation.en == "Possible duplicate of ••••."
    admin_view = DS.call(world.a, dq.get_finding, mine["id"])
    assert admin_view.explanation.en.startswith("Possible duplicate of D")
    with pytest.raises(NotFound):
        DS.call(world.a, dq.get_finding, theirs["id"], as_ctx=teacher)


# --- scope (SEC-015) ------------------------------------------------------------------------------


def test_SEC_015_class_teacher_runs_and_reads_only_own_sections(
    world: Any, admin_engine: Engine
) -> None:
    a9 = DS.conflict_student(world.a)
    c9 = DS.conflict_student(world.a, section_key="section_9c")
    teacher = DS.ctx(world.a, "class_teacher")
    run = DS.call(world.a, dq.run_checks, student_ids=[a9, c9], as_ctx=teacher)
    assert run.stats["students"] == 1
    assert DS.findings(admin_engine, c9) == []
    assert DS.findings(admin_engine, a9, "DQ-003")
    DS.run(world.a, c9)
    listed = DS.call(world.a, dq.list_findings, FindingFilters(), as_ctx=teacher, limit=200)
    assert c9 not in {f.student.id for f in listed.data}
    (hidden,) = DS.findings(admin_engine, c9, "DQ-003")
    with pytest.raises(NotFound):
        DS.call(world.a, dq.get_finding, hidden["id"], as_ctx=teacher)
    with pytest.raises(NotFound):
        DS.call(world.a, dq.findings_for_student, c9, as_ctx=teacher)
    with pytest.raises(NotFound):
        DS.call(world.a, dq.get_run, DS.run(world.a, c9).id, as_ctx=teacher)
    assert DS.call(world.a, dq.get_run, run.id, as_ctx=teacher).id == run.id


def test_SEC_001_other_school_findings_are_invisible(world: Any, admin_engine: Engine) -> None:
    b_student = DS.conflict_student(world.b)
    DS.run(world.b, b_student)
    (b_row,) = DS.findings(admin_engine, b_student, "DQ-003")
    with pytest.raises(NotFound):
        DS.call(world.a, dq.get_finding, b_row["id"])
    out = DS.call(world.a, dq.run_checks, student_ids=[b_student])
    assert out.stats["students"] == 0


# --- incremental runs and change requests (outbox) ------------------------------------------------


def test_FR_DQ_002_incremental_run_from_the_outbox_event(world: Any, admin_engine: Engine) -> None:
    from app.dq import tasks

    sid = DS.student(world.a, extra=DS.aadhaar(dob="2012-03-15"))
    (payload,) = [
        p
        for p in DS.outbox(admin_engine, world.a.tenant_id, "student.values.changed")
        if p["student_ids"] == [str(sid)]
    ]
    run_id = tasks.run_incremental.apply(
        kwargs={
            "tenant_id": str(world.a.tenant_id),
            "event_id": str(uuid.uuid4()),
            "payload": payload,
        }
    ).get()
    row = DS.one(admin_engine, sid, "DQ-002")
    assert row["severity"] == "blocker"
    assert row["first_seen_run_id"] == uuid.UUID(run_id)
    (event,) = [
        e
        for e in _audit(admin_engine, world.a.tenant_id, "dq.run.completed")
        if e["resource_id"] == uuid.UUID(run_id)
    ]
    assert event["summary"]["trigger"] == "event"
    assert event["summary"]["event_type"] == "student.values.changed"
    with admin_engine.connect() as c:
        actor: str = c.execute(
            text(
                "SELECT actor_type FROM audit.events WHERE resource_id = :r "
                "AND action = 'dq.run.completed'"
            ),
            {"r": uuid.UUID(run_id)},
        ).scalar_one()
    assert actor == "system"


def test_FR_DQ_002_change_request_events_link_and_resolve(world: Any, admin_engine: Engine) -> None:
    from app.dq import tasks

    sid = DS.student(world.a, extra=DS.aadhaar(dob="2012-03-15"))
    DS.run(world.a, sid)
    cr = uuid.uuid4()
    payload = {"change_request_id": str(cr), "student_id": str(sid), "attribute_key": "dob"}
    kwargs = {
        "tenant_id": str(world.a.tenant_id),
        "event_id": str(uuid.uuid4()),
        "payload": payload,
    }
    assert tasks.link_change_request.apply(kwargs=kwargs).get() == 1
    assert DS.one(admin_engine, sid, "DQ-002")["change_request_id"] == cr
    # The (synthetic) correction lands, then the approval event re-checks the student.
    DS.call(
        world.a,
        students.record_value,
        sid,
        "aadhaar_dob_as_printed",
        "aadhaar_as_printed",
        "2012-03-14",
    )
    assert tasks.run_incremental.apply(kwargs=kwargs).get()
    row = DS.one(admin_engine, sid, "DQ-002")
    assert (row["status"], row["resolution"], row["change_request_id"]) == (
        "resolved",
        "change_request",
        cr,
    )
    # A rejected request unlinks unresolved findings.
    other = DS.student(world.a, extra=DS.aadhaar(gender="female"))
    DS.run(world.a, other)
    cr2 = uuid.uuid4()
    link = {"change_request_id": str(cr2), "student_id": str(other), "attribute_key": "gender"}
    k2 = {"tenant_id": str(world.a.tenant_id), "event_id": str(uuid.uuid4()), "payload": link}
    tasks.link_change_request.apply(kwargs=k2).get()
    assert tasks.unlink_change_request.apply(kwargs=k2).get() == 1
    assert DS.one(admin_engine, other, "DQ-003")["change_request_id"] is None


def test_FR_DQ_002_outbox_routes_go_to_the_dq_queue() -> None:
    from app.dq import tasks
    from app.ops import service as ops

    for event in (
        "student.values.changed",
        "import.committed",
        "import.reverted",
        "extraction.confirmed",
        "change_request.approved",
    ):
        assert ops.OUTBOX_ROUTES[event] == "dq.run_incremental"
    assert ops.OUTBOX_ROUTES["change_request.submitted"] == "dq.link_change_request"
    assert ops.OUTBOX_ROUTES["change_request.rejected"] == "dq.unlink_change_request"
    assert ops.OUTBOX_ROUTES["dq.run.requested"] == "dq.execute_run"
    for task in (
        tasks.run_incremental,
        tasks.execute_run,
        tasks.link_change_request,
        tasks.unlink_change_request,
    ):
        assert getattr(task, "queue", None) == "dq"


def test_FR_DQ_002_batch_and_extraction_payloads(world: Any, admin_engine: Engine) -> None:
    sid = DS.student(world.a)
    batch = uuid.uuid4()  # a real batch row: attribute_values_import_batch_fk (0012)
    with admin_engine.begin() as c:
        c.execute(
            text(
                "INSERT INTO sis.import_batches (id, tenant_id, source, created_by) "
                "SELECT :id, :t, 'udise_plus', min(user_id::text)::uuid "
                "FROM core.memberships WHERE tenant_id = :t"
            ),
            {"id": batch, "t": world.a.tenant_id},
        )
    DS.call(
        world.a, students.record_value, sid, "gender", "udise_plus", "female", import_batch_id=batch
    )
    run = dq.run_for_event(world.a.tenant_id, {"batch_id": str(batch), "student_ids_count": 1})
    assert run is not None
    assert run.stats is not None
    assert run.stats["students"] == 1
    assert DS.one(admin_engine, sid, "DQ-011")["severity"] == "medium"
    item = {"batch_id": str(uuid.uuid4()), "item_id": str(uuid.uuid4()), "student_id": str(sid)}
    again = dq.run_for_event(world.a.tenant_id, item)
    assert again is not None
    assert again.event_type == "extraction.confirmed"
    assert dq.run_for_event(world.a.tenant_id, {"batch_id": str(uuid.uuid4())}) is None


# --- queued runs ----------------------------------------------------------------------------------


def test_US_501_big_scope_is_queued_and_the_requester_notified(
    world: Any, admin_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.dq import service, tasks
    from app.dq.profiles import load_engine_config
    from app.dq.schemas import RunCreate, RunScopeIn

    cfg = load_engine_config().model_copy(update={"sync_max_students": 0})
    monkeypatch.setattr(service, "load_engine_config", lambda: cfg)
    sid = DS.conflict_student(world.a)
    who = DS.ctx(world.a, "exam_coordinator")
    queued = DS.call(
        world.a, dq.request_run, RunCreate(scope=RunScopeIn(student_ids=[sid])), as_ctx=who
    )
    assert queued.status == "queued"
    (payload,) = [
        p
        for p in DS.outbox(admin_engine, world.a.tenant_id, "dq.run.requested")
        if p["run_id"] == str(queued.id)
    ]
    kwargs = {
        "tenant_id": str(world.a.tenant_id),
        "event_id": str(uuid.uuid4()),
        "payload": payload,
    }
    assert tasks.execute_run.apply(kwargs=kwargs).get() == "completed"
    assert tasks.execute_run.apply(kwargs=kwargs).get() == "completed"  # idempotent
    done = DS.call(world.a, dq.get_run, queued.id, as_ctx=who)
    assert done.stats["students"] == 1
    with admin_engine.connect() as c:
        notes = c.execute(
            text(
                "SELECT template_key, params FROM ops.notifications "
                "WHERE recipient_membership_id = :m AND resource_id = :r"
            ),
            {"m": who.membership_id, "r": queued.id},
        ).all()
    assert [(n[0], n[1]["blockers"], n[1]["warnings"]) for n in notes] == [
        ("dq.run.completed", 0, 1)
    ]
