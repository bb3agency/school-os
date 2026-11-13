"""Records completeness for the principal and the exam coordinator (FT-14, lead decision D86;
FR-ATT-016..FR-ATT-025, FR-MRK-016..FR-MRK-025). Public functions are re-exported by
:mod:`app.academics.service`.

**What it shows.** Per section of the current academic year, how many enrolled students have an
attendance status for a date (default today), and per exam and section how many have a mark (or
are marked absent) for each subject entered for that exam. Counts only: never which students,
never a mark or a status. The section's class teacher is named as the person the entry waits on,
only to holders of ``completeness.read`` (school-wide; principal and exam coordinator). It is
framed as task completeness per section: no teacher score, no ranking, nothing stored.

**Who counts.** The students of a section are its active enrolments in the year (for attendance:
started on or before the date); a mark counts only when it was made in that section for one of
those students. Without a subject catalogue (FT-02), an exam's subjects are the subjects entered
for it in the sections shown; a section with none of them is "not started".

**Reminders (FR-ATT-022, FR-MRK-022).** ``completeness.remind`` holders may send the section's
class teacher one in-app reminder per section, kind and day (or exam), only while
``completeness.reminders_enabled`` is on in ``config.yaml`` (default off: 409 ``reminders_off``).
Audited ``completeness.reminder_sent`` in the same transaction (ids, kind and count only).

Logs carry ids and counts only (invariant 5). Nothing here reaches an AI provider.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections import Counter, defaultdict
from collections.abc import Iterable, Sequence
from typing import Final
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.academics import calendar_service as cal_service
from app.academics import repository as repo
from app.academics.completeness_schemas import (
    AttendanceCompletenessOut,
    AttendanceCompletenessRow,
    AttendanceCompletenessStatus,
    CompletenessReminderIn,
    CompletenessReminderOut,
    CompletenessTeacherRef,
    MarksCellStatus,
    MarksCompletenessCell,
    MarksCompletenessOut,
    MarksCompletenessRow,
    MarksSectionStatus,
)
from app.academics.config import load_config
from app.audit import service as audit
from app.authz.context import UserContext
from app.core.errors import Conflict, NotFound, ValidationFailed
from app.core.logging import get_context, get_logger
from app.identity import service as identity
from app.notifications import service as notifications
from app.students import service as students
from app.tenancy import service as tenancy
from app.tenancy.schemas import AcademicYearOut
from app.tenancy.schemas import SectionOut as TenancySection

log = get_logger(__name__)

COMPLETENESS_READ: Final = "completeness.read"
COMPLETENESS_REMIND: Final = "completeness.remind"
REMINDER_TEMPLATE: Final = "academics.completeness_reminder"
IST: Final = ZoneInfo("Asia/Kolkata")


def _today() -> dt.date:
    return dt.datetime.now(dt.UTC).astimezone(IST).date()


def _error(field: str, code: str) -> dict[str, str]:
    return {"field": field, "code": code, "message_key": f"errors.{code}"}


def _current_year(session: Session) -> AcademicYearOut:
    year = tenancy.get_current_academic_year(session)
    if year is None:
        raise Conflict("Set the current academic year first.", code="no_current_year")
    return year


def _sections(
    session: Session, year_id: uuid.UUID, class_id: uuid.UUID | None = None
) -> list[TenancySection]:
    return tenancy.list_sections(
        session, academic_year_id=year_id, class_id=class_id, include_archived=False
    )


def _labels(session: Session) -> dict[uuid.UUID, str]:
    return {c.id: c.code for c in tenancy.list_classes(session)}


def _teachers(
    session: Session, sections: Iterable[TenancySection]
) -> dict[uuid.UUID, CompletenessTeacherRef]:
    """Section -> its class teacher (only sections that have one)."""
    wanted = {
        s.id: s.class_teacher_membership_id
        for s in sections
        if s.class_teacher_membership_id is not None
    }
    names = identity.member_display_names(session, set(wanted.values())) if wanted else {}
    return {
        section_id: CompletenessTeacherRef(membership_id=m, display_name=names.get(m))
        for section_id, m in wanted.items()
    }


def _roster(
    session: Session,
    year_id: uuid.UUID,
    sections: Sequence[TenancySection],
    on_date: dt.date | None = None,
) -> dict[uuid.UUID, set[uuid.UUID]]:
    """Section -> students actively enrolled (and started by ``on_date`` when given)."""
    out: dict[uuid.UUID, set[uuid.UUID]] = defaultdict(set)
    for e in students.enrolled_in_sections(
        session, academic_year_id=year_id, section_ids=[s.id for s in sections]
    ):
        if on_date is None or e.started_on is None or e.started_on <= on_date:
            out[e.section_id].add(e.student_id)
    return out


def _attendance_status(
    students_n: int, marked: int, working: bool | None
) -> AttendanceCompletenessStatus:
    if students_n == 0:
        return "no_students"
    if marked == 0:
        return "day_off" if working is False else "not_taken"
    return "complete" if marked >= students_n else "partial"


def attendance_completeness(
    session: Session, ctx: UserContext, on_date: dt.date | None = None
) -> AttendanceCompletenessOut:
    """Per section of the current year: students, students with a status for the date, the
    status and the class teacher (``completeness.read``; FR-ATT-016..019). 409
    ``no_current_year``; 422 ``future_date`` / ``date_out_of_range``."""
    del ctx  # school-wide by the route guard (counts only)
    year = _current_year(session)
    day = on_date or _today()
    if day > _today():
        raise ValidationFailed([_error("date", "future_date")])
    if not year.starts_on <= day <= year.ends_on:
        raise ValidationFailed([_error("date", "date_out_of_range")])
    sections = _sections(session, year.id)
    roster = _roster(session, year.id, sections, day)
    marked_pairs = repo.attendance_pairs(session, day, [s.id for s in sections])
    marked: Counter[uuid.UUID] = Counter(
        section_id
        for section_id, student_id in marked_pairs
        if student_id in roster.get(section_id, set())
    )
    cal = cal_service.year_calendar(session, year)
    classes = _labels(session)
    teachers = _teachers(session, sections)
    rows: list[AttendanceCompletenessRow] = []
    for s in sections:
        info = cal.day(day, s.class_id) if cal is not None else None
        n = len(roster.get(s.id, set()))
        rows.append(
            AttendanceCompletenessRow(
                section_id=s.id,
                class_id=s.class_id,
                label=f"{classes.get(s.class_id, '?')}-{s.name}",
                students=n,
                marked=marked.get(s.id, 0),
                status=_attendance_status(
                    n, marked.get(s.id, 0), info.working if info is not None else None
                ),
                calendar=info.kind if info is not None else None,
                class_teacher=teachers.get(s.id),
            )
        )
    tally = Counter(r.status for r in rows)
    log.info("academics.completeness_read", resource_type="attendance", count=len(rows))
    return AttendanceCompletenessOut(
        on_date=day,
        academic_year_id=year.id,
        sections=len(rows),
        complete=tally["complete"],
        partial=tally["partial"],
        not_taken=tally["not_taken"],
        day_off=tally["day_off"],
        no_students=tally["no_students"],
        reminders_enabled=load_config().completeness.reminders_enabled,
        rows=rows,
    )


def _cell_status(students_n: int, entered: int) -> MarksCellStatus:
    if students_n == 0:
        return "no_students"
    if entered == 0:
        return "missing"
    return "complete" if entered >= students_n else "partial"


def _section_status(students_n: int, cells: Sequence[MarksCompletenessCell]) -> MarksSectionStatus:
    if students_n == 0:
        return "no_students"
    if not cells or all(c.status == "missing" for c in cells):
        return "not_started"
    return "complete" if all(c.status == "complete" for c in cells) else "partial"


def marks_completeness(
    session: Session, ctx: UserContext, exam_id: uuid.UUID, class_id: uuid.UUID | None = None
) -> MarksCompletenessOut:
    """Per section of the exam's year (optionally one class) and subject entered for the exam:
    students and students with a mark or absent (``completeness.read``; FR-MRK-016..019). 404
    for an exam of another school; 422 ``unknown_class``."""
    del ctx  # school-wide by the route guard (counts only)
    exam = repo.get_exam(session, exam_id)
    if exam is None:
        raise NotFound("Exam not found")
    classes = _labels(session)
    if class_id is not None and class_id not in classes:
        raise ValidationFailed([_error("class_id", "unknown_class")])
    sections = _sections(session, exam.academic_year_id, class_id)
    roster = _roster(session, exam.academic_year_id, sections)
    keys = repo.exam_mark_keys(session, exam.id, [s.id for s in sections])
    order = {
        subject.casefold(): i for i, subject in enumerate(repo.exam_subjects(session, exam.id))
    }
    display: dict[str, str] = {}
    entered: dict[tuple[uuid.UUID, str], set[uuid.UUID]] = defaultdict(set)
    for section_id, subject, student_id in keys:
        key = subject.casefold()
        display.setdefault(key, subject)
        if student_id in roster.get(section_id, set()):
            entered[(section_id, key)].add(student_id)
    subject_keys = sorted(display, key=lambda k: (order.get(k, len(order)), k))
    teachers = _teachers(session, sections)
    rows: list[MarksCompletenessRow] = []
    for s in sections:
        n = len(roster.get(s.id, set()))
        cells = [
            MarksCompletenessCell(
                subject=display[k],
                entered=len(entered.get((s.id, k), set())),
                status=_cell_status(n, len(entered.get((s.id, k), set()))),
            )
            for k in subject_keys
        ]
        rows.append(
            MarksCompletenessRow(
                section_id=s.id,
                class_id=s.class_id,
                label=f"{classes.get(s.class_id, '?')}-{s.name}",
                students=n,
                status=_section_status(n, cells),
                subjects=cells,
                class_teacher=teachers.get(s.id),
            )
        )
    tally = Counter(r.status for r in rows)
    log.info(
        "academics.completeness_read", resource_type="exam", resource_id=exam.id, count=len(rows)
    )
    return MarksCompletenessOut(
        exam_id=exam.id,
        exam_name=exam.name,
        held_on=exam.held_on,
        locked=exam.locked_at is not None,
        subjects=[display[k] for k in subject_keys],
        sections=len(rows),
        complete=tally["complete"],
        partial=tally["partial"],
        not_started=tally["not_started"],
        no_students=tally["no_students"],
        reminders_enabled=load_config().completeness.reminders_enabled,
        rows=rows,
    )


def send_completeness_reminder(
    session: Session, ctx: UserContext, data: CompletenessReminderIn
) -> CompletenessReminderOut:
    """One in-app reminder to the section's class teacher (``completeness.remind``;
    FR-ATT-022..023, FR-MRK-022..023). 409 ``reminders_off`` (the default), ``no_class_teacher``,
    ``already_complete``; 422 ``exam_required``; 404 for a section or exam of another school or a
    section not in the year looked at. Once per section, kind and day (attendance) or exam
    (marks): a repeat sends nothing (``sent`` 0). Audit ``completeness.reminder_sent``."""
    if not load_config().completeness.reminders_enabled:
        raise Conflict("Reminders are switched off for this school.", code="reminders_off")
    if data.kind == "attendance":
        out_a = attendance_completeness(session, ctx)
        row_a = next((r for r in out_a.rows if r.section_id == data.section_id), None)
        if row_a is None:
            raise NotFound("Section not found")
        teacher, complete = row_a.class_teacher, row_a.status in ("complete", "no_students")
        dedupe = f"{REMINDER_TEMPLATE}:attendance:{data.section_id}:{out_a.on_date.isoformat()}"
    else:
        if data.exam_id is None:
            raise ValidationFailed([_error("exam_id", "exam_required")])
        out_m = marks_completeness(session, ctx, data.exam_id)
        row_m = next((r for r in out_m.rows if r.section_id == data.section_id), None)
        if row_m is None:
            raise NotFound("Section not found")
        teacher, complete = row_m.class_teacher, row_m.status in ("complete", "no_students")
        dedupe = f"{REMINDER_TEMPLATE}:marks:{data.section_id}:{data.exam_id}"
    if teacher is None:
        raise Conflict("This section has no class teacher to remind.", code="no_class_teacher")
    if complete:
        raise Conflict("Nothing is missing for this section.", code="already_complete")
    sent = notifications.notify(
        session,
        tenant_id=ctx.tenant_id,
        recipients=[teacher.membership_id],
        template_key=REMINDER_TEMPLATE,
        params={"kind": data.kind, "section_id": str(data.section_id)},
        resource_id=data.section_id,
        dedupe_key=dedupe,
    )
    request_id = get_context().get("request_id")
    audit.record(
        session,
        action="completeness.reminder_sent",
        resource_type="section",
        resource_id=data.section_id,
        summary={
            "kind": data.kind,
            "exam_id": data.exam_id,
            "recipient_membership_id": teacher.membership_id,
            "sent": sent,
        },
        actor_type="user",
        request_id=request_id if isinstance(request_id, str) else None,
    )
    log.info("academics.completeness_reminder", resource_id=data.section_id, count=sent)
    return CompletenessReminderOut(sent=sent)


__all__ = [
    "COMPLETENESS_READ",
    "COMPLETENESS_REMIND",
    "attendance_completeness",
    "marks_completeness",
    "send_completeness_reminder",
]
