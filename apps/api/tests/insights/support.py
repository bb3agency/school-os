"""Synthetic set-up for the attendance, marks and early-warning tests (M5; synthetic data only).

Loaded by path (``--import-mode=importlib``). Builds on ``tests/api/world.py`` (schools A and B,
one member per role; class teacher scoped to section 9A, subject teacher to class X) and
``tests/students/student_world.py`` (``s9a`` in 9A, ``s9c`` in 9C, ``s10a`` in 10A, ``sb`` in
school B).

:func:`insights_school` builds a separate school (so flags, notifications and audit events of
the shared world stay untouched) with the world's structure and:

    owner          no insights (08 PRV-004: not an educational role)
    principal      school-wide insights, manage (step-up)
    ct             class teacher scoped to 9A AND recorded as 9A's class teacher
    ct9c           class teacher scoped to 9C, not recorded as its class teacher
    office_admin   attendance only
    exam           exam coordinator: exams and marks
    teacher        subject teacher of class X: no attendance, marks or insights
    students       a1, a2 in 9A (admission SYN-A1, SYN-A2; rolls 1, 2), c1 in 9C, x1 in 10A

Rows are made through the real services (attendance, marks, notes, manual flags, the rules).
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import importlib.util
import sys
import uuid
from collections.abc import Sequence
from pathlib import Path
from types import ModuleType
from typing import Any

from sqlalchemy import Engine, text

from app.academics import service as academics
from app.academics.schemas import AttendanceEntryIn, AttendanceWrite, ExamCreate, MarkIn, MarksWrite
from app.authz.context import UserContext
from app.core.db import tenant_session
from app.insights import service as insights
from app.insights.schemas import ManualFlagIn, NoteIn
from app.students import service as students
from app.students.schemas import EnrollmentIn
from app.tenancy import service as tenancy
from app.tenancy.schemas import SectionUpdate

TESTS = Path(__file__).resolve().parents[1]
YEAR_END = dt.date(2027, 3, 31)


def _load(name: str, path: Path) -> ModuleType:
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, path)
        assert spec is not None
        assert spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


SW = _load("sos_test_student_world", TESTS / "students" / "student_world.py")
W = SW.W
D = _load("sos_test_documents_support", TESTS / "documents" / "support.py")


def install() -> Any:
    """Keyring for the synthetic schools and an in-memory object store."""
    SW.configure_keyring()
    return D.memory_store()


def ctx(school: Any, role: str, person: Any | None = None, **scopes: Any) -> UserContext:
    """A context with exactly one system role's permissions (``section_ids``/``class_ids`` for
    scoped roles), signed in with MFA just now."""
    who = person or school.people[role]
    base: UserContext = SW.ctx_for(school.tenant_id, who, role, **scopes)
    return dataclasses.replace(base, auth_time=dt.datetime.now(dt.UTC))


def ct_ctx(school: Any, key: str = "ct", section_key: str = "section_9a") -> UserContext:
    return ctx(
        school,
        "class_teacher",
        school.people[key],
        section_ids=frozenset({school.ids[section_key]}),
    )


def principal_ctx(school: Any) -> UserContext:
    return ctx(school, "principal")


def school_days(count: int, *, end: dt.date | None = None) -> list[dt.date]:
    """``count`` weekdays ending at ``end`` (default: today, IST, within the 2026-27 year)."""
    day = end or min(academics.today_ist(), YEAR_END)
    out: list[dt.date] = []
    while len(out) < count:
        if day.weekday() < 5:
            out.append(day)
        day -= dt.timedelta(days=1)
    return sorted(out)


def insights_school(admin: Engine) -> Any:
    install()
    s = W.School(W.provision_school())
    add = W.add_member
    s.people["owner"] = add(admin, s.tenant_id, ["owner"], display_name="Synthetic Owner Rao")
    W.build_structure(s, s.people["owner"])
    s.people["principal"] = add(
        admin, s.tenant_id, ["principal"], display_name="Synthetic Principal Suresh"
    )
    s.people["ct"] = add(
        admin,
        s.tenant_id,
        ["class_teacher"],
        scopes=[("section", s.ids["section_9a"])],
        display_name="Synthetic Teacher Anitha",
    )
    s.people["ct9c"] = add(
        admin,
        s.tenant_id,
        ["class_teacher"],
        scopes=[("section", s.ids["section_9c"])],
        display_name="Synthetic Teacher Ramesh",
    )
    s.people["office_admin"] = add(admin, s.tenant_id, ["office_admin"])
    s.people["exam"] = add(admin, s.tenant_id, ["exam_coordinator"])
    s.people["teacher"] = add(admin, s.tenant_id, ["teacher"], scopes=[("class", s.ids["class_x"])])
    with tenant_session(s.tenant_id, s.people["owner"].user_id) as db:
        section = tenancy.get_section(db, s.ids["section_9a"])
        tenancy.update_section(
            db,
            section.id,
            SectionUpdate(class_teacher_membership_id=s.people["ct"].membership_id),
            expected_version=section.version,
        )
    for key, section_key, adm, roll in (
        ("a1", "section_9a", "SYN-A1", "1"),
        ("a2", "section_9a", "SYN-A2", "2"),
        ("c1", "section_9c", "SYN-C1", "1"),
        ("x1", "section_10a", "SYN-X1", "1"),
    ):
        sid = SW.create(
            s, name=f"Synthetica {key.upper()} Kumar", section_key=None, admission_no=adm
        )
        with tenant_session(s.tenant_id, s.people["owner"].user_id) as db:
            students.enrol(
                db,
                SW.admin_ctx(s),
                sid,
                EnrollmentIn(section_id=s.ids[section_key], roll_no=roll),
            )
        s.ids[key] = sid
    return s


def record(
    school: Any,
    section_key: str,
    statuses: dict[uuid.UUID, Sequence[str]],
    days: Sequence[dt.date],
    *,
    who: UserContext | None = None,
) -> Any:
    """Attendance for ``days`` (one status per day per student) through the real service."""
    actor = who or principal_ctx(school)
    entries = [
        AttendanceEntryIn(student_id=sid, on_date=day, status=status)
        for sid, seq in statuses.items()
        for day, status in zip(days, seq, strict=True)
    ]
    with tenant_session(school.tenant_id, actor.user_id) as db:
        return academics.record_attendance(
            db, actor, school.ids[section_key], AttendanceWrite(entries=entries)
        )


def exam(school: Any, name: str, held_on: dt.date) -> uuid.UUID:
    actor = principal_ctx(school)
    with tenant_session(school.tenant_id, actor.user_id) as db:
        out = academics.create_exam(db, actor, ExamCreate(name=name, held_on=held_on))
    return out.id


def marks(
    school: Any,
    section_key: str,
    exam_id: uuid.UUID,
    rows: dict[uuid.UUID, Sequence[tuple[str, float | None, float]]],
) -> Any:
    """Marks as (subject, marks or None for absent, max) per student."""
    actor = principal_ctx(school)
    entries = [
        MarkIn(
            student_id=sid,
            subject=subject,
            marks=None if got is None else got,
            max_marks=maximum,
            absent=got is None,
        )
        for sid, papers in rows.items()
        for subject, got, maximum in papers
    ]
    with tenant_session(school.tenant_id, actor.user_id) as db:
        return academics.record_marks(
            db, actor, school.ids[section_key], exam_id, MarksWrite(entries=entries)
        )


def evaluate(school: Any, student_ids: Sequence[uuid.UUID] | None = None) -> int:
    with tenant_session(school.tenant_id) as db:
        return insights.evaluate(db, student_ids)


def manual_flag(school: Any, student_key: str, *, who: UserContext | None = None) -> Any:
    actor = who or principal_ctx(school)
    with tenant_session(school.tenant_id, actor.user_id) as db:
        return insights.raise_flag(
            db, actor, school.ids[student_key], ManualFlagIn(indicator="behaviour")
        )


def note(
    school: Any,
    student_key: str,
    body: str = "Synthetic note: did not bring homework this week.",
    *,
    category: str = "observation",
    who: UserContext | None = None,
) -> Any:
    actor = who or ct_ctx(school)
    with tenant_session(school.tenant_id, actor.user_id) as db:
        return insights.add_note(
            db,
            actor,
            school.ids[student_key],
            NoteIn(category=category, text=body),
        )


def flags_of(admin: Engine, tenant_id: uuid.UUID, student_id: uuid.UUID) -> list[dict[str, Any]]:
    with admin.connect() as c:
        return [
            dict(r._mapping)
            for r in c.execute(
                text(
                    "SELECT * FROM sis.insight_flags WHERE tenant_id = :t AND student_id = :s "
                    "ORDER BY created_at"
                ),
                {"t": tenant_id, "s": student_id},
            )
        ]


def notifications(admin: Engine, tenant_id: uuid.UUID, template: str) -> list[dict[str, Any]]:
    with admin.connect() as c:
        return [
            dict(r._mapping)
            for r in c.execute(
                text(
                    "SELECT recipient_membership_id, params, resource_id FROM ops.notifications "
                    "WHERE tenant_id = :t AND template_key = :k ORDER BY created_at"
                ),
                {"t": tenant_id, "k": template},
            )
        ]


XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def sheet_document(
    admin: Engine, school: Any, data: bytes, *, kind: str = "csv", section_key: str = "section_9a"
) -> uuid.UUID:
    """A virus-checked ``import_file`` upload of the school, visible to the section's staff
    (ACL = the section, as the web registers it)."""
    doc_id: uuid.UUID = D.make_document(
        admin,
        school.tenant_id,
        school.people["owner"].user_id,
        acl=[("section", str(school.ids[section_key]))],
        purpose="import_file",
        doc_type="import_file",
        data=data,
        mime_type=XLSX_MIME if kind == "xlsx" else "text/csv",
        ext=kind,
    )
    return doc_id


def document_exists(admin: Engine, document_id: uuid.UUID) -> bool:
    with admin.connect() as c:
        return bool(
            c.execute(
                text("SELECT count(*) FROM kb.documents WHERE id = :d"), {"d": document_id}
            ).scalar_one()
        )


# --- shared world (matrix, BOLA) ------------------------------------------------------------------


def world_manual_flag(w: Any, student_key: str = "s9a") -> Any:
    """A fresh manual flag on a shared-world student of school A (principal raises it)."""
    ids = SW.ensure_students(w)
    actor = ctx(w.a, "principal")
    with tenant_session(w.a.tenant_id, actor.user_id) as db:
        return insights.raise_flag(db, actor, ids[student_key], ManualFlagIn(indicator="course"))


def world_note(w: Any, student_key: str = "s9a") -> Any:
    ids = SW.ensure_students(w)
    actor = ctx(w.a, "principal")
    with tenant_session(w.a.tenant_id, actor.user_id) as db:
        return insights.add_note(
            db,
            actor,
            ids[student_key],
            NoteIn(category="positive", text="Synthetic note: helped a classmate."),
        )


def world_exam(w: Any) -> uuid.UUID:
    """One exam of school A's current year (created once per process)."""
    if "insights_exam" not in w.a.ids:
        actor = ctx(w.a, "principal")
        day = min(academics.today_ist(), YEAR_END)
        with tenant_session(w.a.tenant_id, actor.user_id) as db:
            out = academics.create_exam(
                db, actor, ExamCreate(name=f"Synthetic test {uuid.uuid4().hex[:6]}", held_on=day)
            )
        w.a.ids["insights_exam"] = out.id
    value: uuid.UUID = w.a.ids["insights_exam"]
    return value
