"""Attendance, exams and marks through the service (US-1701..US-1703; FR-ATT-001..005,
FR-MRK-001..005; invariants 3, 4, 7). Synthetic school from ``tests/insights/support.py``.
"""

from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import Engine, text

from app.academics import service as academics
from app.academics.schemas import (
    AttendanceEntryIn,
    AttendanceWrite,
    ExamCreate,
    MarkIn,
    MarksWrite,
    SheetIn,
)
from app.core.db import tenant_session
from app.core.errors import Conflict, NotFound, ValidationFailed
from app.core.redaction import verhoeff_check_digit

pytestmark = pytest.mark.db


def _load() -> Any:
    import sys

    return sys.modules["sos_test_insights_support"]


S = _load()


def _count(admin: Engine, sql: str, **params: Any) -> int:
    with admin.connect() as c:
        return int(c.execute(text(sql), params).scalar_one())


def _events(admin: Engine, tenant_id: uuid.UUID, action: str) -> list[dict[str, Any]]:
    with admin.connect() as c:
        return [
            dict(r._mapping)
            for r in c.execute(
                text(
                    "SELECT resource_id, summary, actor_type FROM audit.events "
                    "WHERE tenant_id = :t AND action = :a ORDER BY seq"
                ),
                {"t": tenant_id, "a": action},
            )
        ]


def _codes(err: pytest.ExceptionInfo[ValidationFailed]) -> set[str]:
    return {e["code"] for e in err.value.errors}


# --- attendance -----------------------------------------------------------------------------------


def test_FR_ATT_002_a_day_is_saved_audited_and_queued_for_the_rules(
    school: Any, admin_engine: Engine
) -> None:
    day = S.school_days(1, end=S.school_days(20)[0])[0]
    before = _count(
        admin_engine,
        "SELECT count(*) FROM ops.outbox WHERE tenant_id = :t "
        "AND event_type = 'academics.records.changed'",
        t=school.tenant_id,
    )
    out = S.record(
        school,
        "section_9a",
        {school.ids["a1"]: ["present"], school.ids["a2"]: ["absent"]},
        [day],
        who=S.ct_ctx(school),
    )
    assert out.written == 2
    assert out.unchanged == 0
    event = _events(admin_engine, school.tenant_id, "attendance.recorded")[-1]
    assert event["resource_id"] == school.ids["section_9a"]
    assert event["summary"]["entries"] == 2
    assert event["summary"]["first_date"] == day.isoformat()
    assert "student" not in str(event["summary"]).lower()
    after = _count(
        admin_engine,
        "SELECT count(*) FROM ops.outbox WHERE tenant_id = :t "
        "AND event_type = 'academics.records.changed'",
        t=school.tenant_id,
    )
    assert after == before + 1


def test_FR_ATT_002_saving_again_corrects_and_counts_unchanged(school: Any) -> None:
    day = S.school_days(1, end=S.school_days(21)[0])[0]
    a1, a2 = school.ids["a1"], school.ids["a2"]
    S.record(school, "section_9a", {a1: ["absent"], a2: ["present"]}, [day])
    again = S.record(school, "section_9a", {a1: ["present"], a2: ["present"]}, [day])
    assert (again.written, again.unchanged) == (1, 1)
    actor = S.principal_ctx(school)
    with tenant_session(school.tenant_id, actor.user_id) as db:
        view = academics.attendance_day(db, actor, school.ids["section_9a"], day)
    assert {s.student.student_id: s.status for s in view.students}[a1] == "present"
    assert view.marked is True
    assert [s.student.roll_no for s in view.students] == ["1", "2"]


@pytest.mark.parametrize(
    ("student_key", "offset", "code"),
    [
        ("c1", 0, "not_in_section"),
        ("a1", 5, "future_date"),
        ("a1", -400, "date_out_of_range"),
    ],
)
def test_FR_ATT_002_bad_entries_refuse_the_whole_request(
    school: Any, admin_engine: Engine, student_key: str, offset: int, code: str
) -> None:
    today = academics.today_ist()
    good = AttendanceEntryIn(
        student_id=school.ids["a2"],
        on_date=S.school_days(1, end=S.school_days(22)[0])[0],
        status="leave",
    )
    bad = AttendanceEntryIn(
        student_id=school.ids[student_key],
        on_date=today + dt.timedelta(days=offset),
        status="absent",
    )
    actor = S.principal_ctx(school)
    rows = _count(
        admin_engine,
        "SELECT count(*) FROM sis.attendance_marks WHERE tenant_id = :t",
        t=school.tenant_id,
    )
    with (
        pytest.raises(ValidationFailed) as err,
        tenant_session(school.tenant_id, actor.user_id) as db,
    ):
        academics.record_attendance(
            db, actor, school.ids["section_9a"], AttendanceWrite(entries=[good, bad])
        )
    assert code in _codes(err)
    assert rows == _count(
        admin_engine,
        "SELECT count(*) FROM sis.attendance_marks WHERE tenant_id = :t",
        t=school.tenant_id,
    )


def test_FR_ATT_002_duplicate_entries_are_refused(school: Any) -> None:
    day = S.school_days(1, end=S.school_days(23)[0])[0]
    entry = AttendanceEntryIn(student_id=school.ids["a1"], on_date=day, status="present")
    actor = S.principal_ctx(school)
    with (
        pytest.raises(ValidationFailed) as err,
        tenant_session(school.tenant_id, actor.user_id) as db,
    ):
        academics.record_attendance(
            db, actor, school.ids["section_9a"], AttendanceWrite(entries=[entry, entry])
        )
    assert "duplicate_entry" in _codes(err)


def test_invariant_3_class_teacher_reaches_only_their_section(school: Any) -> None:
    ct = S.ct_ctx(school)
    day = academics.today_ist()
    with tenant_session(school.tenant_id, ct.user_id) as db:
        with pytest.raises(NotFound):
            academics.attendance_day(db, ct, school.ids["section_9c"], day)
        with pytest.raises(NotFound):
            academics.record_attendance(
                db,
                ct,
                school.ids["section_9c"],
                AttendanceWrite(
                    entries=[
                        AttendanceEntryIn(
                            student_id=school.ids["c1"],
                            on_date=S.school_days(1)[0],
                            status="absent",
                        )
                    ]
                ),
            )
        own = academics.attendance_day(db, ct, school.ids["section_9a"], day)
    assert {s.student.student_id for s in own.students} == {school.ids["a1"], school.ids["a2"]}


def test_FR_ATT_003_month_register_lists_school_days(school: Any) -> None:
    days = S.school_days(3, end=S.school_days(30)[0])
    S.record(
        school,
        "section_9a",
        {school.ids["a1"]: ["present", "absent", "late"]},
        days,
    )
    actor = S.principal_ctx(school)
    month = days[-1].strftime("%Y-%m")
    with tenant_session(school.tenant_id, actor.user_id) as db:
        out = academics.attendance_month(db, actor, school.ids["section_9a"], month)
    in_month = [d for d in days if d.strftime("%Y-%m") == month]
    assert set(in_month) <= set(out.school_days)
    row = {r.student.student_id: r for r in out.students}[school.ids["a1"]]
    for day in in_month:
        assert day.isoformat() in row.days


def test_FR_ATT_003_a_month_outside_the_calendar_is_refused_not_an_error(school: Any) -> None:
    # "YYYY-MM" passes the route's pattern; dt.date() refused year 0 and, for December 9999,
    # the first day of the next month (500).
    actor = S.principal_ctx(school)
    with (
        pytest.raises(ValidationFailed) as err,
        tenant_session(school.tenant_id, actor.user_id) as db,
    ):
        academics.attendance_month(db, actor, school.ids["section_9a"], "0000-01")
    assert _codes(err) == {"invalid_month"}
    with tenant_session(school.tenant_id, actor.user_id) as db:
        last = academics.attendance_month(db, actor, school.ids["section_9a"], "9999-12")
    assert last.school_days == []


# --- sheets ---------------------------------------------------------------------------------------


def test_FR_ATT_004_sheet_preview_stores_nothing_and_deletes_the_upload(
    school: Any, admin_engine: Engine
) -> None:
    days = S.school_days(2, end=S.school_days(40)[0])
    data = (
        "Adm No,Name,"
        + ",".join(d.strftime("%d/%m/%Y") for d in days)
        + "\nSYN-A1,Synthetic A1,P,A\nSYN-A2,Synthetic A2,LV,P\nSYN-C1,Synthetic C1,P,P\n"
    ).encode()
    doc = S.sheet_document(admin_engine, school, data)
    rows = _count(
        admin_engine,
        "SELECT count(*) FROM sis.attendance_marks WHERE tenant_id = :t",
        t=school.tenant_id,
    )
    out = academics.preview_attendance_sheet(
        S.ct_ctx(school), school.ids["section_9a"], SheetIn(document_id=doc)
    )
    assert out.dates == days
    assert out.students == 2
    assert len(out.entries) == 4
    # SYN-C1 is in 9C: not on this section's roster.
    assert [(i.row, i.code) for i in out.issues] == [(4, "unknown_student")]
    assert not S.document_exists(admin_engine, doc)
    assert rows == _count(
        admin_engine,
        "SELECT count(*) FROM sis.attendance_marks WHERE tenant_id = :t",
        t=school.tenant_id,
    )
    read = _events(admin_engine, school.tenant_id, "attendance.sheet_read")[-1]
    assert read["summary"]["document_id"] == str(doc)


def test_invariant_4_a_sheet_with_an_aadhaar_number_is_refused_and_still_deleted(
    school: Any, admin_engine: Engine
) -> None:
    number = "23456789012" + verhoeff_check_digit("23456789012")
    day = S.school_days(1)[0].strftime("%d/%m/%Y")
    doc = S.sheet_document(
        admin_engine, school, f"Adm No,{day}\n{number},P\n".encode(), uploader="principal"
    )
    with pytest.raises(ValidationFailed) as err:
        academics.preview_attendance_sheet(
            S.principal_ctx(school), school.ids["section_9a"], SheetIn(document_id=doc)
        )
    assert _codes(err) == {"aadhaar_full_number_rejected"}
    assert number not in str(err.value.errors)
    assert not S.document_exists(admin_engine, doc)


def test_SEC_015_only_the_uploader_may_read_and_delete_a_sheet(
    school: Any, admin_engine: Engine
) -> None:
    """Audit 2026-10-04, DL-04: a sheet preview reads the upload and deletes it at once. Any
    ``import_file`` the caller could see was accepted, so a class teacher (or a school-wide
    reader) could read and destroy another person's upload, e.g. a file waiting to be
    imported, without delete rights. Someone else's upload is 404 and is left alone."""
    day = S.school_days(1)[0].strftime("%d/%m/%Y")
    doc = S.sheet_document(
        admin_engine, school, f"Adm No,{day}\nSYN-A1,P\n".encode(), uploader="owner"
    )
    for actor in (S.ct_ctx(school), S.principal_ctx(school)):
        with pytest.raises(NotFound):
            academics.preview_attendance_sheet(
                actor, school.ids["section_9a"], SheetIn(document_id=doc)
            )
    assert S.document_exists(admin_engine, doc)
    assert not any(
        e["summary"].get("document_id") == str(doc)
        for e in _events(admin_engine, school.tenant_id, "attendance.sheet_read")
    )


def test_FR_ATT_004_the_upload_must_be_visible_and_scanned(
    school: Any, admin_engine: Engine
) -> None:
    doc = S.sheet_document(
        admin_engine, school, b"Adm No\n", section_key="section_9c", uploader="principal"
    )
    with pytest.raises(NotFound):
        academics.preview_attendance_sheet(
            S.ct_ctx(school), school.ids["section_9a"], SheetIn(document_id=doc)
        )
    assert S.document_exists(admin_engine, doc)
    with admin_engine.begin() as c:
        c.execute(
            text("UPDATE kb.document_versions SET status = 'queued' WHERE document_id = :d"),
            {"d": doc},
        )
    with pytest.raises(Conflict) as err:
        academics.preview_attendance_sheet(
            S.principal_ctx(school), school.ids["section_9a"], SheetIn(document_id=doc)
        )
    assert err.value.code == "document_not_ready"


# --- exams and marks ------------------------------------------------------------------------------


def test_FR_MRK_001_exams_belong_to_the_current_year(school: Any) -> None:
    actor = S.principal_ctx(school)
    today = min(academics.today_ist(), S.YEAR_END)
    name = f"Synthetic unit test {uuid.uuid4().hex[:5]}"
    with tenant_session(school.tenant_id, actor.user_id) as db:
        exam = academics.create_exam(db, actor, ExamCreate(name=name, held_on=today))
    assert exam.academic_year_id == school.ids["year"]
    with (
        pytest.raises(ValidationFailed) as err,
        tenant_session(school.tenant_id, actor.user_id) as db,
    ):
        academics.create_exam(db, actor, ExamCreate(name=name.upper(), held_on=today))
    assert "exam_name_taken" in _codes(err)
    with (
        pytest.raises(ValidationFailed) as err,
        tenant_session(school.tenant_id, actor.user_id) as db,
    ):
        academics.create_exam(
            db, actor, ExamCreate(name="Synthetic old", held_on=dt.date(2020, 1, 1))
        )
    assert "date_out_of_range" in _codes(err)
    with tenant_session(school.tenant_id, actor.user_id) as db:
        listed = academics.list_exams(db, actor)
    assert exam.id in {e.id for e in listed}


def test_R_13_a_concurrent_duplicate_exam_is_a_422_not_a_500(
    school: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two requests creating the same exam at once both pass the name check; the second insert
    hit the unique constraint and was a 500 (audit 2026-10-06 R-13). The race is simulated by
    letting the check miss the first exam."""
    actor = S.principal_ctx(school)
    today = min(academics.today_ist(), S.YEAR_END)
    name = f"Synthetic race {uuid.uuid4().hex[:5]}"
    with tenant_session(school.tenant_id, actor.user_id) as db:
        academics.create_exam(db, actor, ExamCreate(name=name, held_on=today))
    monkeypatch.setattr(academics.repo, "exam_name_taken", lambda *_a, **_k: False)
    with (
        pytest.raises(ValidationFailed) as err,
        tenant_session(school.tenant_id, actor.user_id) as db,
    ):
        academics.create_exam(db, actor, ExamCreate(name=name, held_on=today))
    assert "exam_name_taken" in _codes(err)


def test_FR_MRK_002_marks_grid_with_percent_and_absent_papers(school: Any) -> None:
    exam_id = S.exam(school, f"Synthetic SA {uuid.uuid4().hex[:5]}", S.school_days(1)[0])
    a1 = school.ids["a1"]
    out = S.marks(
        school,
        "section_9a",
        exam_id,
        {a1: [("Telugu", 40, 50), ("Maths", None, 50), ("English", 30.5, 50)]},
    )
    assert out.written == 3
    ct = S.ct_ctx(school)
    with tenant_session(school.tenant_id, ct.user_id) as db:
        grid = academics.section_marks(db, ct, school.ids["section_9a"], exam_id)
    row = {s.student.student_id: s for s in grid.students}[a1]
    assert row.percent == 70.5  # (40 + 30.5) / 100, the absent paper left out
    assert {m.subject: m.absent for m in row.marks}["Maths"] is True
    assert academics.percent_of([(None, Decimal(50), True)]) is None


@pytest.mark.parametrize(
    ("entry", "code"),
    [
        ({"marks": "51", "max_marks": "50"}, "marks_over_max"),
        ({"marks": "10", "absent": True}, "absent_has_no_marks"),
        (
            {"subject": "Aadhaar " + "23456789012" + verhoeff_check_digit("23456789012")},
            "aadhaar_full_number_rejected",
        ),
    ],
)
def test_FR_MRK_002_bad_marks_are_refused(school: Any, entry: dict[str, Any], code: str) -> None:
    exam_id = S.exam(school, f"Synthetic FA {uuid.uuid4().hex[:5]}", S.school_days(1)[0])
    base: dict[str, Any] = {
        "student_id": school.ids["a1"],
        "subject": "Science",
        "max_marks": "50",
        "marks": "20",
        "absent": False,
    }
    actor = S.principal_ctx(school)
    with (
        pytest.raises(ValidationFailed) as err,
        tenant_session(school.tenant_id, actor.user_id) as db,
    ):
        academics.record_marks(
            db,
            actor,
            school.ids["section_9a"],
            exam_id,
            MarksWrite(entries=[MarkIn.model_validate({**base, **entry})]),
        )
    assert code in _codes(err)


def test_FR_MRK_002_a_subject_spelt_in_another_case_corrects_the_same_paper(school: Any) -> None:
    # One mark per exam, student and subject: "MATHS" from a sheet corrects "Maths" typed on
    # screen instead of adding a second paper that the percentage would count twice.
    exam_id = S.exam(school, f"Synthetic FA4 {uuid.uuid4().hex[:5]}", S.school_days(1)[0])
    a1 = school.ids["a1"]
    S.marks(school, "section_9a", exam_id, {a1: [("Maths", 10, 50), ("Telugu", 40, 50)]})
    again = S.marks(school, "section_9a", exam_id, {a1: [("MATHS", 30, 50)]})
    assert (again.written, again.unchanged) == (1, 0)
    # Another student's first maths paper joins the exam's column.
    S.marks(school, "section_9a", exam_id, {school.ids["a2"]: [("maths", 25, 50)]})
    ct = S.ct_ctx(school)
    with tenant_session(school.tenant_id, ct.user_id) as db:
        grid = academics.section_marks(db, ct, school.ids["section_9a"], exam_id)
    row = {s.student.student_id: s for s in grid.students}[a1]
    assert sorted((m.subject, m.marks) for m in row.marks) == [
        ("Maths", Decimal("30.00")),
        ("Telugu", Decimal("40.00")),
    ]
    assert row.percent == 70.0
    assert grid.subjects == ["Maths", "Telugu"]


def test_FR_MRK_004_marks_sheet_preview(school: Any, admin_engine: Engine) -> None:
    exam_id = S.exam(school, f"Synthetic FA2 {uuid.uuid4().hex[:5]}", S.school_days(1)[0])
    data = b"Roll no,Telugu,Maths\nMax marks,50,100\n1,45,AB\n2,51,70\n"
    doc = S.sheet_document(admin_engine, school, data)
    out = academics.preview_marks_sheet(
        S.ct_ctx(school), school.ids["section_9a"], exam_id, SheetIn(document_id=doc)
    )
    assert out.subjects == ["Telugu", "Maths"]
    assert [(i.row, i.column, i.code) for i in out.issues] == [(4, 2, "marks_over_max")]
    got = {(e.student_id, e.subject): e for e in out.entries}
    assert got[(school.ids["a1"], "Maths")].absent is True
    assert got[(school.ids["a2"], "Maths")].marks == Decimal("70.00")
    assert not S.document_exists(admin_engine, doc)


def test_FR_MRK_005_results_per_exam_for_the_rules(school: Any) -> None:
    exam_id = S.exam(school, f"Synthetic FA3 {uuid.uuid4().hex[:5]}", S.school_days(1)[0])
    S.marks(school, "section_9a", exam_id, {school.ids["a2"]: [("Science", 12, 40)]})
    with tenant_session(school.tenant_id) as db:
        results = academics.exam_results(db, [school.ids["a2"]])
    mine = [r for r in results[school.ids["a2"]] if r.exam_id == exam_id]
    assert mine[0].percent == 30.0
    assert mine[0].papers == 1


# --- records made in another section (audit 2026-10-05 A-04) --------------------------------------


def _student_in_9c(school: Any) -> uuid.UUID:
    """A new student enrolled in 9C (the fixture's students stay where they are)."""
    sid: uuid.UUID = S.SW.create(
        school,
        name="Synthetica Moved Kumar",
        section_key=None,
        admission_no=f"SYN-MV-{uuid.uuid4().hex[:6]}",
    )
    with tenant_session(school.tenant_id, school.people["owner"].user_id) as db:
        S.students.enrol(
            db,
            S.SW.admin_ctx(school),
            sid,
            S.EnrollmentIn(section_id=school.ids["section_9c"], roll_no="7"),
        )
    return sid


def _move_to_9a(school: Any, admin_engine: Engine, sid: uuid.UUID) -> None:
    with admin_engine.begin() as c:
        c.execute(
            text(
                "UPDATE sis.enrollments SET section_id = :s WHERE tenant_id = :t "
                "AND student_id = :st AND status = 'active'"
            ),
            {"s": school.ids["section_9a"], "t": school.tenant_id, "st": sid},
        )


def test_SEC_015_a_scoped_teacher_cannot_rewrite_another_sections_attendance(
    school: Any, admin_engine: Engine
) -> None:
    sid = _student_in_9c(school)
    day = S.school_days(1, end=S.school_days(30)[0])[0]
    ct9c = S.ct_ctx(school, "ct9c", "section_9c")
    S.record(school, "section_9c", {sid: ["present"]}, [day], who=ct9c)
    _move_to_9a(school, admin_engine, sid)

    ct = S.ct_ctx(school)
    entry = AttendanceEntryIn(student_id=sid, on_date=day, status="absent")
    with (
        pytest.raises(ValidationFailed) as err,
        tenant_session(school.tenant_id, ct.user_id) as db,
    ):
        academics.record_attendance(
            db, ct, school.ids["section_9a"], AttendanceWrite(entries=[entry])
        )
    assert "recorded_in_another_section" in _codes(err)
    with admin_engine.connect() as c:
        row = c.execute(
            text(
                "SELECT section_id, status FROM sis.attendance_marks "
                "WHERE tenant_id = :t AND student_id = :s AND on_date = :d"
            ),
            {"t": school.tenant_id, "s": sid, "d": day},
        ).one()
    assert (row.section_id, row.status) == (school.ids["section_9c"], "present")

    # A day with no record yet, and a school-wide recorder's correction, still work.
    later = S.school_days(1, end=S.school_days(29)[0])[0]
    assert S.record(school, "section_9a", {sid: ["late"]}, [later], who=ct).written == 1
    assert S.record(school, "section_9a", {sid: ["absent"]}, [day]).written == 1


def test_SEC_015_a_scoped_teacher_cannot_rewrite_another_sections_marks(
    school: Any, admin_engine: Engine
) -> None:
    sid = _student_in_9c(school)
    exam_id = S.exam(school, f"Synthetic moved {uuid.uuid4().hex[:5]}", S.school_days(1)[0])
    S.marks(school, "section_9c", exam_id, {sid: [("Maths", 20, 50)]})
    _move_to_9a(school, admin_engine, sid)

    ct = S.ct_ctx(school)
    entry = MarkIn(student_id=sid, subject="Maths", marks=45, max_marks=50, absent=False)
    with (
        pytest.raises(ValidationFailed) as err,
        tenant_session(school.tenant_id, ct.user_id) as db,
    ):
        academics.record_marks(
            db, ct, school.ids["section_9a"], exam_id, MarksWrite(entries=[entry])
        )
    assert "recorded_in_another_section" in _codes(err)
    with admin_engine.connect() as c:
        row = c.execute(
            text(
                "SELECT section_id, marks FROM sis.exam_marks "
                "WHERE tenant_id = :t AND student_id = :s AND exam_id = :e"
            ),
            {"t": school.tenant_id, "s": sid, "e": exam_id},
        ).one()
    assert (row.section_id, row.marks) == (school.ids["section_9c"], Decimal("20.00"))
