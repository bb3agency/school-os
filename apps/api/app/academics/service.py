"""Attendance, exams and marks: public API (M5; US-1701..US-1703; FR-ATT-*, FR-MRK-*).

**Scope (SEC-015, invariant 3).** Every read and write names a section; the section must be
reachable with the route's permission (class teachers: their sections; 404 otherwise, never
403, so existence is not revealed). A section's roster is the students actively enrolled in it
in the current academic year whom the caller may read (``student.read_basic``) AND reach with
the permission (``students.list_students_in_scope``); a write naming any other student is
refused as a whole (422).

**Records.** Attendance is one status per student and IST date inside the section's academic
year and not after today; marks are per exam, student and subject with the maximum marks, or
absent. Writes are all-or-nothing upserts (a new value corrects the old one; unchanged rows are
left alone) and are audited in the same transaction with counts and dates only. Each write
queues ``academics.records.changed`` (student ids only) so the early-warning rules
(``app.insights``) look at those students again (FR-ATT-005). Rows are never deleted by the
app: they are school records (retention: school policy; offboarding purges them).

**Sheets (FR-ATT-004, FR-MRK-004).** A preview reads an uploaded, virus-checked ``import_file``
document with the shared readers (formulas never evaluated, zip bombs refused), matches rows to
the roster only and returns entries and problems; it stores nothing. The file is deleted in its
own committed transaction BEFORE it is parsed, so a refused file (e.g. one holding a full
Aadhaar number) never lingers (data minimisation).

Nothing here is ever sent to an AI provider. Logs carry ids and counts only (invariant 5).
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections import Counter
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Final
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.academics import repository as repo
from app.academics import sheets
from app.academics.config import load_config
from app.academics.models import Exam, ExamMark
from app.academics.schemas import (
    AttendanceDayOut,
    AttendanceDayStudent,
    AttendanceEntryIn,
    AttendanceMonthOut,
    AttendanceMonthStudent,
    AttendanceSheetOut,
    AttendanceWrite,
    ExamCreate,
    ExamOut,
    MarkIn,
    MarkOut,
    MarksGridOut,
    MarksSheetOut,
    MarksStudentOut,
    MarksWrite,
    RosterStudentOut,
    SectionOut,
    SheetIn,
    SheetIssueOut,
    WriteResultOut,
)
from app.audit import service as audit
from app.authz.context import UserContext
from app.authz.scope import ensure_section_visible
from app.core import purge as purging
from app.core.db import tenant_session
from app.core.errors import Conflict, NotFound, ValidationFailed
from app.core.ids import new_id
from app.core.logging import get_context, get_logger
from app.core.records import RecordTable
from app.core.redaction import contains_full_aadhaar
from app.core.spreadsheet import FileKind, SpreadsheetError, raw_rows
from app.documents import service as documents
from app.ops import service as ops
from app.students import service as students
from app.tenancy import service as tenancy
from app.tenancy.schemas import AcademicYearOut
from app.tenancy.schemas import SectionOut as TenancySection

log = get_logger(__name__)

ATTENDANCE_RECORD: Final = "attendance.record"
ATTENDANCE_READ: Final = "attendance.read"
EXAM_MANAGE: Final = "exam.manage"
MARKS_RECORD: Final = "marks.record"
MARKS_READ: Final = "marks.read"

RECORDS_CHANGED_EVENT: Final = "academics.records.changed"
"""Outbox event after attendance or marks writes (``{"student_ids": [...], "kind": ...}``, at
most :data:`EVENT_CHUNK` ids); consumed by ``app.insights`` (FR-ATT-005, FR-EW-005)."""
EVENT_CHUNK: Final = 100
IST: Final = ZoneInfo("Asia/Kolkata")
SHEET_REASON: Final = "records_sheet_read"
AADHAAR_CODE: Final = "aadhaar_full_number_rejected"
_MIME_KINDS: Final[dict[str, FileKind]] = {
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "xlsx",
    "text/csv": "csv",
}
_ERRORS_SHOWN: Final = 50


# --- helpers --------------------------------------------------------------------------------------


def today_ist(now: dt.datetime | None = None) -> dt.date:
    return (now or dt.datetime.now(dt.UTC)).astimezone(IST).date()


def _request_id() -> str | None:
    value = get_context().get("request_id")
    return value if isinstance(value, str) else None


def _error(field: str, code: str) -> dict[str, str]:
    return {"field": field, "code": code, "message_key": f"errors.{code}"}


def _refuse(errors: list[dict[str, str]]) -> None:
    if errors:
        raise ValidationFailed(
            errors[:_ERRORS_SHOWN], detail="Some entries cannot be saved. Fix them and try again."
        )


def _audit(
    session: Session,
    action: str,
    resource_type: str,
    resource_id: uuid.UUID,
    summary: Mapping[str, Any],
) -> None:
    audit.record(
        session,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        summary=summary,
        actor_type="user",
        request_id=_request_id(),
    )


def _queue_rules(session: Session, student_ids: Iterable[uuid.UUID], kind: str) -> None:
    """FR-ATT-005: the early-warning rules look at these students again (outbox, ids only)."""
    ids = sorted(set(student_ids), key=str)
    for start in range(0, len(ids), EVENT_CHUNK):
        ops.enqueue_event(
            session,
            RECORDS_CHANGED_EVENT,
            {"student_ids": ids[start : start + EVENT_CHUNK], "kind": kind},
        )


@dataclass(frozen=True, slots=True)
class _Section:
    section: TenancySection
    year: AcademicYearOut
    out: SectionOut


def _section(
    session: Session, ctx: UserContext, section_id: uuid.UUID, permission: str
) -> _Section:
    """The section if the caller reaches it with ``permission`` (404 otherwise)."""
    try:
        section = tenancy.get_section(session, section_id)
    except NotFound:
        raise NotFound("Section not found") from None
    ensure_section_visible(ctx, permission, section)
    year = tenancy.get_academic_year(session, section.academic_year_id)
    klass = tenancy.get_class(session, section.class_id)
    out = SectionOut(
        id=section.id,
        class_id=section.class_id,
        academic_year_id=section.academic_year_id,
        label=f"{klass.code}-{section.name}",
    )
    return _Section(section=section, year=year, out=out)


def _roll_key(roll: str | None) -> tuple[int, int, str]:
    if roll is None:
        return (2, 0, "")
    digits = roll.strip()
    return (0, int(digits), digits) if digits.isdigit() else (1, 0, digits.casefold())


def _roster(
    session: Session, ctx: UserContext, section: TenancySection, permission: str
) -> list[RosterStudentOut]:
    """Current-year students of the section the caller reaches (read + ``permission``), in
    roll-number order."""
    ids = students.list_students_in_scope(
        session, ctx, section_ids=[section.id], permissions=(permission,)
    )
    if not ids:
        return []
    enrolments = students.active_enrolments(session, ids)
    values = students.canonical_values(session, ids, ["full_name", "admission_no"])
    out: list[RosterStudentOut] = []
    for sid in ids:
        roll = next(
            (e.roll_no for e in enrolments.get(sid, []) if e.section_id == section.id), None
        )
        vals = values.get(sid, {})
        name = vals.get("full_name")
        adm = vals.get("admission_no")
        out.append(
            RosterStudentOut(
                student_id=sid,
                full_name=name.value if name else None,
                admission_no=adm.value if adm else None,
                roll_no=roll,
            )
        )
    return sorted(out, key=lambda s: (_roll_key(s.roll_no), (s.full_name or "").casefold()))


def _check_dates(
    errors: list[dict[str, str]], field: str, day: dt.date, year: AcademicYearOut, today: dt.date
) -> None:
    if day > today:
        errors.append(_error(field, "future_date"))
    elif not year.starts_on <= day <= year.ends_on:
        errors.append(_error(field, "date_out_of_range"))


# --- attendance -----------------------------------------------------------------------------------


def _counts(statuses: Iterable[str]) -> dict[str, int]:
    found = Counter(statuses)
    return {s: found.get(s, 0) for s in ("present", "absent", "late", "leave")}


def attendance_day(
    session: Session, ctx: UserContext, section_id: uuid.UUID, on_date: dt.date
) -> AttendanceDayOut:
    """One day of a section's register (``attendance.read``; FR-ATT-003)."""
    sec = _section(session, ctx, section_id, ATTENDANCE_READ)
    roster = _roster(session, ctx, sec.section, ATTENDANCE_READ)
    rows = repo.section_marks(session, sec.section.id, on_date, on_date)
    by_student = {r.student_id: r.status for r in rows}
    shown = {s.student_id for s in roster}
    return AttendanceDayOut(
        section=sec.out,
        on_date=on_date,
        marked=bool(rows),
        students=[
            AttendanceDayStudent(student=s, status=by_student.get(s.student_id)) for s in roster
        ],
        counts=_counts(v for k, v in by_student.items() if k in shown),
    )


def _month_bounds(month: str) -> tuple[dt.date, dt.date]:
    year, mon = (int(p) for p in month.split("-"))
    first = dt.date(year, mon, 1)
    nxt = dt.date(year + (mon == 12), mon % 12 + 1, 1)
    return first, nxt - dt.timedelta(days=1)


def attendance_month(
    session: Session, ctx: UserContext, section_id: uuid.UUID, month: str
) -> AttendanceMonthOut:
    """A month of a section's register: school days (dates with any mark) x students
    (``attendance.read``; FR-ATT-003; A4 print in the web)."""
    sec = _section(session, ctx, section_id, ATTENDANCE_READ)
    roster = _roster(session, ctx, sec.section, ATTENDANCE_READ)
    first, last = _month_bounds(month)
    rows = repo.section_marks(session, sec.section.id, first, last)
    days = sorted({r.on_date for r in rows})
    by_student: dict[uuid.UUID, dict[str, str]] = {}
    for r in rows:
        by_student.setdefault(r.student_id, {})[r.on_date.isoformat()] = r.status
    return AttendanceMonthOut(
        section=sec.out,
        month=month,
        school_days=days,
        students=[
            AttendanceMonthStudent(
                student=s,
                days=by_student.get(s.student_id, {}),
                counts=_counts(by_student.get(s.student_id, {}).values()),
            )
            for s in roster
        ],
    )


def record_attendance(
    session: Session, ctx: UserContext, section_id: uuid.UUID, data: AttendanceWrite
) -> WriteResultOut:
    """Store a section's statuses for one or more dates, all or nothing (``attendance.record``;
    FR-ATT-002). 422 when a student is not in the section's roster, a date is in the future or
    outside the section's academic year, or an entry repeats. Audit ``attendance.recorded``."""
    cfg = load_config().attendance
    sec = _section(session, ctx, section_id, ATTENDANCE_RECORD)
    if len(data.entries) > cfg.max_entries:
        _refuse([_error("entries", "too_many_entries")])
    roster = {s.student_id for s in _roster(session, ctx, sec.section, ATTENDANCE_RECORD)}
    today = today_ist()
    errors: list[dict[str, str]] = []
    seen: set[tuple[uuid.UUID, dt.date]] = set()
    for i, entry in enumerate(data.entries):
        if entry.student_id not in roster:
            errors.append(_error(f"entries.{i}.student_id", "not_in_section"))
        _check_dates(errors, f"entries.{i}.on_date", entry.on_date, sec.year, today)
        key = (entry.student_id, entry.on_date)
        if key in seen:
            errors.append(_error(f"entries.{i}", "duplicate_entry"))
        seen.add(key)
    _refuse(errors)
    inserted, updated = repo.upsert_attendance(
        session,
        [
            {
                "id": new_id(),
                "student_id": e.student_id,
                "section_id": sec.section.id,
                "on_date": e.on_date,
                "status": e.status,
                "source": data.source,
                "recorded_by": ctx.user_id,
            }
            for e in data.entries
        ],
    )
    dates = sorted({e.on_date for e in data.entries})
    written = inserted + updated
    _audit(
        session,
        "attendance.recorded",
        "section",
        sec.section.id,
        {
            "source": data.source,
            "entries": len(data.entries),
            "inserted": inserted,
            "corrected": updated,
            "days": len(dates),
            "first_date": dates[0].isoformat(),
            "last_date": dates[-1].isoformat(),
        },
    )
    if written:
        _queue_rules(session, {e.student_id for e in data.entries}, "attendance")
    log.info("academics.attendance_recorded", resource_id=sec.section.id, count=written)
    return WriteResultOut(written=written, unchanged=len(data.entries) - written, dates=dates)


# --- sheets ---------------------------------------------------------------------------------------


def _consume_sheet(
    ctx: UserContext, document_id: uuid.UUID, action: str, section_id: uuid.UUID
) -> tuple[bytes, FileKind]:
    """Read an uploaded ``import_file`` the caller can see, then delete it, committed in its own
    transaction (whatever the parse finds, the file does not linger). 404 when not visible;
    409 ``document_not_ready`` while the virus check runs; 415/413 for other types or sizes."""
    limits = load_config().sheets
    with tenant_session(ctx.tenant_id, ctx.user_id) as session:
        if not documents.is_visible(session, ctx, document_id):
            raise NotFound("Document not found")
        obj = documents.document_object(session, document_id)
        if obj.purpose != "import_file":
            raise ValidationFailed([_error("document_id", "not_an_import_file")])
        kind = _MIME_KINDS.get(obj.mime_type)
        if kind is None:
            raise documents.UnsupportedFileType("Use an XLSX or CSV file.")
        if obj.size_bytes > limits.max_file_bytes:
            raise documents.FileTooLarge("Sheets for one section can be at most 2 MB.")
        data = documents.read_document_object(session, obj)
        documents.delete_for_retention(session, document_id, reason=SHEET_REASON)
        _audit(
            session,
            action,
            "section",
            section_id,
            {"document_id": document_id, "file_kind": kind, "size_bytes": len(data)},
        )
    return data, kind


def _sheet_refused(code: str) -> ValidationFailed:
    detail = (
        "Don't put Aadhaar numbers in the sheet. Remove them and upload it again."
        if code == AADHAAR_CODE
        else "This sheet cannot be read. Check it against the sample and upload it again."
    )
    return ValidationFailed([_error("document_id", code)], detail=detail)


def _issues(items: Sequence[sheets.Issue]) -> list[SheetIssueOut]:
    return [SheetIssueOut(row=i.row, column=i.column, code=i.code) for i in items]


def _sheet_roster(
    session: Session, ctx: UserContext, sec: _Section, permission: str
) -> sheets.Roster:
    return sheets.Roster.build(
        (s.student_id, s.admission_no, s.roll_no)
        for s in _roster(session, ctx, sec.section, permission)
    )


def preview_attendance_sheet(
    ctx: UserContext, section_id: uuid.UUID, data: SheetIn
) -> AttendanceSheetOut:
    """Read an attendance sheet for a section (``attendance.record``; FR-ATT-004): entries and
    problems, nothing stored; the file is deleted once read. Audit ``attendance.sheet_read``."""
    with tenant_session(ctx.tenant_id, ctx.user_id) as session:
        sec = _section(session, ctx, section_id, ATTENDANCE_RECORD)
        roster = _sheet_roster(session, ctx, sec, ATTENDANCE_RECORD)
    blob, kind = _consume_sheet(ctx, data.document_id, "attendance.sheet_read", section_id)
    cfg = load_config()
    last = min(sec.year.ends_on, today_ist())
    try:
        sheet = sheets.read_attendance(
            raw_rows(blob, kind, cfg.sheets.limits),
            roster,
            first=sec.year.starts_on,
            last=sec.year.ends_on,
            today=last,
            cfg=cfg,
        )
    except (SpreadsheetError, sheets.SheetRefused) as exc:
        raise _sheet_refused(exc.code) from None
    return AttendanceSheetOut(
        entries=[
            AttendanceEntryIn(student_id=e.student_id, on_date=e.on_date, status=e.status)
            for e in sheet.entries
        ],
        dates=list(sheet.dates),
        students=sheet.students,
        issues=_issues(sheet.issues),
        issue_count=sheet.issue_count,
    )


# --- exams ----------------------------------------------------------------------------------------


def _exam_out(exam: Exam) -> ExamOut:
    return ExamOut(
        id=exam.id,
        academic_year_id=exam.academic_year_id,
        name=exam.name,
        held_on=exam.held_on,
        version=exam.version,
    )


def list_exams(
    session: Session, ctx: UserContext, academic_year_id: uuid.UUID | None = None
) -> list[ExamOut]:
    """Exams of an academic year (default: the current one), by date. Exam names and dates are
    school-internal (C1); marks are read per section."""
    year_id = academic_year_id
    if year_id is None:
        current = tenancy.get_current_academic_year(session)
        if current is None:
            return []
        year_id = current.id
    return [_exam_out(e) for e in repo.list_exams(session, year_id)]


def create_exam(session: Session, ctx: UserContext, data: ExamCreate) -> ExamOut:
    """Add an exam to the current academic year (``exam.manage``; FR-MRK-001). 409
    ``no_current_year``; 422 ``date_out_of_range`` / ``exam_name_taken``. Audit
    ``exam.created``."""
    year = tenancy.get_current_academic_year(session)
    if year is None:
        raise Conflict("Set the current academic year first.", code="no_current_year")
    errors: list[dict[str, str]] = []
    if contains_full_aadhaar(data.name):
        errors.append(_error("name", AADHAAR_CODE))
    if not year.starts_on <= data.held_on <= year.ends_on:
        errors.append(_error("held_on", "date_out_of_range"))
    if repo.exam_name_taken(session, year.id, data.name):
        errors.append(_error("name", "exam_name_taken"))
    _refuse(errors)
    exam = repo.insert_exam(
        session,
        {
            "id": new_id(),
            "academic_year_id": year.id,
            "name": data.name,
            "held_on": data.held_on,
            "created_by": ctx.user_id,
        },
    )
    _audit(
        session,
        "exam.created",
        "exam",
        exam.id,
        {"academic_year_id": year.id, "held_on": exam.held_on.isoformat()},
    )
    return _exam_out(exam)


def _exam(session: Session, exam_id: uuid.UUID) -> Exam:
    exam = repo.get_exam(session, exam_id)
    if exam is None:
        raise NotFound("Exam not found")
    return exam


# --- marks ----------------------------------------------------------------------------------------


def percent_of(rows: Iterable[tuple[Decimal | None, Decimal, bool]]) -> float | None:
    """FR-MRK-005: sum of marks / sum of maximum marks over the papers with marks (absent papers
    left out), as a percentage with one decimal; None when no paper has marks."""
    got = total = Decimal(0)
    for marks, max_marks, absent in rows:
        if absent or marks is None:
            continue
        got += marks
        total += max_marks
    if total <= 0:
        return None
    return float(round(got * 100 / total, 1))


def section_marks(
    session: Session, ctx: UserContext, section_id: uuid.UUID, exam_id: uuid.UUID
) -> MarksGridOut:
    """A section's marks for one exam (``marks.read``; FR-MRK-003)."""
    sec = _section(session, ctx, section_id, MARKS_READ)
    exam = _exam(session, exam_id)
    roster = _roster(session, ctx, sec.section, MARKS_READ)
    rows = repo.exam_marks(session, exam.id, [s.student_id for s in roster])
    by_student: dict[uuid.UUID, list[ExamMark]] = {}
    subjects: dict[str, None] = {}
    for r in rows:
        by_student.setdefault(r.student_id, []).append(r)
        subjects.setdefault(r.subject, None)
    return MarksGridOut(
        section=sec.out,
        exam=_exam_out(exam),
        subjects=list(subjects),
        students=[
            MarksStudentOut(
                student=s,
                marks=[
                    MarkOut(
                        subject=m.subject, max_marks=m.max_marks, marks=m.marks, absent=m.absent
                    )
                    for m in by_student.get(s.student_id, [])
                ],
                percent=percent_of(
                    (m.marks, m.max_marks, m.absent) for m in by_student.get(s.student_id, [])
                ),
            )
            for s in roster
        ],
    )


def record_marks(
    session: Session,
    ctx: UserContext,
    section_id: uuid.UUID,
    exam_id: uuid.UUID,
    data: MarksWrite,
) -> WriteResultOut:
    """Store a section's marks for one exam, all or nothing (``marks.record``; FR-MRK-002). 422
    when a student is not in the roster, marks exceed the maximum, absent papers carry marks,
    a subject is too long or holds an Aadhaar-like number, or an entry repeats. Audit
    ``marks.recorded``."""
    cfg = load_config().marks
    sec = _section(session, ctx, section_id, MARKS_RECORD)
    exam = _exam(session, exam_id)
    errors: list[dict[str, str]] = []
    if exam.academic_year_id != sec.section.academic_year_id:
        errors.append(_error("exam_id", "exam_other_year"))
    if len(data.entries) > cfg.max_entries:
        errors.append(_error("entries", "too_many_entries"))
    _refuse(errors)
    roster = {s.student_id for s in _roster(session, ctx, sec.section, MARKS_RECORD)}
    seen: set[tuple[uuid.UUID, str]] = set()
    subjects: set[str] = set()
    for i, entry in enumerate(data.entries):
        field = f"entries.{i}"
        if entry.student_id not in roster:
            errors.append(_error(f"{field}.student_id", "not_in_section"))
        if contains_full_aadhaar(entry.subject):
            errors.append(_error(f"{field}.subject", AADHAAR_CODE))
        if entry.absent != (entry.marks is None):
            errors.append(_error(f"{field}.marks", "absent_has_no_marks"))
        elif entry.marks is not None and entry.marks > entry.max_marks:
            errors.append(_error(f"{field}.marks", "marks_over_max"))
        if entry.max_marks > cfg.max_marks_ceiling:
            errors.append(_error(f"{field}.max_marks", "bad_max_marks"))
        key = (entry.student_id, entry.subject.casefold())
        if key in seen:
            errors.append(_error(field, "duplicate_entry"))
        seen.add(key)
        subjects.add(entry.subject.casefold())
    if len(subjects) > cfg.max_subjects:
        errors.append(_error("entries", "too_many_subjects"))
    _refuse(errors)
    inserted, updated = repo.upsert_marks(
        session,
        [
            {
                "id": new_id(),
                "exam_id": exam.id,
                "student_id": e.student_id,
                "section_id": sec.section.id,
                "subject": e.subject,
                "max_marks": e.max_marks,
                "marks": e.marks,
                "absent": e.absent,
                "source": data.source,
                "recorded_by": ctx.user_id,
            }
            for e in data.entries
        ],
    )
    written = inserted + updated
    _audit(
        session,
        "marks.recorded",
        "exam",
        exam.id,
        {
            "section_id": sec.section.id,
            "source": data.source,
            "entries": len(data.entries),
            "inserted": inserted,
            "corrected": updated,
            "subjects": len(subjects),
        },
    )
    if written:
        _queue_rules(session, {e.student_id for e in data.entries}, "marks")
    log.info("academics.marks_recorded", resource_id=exam.id, count=written)
    return WriteResultOut(written=written, unchanged=len(data.entries) - written)


def preview_marks_sheet(
    ctx: UserContext, section_id: uuid.UUID, exam_id: uuid.UUID, data: SheetIn
) -> MarksSheetOut:
    """Read a marks sheet for a section and exam (``marks.record``; FR-MRK-004): entries and
    problems, nothing stored; the file is deleted once read. Audit ``marks.sheet_read``."""
    with tenant_session(ctx.tenant_id, ctx.user_id) as session:
        sec = _section(session, ctx, section_id, MARKS_RECORD)
        _exam(session, exam_id)
        roster = _sheet_roster(session, ctx, sec, MARKS_RECORD)
    blob, kind = _consume_sheet(ctx, data.document_id, "marks.sheet_read", section_id)
    cfg = load_config()
    try:
        sheet = sheets.read_marks(raw_rows(blob, kind, cfg.sheets.limits), roster, cfg=cfg)
    except (SpreadsheetError, sheets.SheetRefused) as exc:
        raise _sheet_refused(exc.code) from None
    return MarksSheetOut(
        entries=[
            MarkIn(
                student_id=e.student_id,
                subject=e.subject,
                max_marks=e.max_marks,
                marks=e.marks,
                absent=e.absent,
            )
            for e in sheet.entries
        ],
        subjects=list(sheet.subjects),
        students=sheet.students,
        issues=_issues(sheet.issues),
        issue_count=sheet.issue_count,
    )


# --- system reads for the early-warning rules and the timeline (app.insights) ------------------


@dataclass(frozen=True, slots=True)
class DayMark:
    on_date: dt.date
    status: str


def attendance_history(
    session: Session, student_ids: Collection[uuid.UUID], *, since: dt.date | None = None
) -> dict[uuid.UUID, list[DayMark]]:
    """Every student's statuses, oldest first. System read: ``student_ids`` MUST be students
    the caller already reached (``insights`` checks scope, or the rules job for the school)."""
    out: dict[uuid.UUID, list[DayMark]] = {sid: [] for sid in student_ids}
    for student_id, on_date, status in repo.student_marks(session, student_ids, since=since):
        out.setdefault(student_id, []).append(DayMark(on_date, status))
    return out


@dataclass(frozen=True, slots=True)
class ExamResult:
    exam_id: uuid.UUID
    name: str
    held_on: dt.date
    academic_year_id: uuid.UUID
    percent: float | None
    papers: int
    absent_papers: int


def exam_results(
    session: Session,
    student_ids: Collection[uuid.UUID],
    *,
    academic_year_id: uuid.UUID | None = None,
) -> dict[uuid.UUID, list[ExamResult]]:
    """Every student's results per exam (FR-MRK-005), oldest exam first. Same contract as
    :func:`attendance_history` for ``student_ids``."""
    grouped: dict[uuid.UUID, dict[uuid.UUID, tuple[Exam, list[ExamMark]]]] = {}
    for mark, exam in repo.results_of(session, student_ids, academic_year_id):
        per = grouped.setdefault(mark.student_id, {})
        per.setdefault(exam.id, (exam, []))[1].append(mark)
    out: dict[uuid.UUID, list[ExamResult]] = {sid: [] for sid in student_ids}
    for sid, exams in grouped.items():
        results = [
            ExamResult(
                exam_id=exam.id,
                name=exam.name,
                held_on=exam.held_on,
                academic_year_id=exam.academic_year_id,
                percent=percent_of((m.marks, m.max_marks, m.absent) for m in marks),
                papers=len(marks),
                absent_papers=sum(1 for m in marks if m.absent),
            )
            for exam, marks in exams.values()
        ]
        out[sid] = sorted(results, key=lambda r: (r.held_on, str(r.exam_id)))
    return out


# --- full export and offboarding ------------------------------------------------------------------


def export_records(session: Session) -> list[RecordTable]:
    """Worker only: attendance, exams and marks of the current school for its full data export
    (``app.admin``; the caller checked ``tenant.export_all`` and audits the export)."""
    return repo.export_tables(session)


_PURGE = purging.PurgeTables(
    deleted=("sis.exam_marks", "sis.exams", "sis.attendance_marks"),
)


def tenant_data_counts(session: Session) -> dict[str, int]:
    """Rows of the current school in this module's tables (offboarding inventory)."""
    return _PURGE.count(session)


def purge_tenant_data(session: Session) -> dict[str, int]:
    """Delete the current school's rows of this module (offboarding only; ADR-0029)."""
    return _PURGE.delete(session)


tenancy.register_data_owner(
    tenancy.TenantDataOwner(name="academics", count=tenant_data_counts, purge=purge_tenant_data)
)


__all__ = [
    "ATTENDANCE_READ",
    "ATTENDANCE_RECORD",
    "EXAM_MANAGE",
    "MARKS_READ",
    "MARKS_RECORD",
    "RECORDS_CHANGED_EVENT",
    "DayMark",
    "ExamResult",
    "attendance_day",
    "attendance_history",
    "attendance_month",
    "create_exam",
    "exam_results",
    "export_records",
    "list_exams",
    "percent_of",
    "preview_attendance_sheet",
    "preview_marks_sheet",
    "purge_tenant_data",
    "record_attendance",
    "record_marks",
    "section_marks",
    "tenant_data_counts",
    "today_ist",
]
