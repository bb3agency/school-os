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

**Exam lock (FT-01; FR-MRK-008..FR-MRK-016).** ``exam.manage`` locks an exam; a locked exam
refuses marks writes and marks-sheet previews (409 ``exam_locked``). Only the principal unlocks
(``exam.unlock``, step-up MFA) with a reason; corrections after an unlock need no second person.
Every change of a stored mark (after its first entry, locked or not) appends a row to
``sis.exam_mark_history`` with the previous and new value, who, when and the reason, in the
same transaction as the write; changes made after the exam was first locked are shown as
"Changed after lock". Lock, unlock and marks writes of one exam serialise on the same advisory
lock, so a write never slips past a lock.

**Enrolment start (FT-09; FR-ATT-010..FR-ATT-016).** Attendance dated before a student's
explicit enrolment start in the section (``students.ActiveEnrolment.started_on_explicit``) is
refused (422 ``before_enrolment``; a per-row issue in sheets). Implicit starts are not checked.

Nothing here is ever sent to an AI provider. Logs carry ids and counts only (invariant 5).
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections import Counter
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Final
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.academics import calendar_service as cal_service
from app.academics import repository as repo
from app.academics import sheets
from app.academics.calendar_service import (
    CALENDAR_MANAGE,
    CALENDAR_READ,
    HOLIDAY_CODE,
    AttendanceSummary,
    add_entry,
    attendance_summary,
    calendar_month,
    get_calendar,
    remove_entry,
    set_pattern,
    suggestions,
)
from app.academics.completeness import (
    COMPLETENESS_READ,
    COMPLETENESS_REMIND,
    attendance_completeness,
    marks_completeness,
    send_completeness_reminder,
)
from app.academics.config import load_config
from app.academics.models import Exam, ExamMark, ExamMarkHistory
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
    ExamUnlockIn,
    MarkChangeOut,
    MarkIn,
    MarkOut,
    MarksGridOut,
    MarksHistoryOut,
    MarksSheetOut,
    MarksStudentOut,
    MarksWrite,
    RecordSectionOut,
    RosterStudentOut,
    SheetIn,
    SheetIssueOut,
    WriteResultOut,
)
from app.academics.subjects import subject_report
from app.academics.year_end import (
    YEAR_RESULT_CONFIRM,
    YEAR_RESULT_READ,
    confirm_results,
    held_back,
    progression,
    progression_csv,
    tc_suggestions,
    year_results,
)
from app.audit import service as audit
from app.authz.context import UserContext
from app.authz.scope import ensure_section_visible
from app.core import purge as purging
from app.core.db import tenant_session
from app.core.errors import Conflict, NotFound, PreconditionFailed, ValidationFailed
from app.core.ids import new_id
from app.core.logging import get_context, get_logger
from app.core.records import RecordTable
from app.core.redaction import contains_full_aadhaar
from app.core.spreadsheet import FileKind, SpreadsheetError, raw_rows
from app.documents import service as documents
from app.identity import service as identity
from app.ops import service as ops
from app.students import service as students
from app.tenancy import service as tenancy
from app.tenancy.schemas import AcademicYearOut
from app.tenancy.schemas import SectionOut as TenancySection

log = get_logger(__name__)

ATTENDANCE_RECORD: Final = "attendance.record"
ATTENDANCE_READ: Final = "attendance.read"
EXAM_MANAGE: Final = "exam.manage"
EXAM_UNLOCK: Final = "exam.unlock"
EXAM_LOCKED_CODE: Final = "exam_locked"
BEFORE_ENROLMENT_CODE: Final = "before_enrolment"
HISTORY_LIMIT: Final = 2000
MARKS_RECORD: Final = "marks.record"
MARKS_READ: Final = "marks.read"

RECORDS_CHANGED_EVENT: Final = "academics.records.changed"
"""Outbox event after attendance or marks writes (``{"student_ids": [...], "kind": ...}``, at
most :data:`EVENT_CHUNK` ids); consumed by ``app.insights`` (FR-ATT-005, FR-EW-005)."""
EVENT_CHUNK: Final = 100
IST: Final = ZoneInfo("Asia/Kolkata")
SHEET_REASON: Final = "records_sheet_read"
AADHAAR_CODE: Final = "aadhaar_full_number_rejected"
OTHER_SECTION_CODE: Final = "recorded_in_another_section"
"""A scoped recorder may not overwrite a record made under another section (the section the
student was in that day or for that exam); school-wide recorders may (audit 2026-10-05 A-04)."""
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
    out: RecordSectionOut


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
    out = RecordSectionOut(
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


def _explicit_starts(
    session: Session, student_ids: Collection[uuid.UUID], section_id: uuid.UUID
) -> dict[uuid.UUID, dt.date]:
    """FT-09 (FR-ATT-012): the explicit start date of each student's active enrolment in the
    section. Implicit starts (defaulted dates) are left out: they are not checked."""
    out: dict[uuid.UUID, dt.date] = {}
    for sid, enrolments in students.active_enrolments(session, student_ids).items():
        for e in enrolments:
            if e.section_id == section_id and e.started_on_explicit and e.started_on is not None:
                out[sid] = e.started_on
    return out


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
    versions = {r.student_id: r.version for r in rows}
    shown = {s.student_id for s in roster}
    cal = cal_service.year_calendar(session, sec.year)
    return AttendanceDayOut(
        section=sec.out,
        on_date=on_date,
        calendar=(
            cal_service.day_out(cal.day(on_date, sec.section.class_id)) if cal is not None else None
        ),
        marked=bool(rows),
        students=[
            AttendanceDayStudent(
                student=s,
                status=by_student.get(s.student_id),
                version=versions.get(s.student_id, 0),
            )
            for s in roster
        ],
        counts=_counts(v for k, v in by_student.items() if k in shown),
        draft_keep_hours=load_config().attendance.draft_keep_hours,
    )


def _month_bounds(month: str) -> tuple[dt.date, dt.date]:
    """First and last day of ``YYYY-MM``; 422 ``invalid_month`` outside the calendar (years 1
    to 9999)."""
    return cal_service.month_bounds(month)


def attendance_month(
    session: Session, ctx: UserContext, section_id: uuid.UUID, month: str
) -> AttendanceMonthOut:
    """A month of a section's register (``attendance.read``; FR-ATT-003; A4 print in the web).
    Without a calendar the school days are the dates with any mark; with one (FT-06,
    FR-CAL-011) they are the working days up to today plus any marked date, and each student's
    counts add ``not_marked`` (working days up to today, from their explicit start, unmarked)."""
    sec = _section(session, ctx, section_id, ATTENDANCE_READ)
    roster = _roster(session, ctx, sec.section, ATTENDANCE_READ)
    first, last = _month_bounds(month)
    rows = repo.section_marks(session, sec.section.id, first, last)
    marked = {r.on_date for r in rows}
    by_student: dict[uuid.UUID, dict[str, str]] = {}
    for r in rows:
        by_student.setdefault(r.student_id, {})[r.on_date.isoformat()] = r.status
    cal = cal_service.year_calendar(session, sec.year)
    working = cal.working_days(first, last, sec.section.class_id) if cal is not None else []
    today = today_ist()
    open_days = [d for d in working if d <= today]
    starts = (
        _explicit_starts(session, [s.student_id for s in roster], sec.section.id)
        if cal is not None
        else {}
    )

    def counts(student_id: uuid.UUID) -> dict[str, int]:
        days = by_student.get(student_id, {})
        out = _counts(days.values())
        if cal is not None:
            start = starts.get(student_id)
            out["not_marked"] = sum(
                1 for d in open_days if d.isoformat() not in days and (start is None or d >= start)
            )
        return out

    return AttendanceMonthOut(
        section=sec.out,
        month=month,
        school_days=sorted(marked | set(open_days)),
        calendar_set=cal is not None,
        working_days=working,
        students=[
            AttendanceMonthStudent(
                student=s,
                days=by_student.get(s.student_id, {}),
                counts=counts(s.student_id),
            )
            for s in roster
        ],
    )


@dataclass(frozen=True, slots=True)
class PeriodAttendance:
    """One student's marks over a period (FO-11): counts per status, no dates."""

    student: RosterStudentOut
    present: int
    late: int
    absent: int
    leave: int


@dataclass(frozen=True, slots=True)
class SectionPeriodAttendance:
    """A section's attendance counts over a period (FO-11, FR-EXP-020)."""

    section: RecordSectionOut
    year: AcademicYearOut
    first: dt.date
    last: dt.date
    students: list[PeriodAttendance]


def attendance_period(
    session: Session, ctx: UserContext, section_id: uuid.UUID, first: dt.date, last: dt.date
) -> SectionPeriodAttendance:
    """Each current student's attendance counts in a section between ``first`` and ``last``
    (inclusive), in roll-number order: the scheme verification lists of ``app.exports`` (FO-11,
    FR-EXP-020). ``attendance.read`` in the section's scope (404 outside it) and
    ``student.read_basic`` (the roster). 422 ``invalid_period`` when ``first`` is after
    ``last``, ``date_out_of_range`` outside the section's academic year, ``future_date`` after
    today. Counts only; the caller decides what a percentage means (no eligibility here)."""
    sec = _section(session, ctx, section_id, ATTENDANCE_READ)
    errors: list[dict[str, str]] = []
    if first > last:
        errors.append(_error("from_date", "invalid_period"))
    else:
        today = today_ist()
        _check_dates(errors, "from_date", first, sec.year, today)
        _check_dates(errors, "to_date", last, sec.year, today)
    if errors:
        raise ValidationFailed(
            errors, detail="Choose a period inside the academic year, up to today."
        )
    roster = _roster(session, ctx, sec.section, ATTENDANCE_READ)
    found: dict[uuid.UUID, Counter[str]] = {}
    for row in repo.section_marks(session, sec.section.id, first, last):
        found.setdefault(row.student_id, Counter())[row.status] += 1
    students_out: list[PeriodAttendance] = []
    for s in roster:
        c = found.get(s.student_id, Counter())
        students_out.append(
            PeriodAttendance(
                student=s,
                present=c.get("present", 0),
                late=c.get("late", 0),
                absent=c.get("absent", 0),
                leave=c.get("leave", 0),
            )
        )
    return SectionPeriodAttendance(
        section=sec.out, year=sec.year, first=first, last=last, students=students_out
    )


def _days_off(
    session: Session, sec: _Section, entries: Iterable[AttendanceEntryIn]
) -> set[dt.date]:
    """FT-06 (FR-CAL-007): the dates among ``entries`` that are not working days for the
    section's class; none when the year has no calendar."""
    cal = cal_service.year_calendar(session, sec.year)
    asked = {e.on_date for e in entries}
    if cal is None or not asked:
        return set()
    return {
        d.on_date
        for d in cal.days(min(asked), max(asked), sec.section.class_id)
        if not d.working and d.on_date in asked
    }


def record_attendance(
    session: Session, ctx: UserContext, section_id: uuid.UUID, data: AttendanceWrite
) -> WriteResultOut:
    """Store a section's statuses for one or more dates, all or nothing (``attendance.record``;
    FR-ATT-002). 422 when a student is not in the section's roster, a date is in the future or
    outside the section's academic year or before the student's explicit enrolment start
    (``before_enrolment``, FR-ATT-012) or not a working day in the school calendar
    (``holiday``, FR-CAL-007), or an entry repeats; and, for a scoped recorder, when
    the day is already recorded under another section (``recorded_in_another_section``); 412
    ``attendance_changed`` when an entry's ``expected_version`` is stale (XA-06).
    Audit ``attendance.recorded``."""
    cfg = load_config().attendance
    sec = _section(session, ctx, section_id, ATTENDANCE_RECORD)
    if len(data.entries) > cfg.max_entries:
        _refuse([_error("entries", "too_many_entries")])
    roster = {s.student_id for s in _roster(session, ctx, sec.section, ATTENDANCE_RECORD)}
    starts = _explicit_starts(session, roster, sec.section.id)
    closed = _days_off(session, sec, data.entries)
    today = today_ist()
    errors: list[dict[str, str]] = []
    seen: set[tuple[uuid.UUID, dt.date]] = set()
    for i, entry in enumerate(data.entries):
        if entry.student_id not in roster:
            errors.append(_error(f"entries.{i}.student_id", "not_in_section"))
        _check_dates(errors, f"entries.{i}.on_date", entry.on_date, sec.year, today)
        start = starts.get(entry.student_id)
        if start is not None and entry.on_date < start:
            errors.append(_error(f"entries.{i}.on_date", BEFORE_ENROLMENT_CODE))
        if entry.on_date in closed:
            errors.append(_error(f"entries.{i}.on_date", HOLIDAY_CODE))
        key = (entry.student_id, entry.on_date)
        if key in seen:
            errors.append(_error(f"entries.{i}", "duplicate_entry"))
        seen.add(key)
    _refuse(errors)
    if not ctx.scope_for(ATTENDANCE_RECORD).school_wide:
        dates = [e.on_date for e in data.entries]
        elsewhere = repo.marks_in_other_sections(
            session, sec.section.id, roster, min(dates), max(dates)
        )
        for i, entry in enumerate(data.entries):
            if (entry.student_id, entry.on_date) in elsewhere:
                errors.append(_error(f"entries.{i}.on_date", OTHER_SECTION_CODE))
        _refuse(errors)
    _check_expected_versions(session, data)
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


def _check_expected_versions(session: Session, data: AttendanceWrite) -> None:
    """Optimistic concurrency per mark (XA-06): with the school's attendance of those dates
    locked, every entry that carries ``expected_version`` must still find that version (0: not
    marked). Otherwise 412 ``attendance_changed`` and nothing is saved, so two people marking
    the same section and day cannot silently overwrite each other's change."""
    repo.lock_attendance_days(session, {e.on_date for e in data.entries})
    checked = [e for e in data.entries if e.expected_version is not None]
    if not checked:
        return
    stored = repo.attendance_versions(
        session, {e.student_id for e in checked}, {e.on_date for e in checked}
    )
    stale = sum(
        1 for e in checked if stored.get((e.student_id, e.on_date), 0) != e.expected_version
    )
    if stale:
        raise PreconditionFailed(
            f"Someone else changed the attendance of {stale} student(s) since you opened it. "
            "Reload to see their changes, then save again.",
            code="attendance_changed",
        )


# --- sheets ---------------------------------------------------------------------------------------


def _consume_sheet(
    ctx: UserContext, document_id: uuid.UUID, action: str, section_id: uuid.UUID
) -> tuple[bytes, FileKind]:
    """Read an ``import_file`` the caller uploaded and can see, then delete it, committed in its
    own transaction (whatever the parse finds, the file does not linger). 404 when not visible
    or uploaded by someone else (a preview never reads or deletes another person's upload, audit
    DL-04); 409 ``document_not_ready`` while the virus check runs; 415/413 for other types or
    sizes."""
    limits = load_config().sheets
    with tenant_session(ctx.tenant_id, ctx.user_id) as session:
        if not documents.is_own_upload(session, ctx, document_id):
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
        members = _roster(session, ctx, sec.section, ATTENDANCE_RECORD)
        roster = sheets.Roster.build((s.student_id, s.admission_no, s.roll_no) for s in members)
        starts = _explicit_starts(session, [s.student_id for s in members], sec.section.id)
        cal = cal_service.year_calendar(session, sec.year)
        closed = (
            {
                d.on_date
                for d in cal.days(sec.year.starts_on, sec.year.ends_on, sec.section.class_id)
                if not d.working
            }
            if cal is not None
            else set()
        )
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
            starts=starts,
            closed=closed,
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
        locked=exam.locked_at is not None,
        locked_at=exam.locked_at,
        unlocked_at=exam.unlocked_at,
        unlock_reason=exam.unlock_reason,
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
    if exam is None:  # R-13: a concurrent request created the same exam first
        raise ValidationFailed(
            [_error("name", "exam_name_taken")],
            detail="Some entries cannot be saved. Fix them and try again.",
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


def _ensure_unlocked(exam: Exam) -> None:
    """409 ``exam_locked`` for a locked exam (FR-MRK-009)."""
    if exam.locked_at is not None:
        raise Conflict(
            "This exam is locked, so its marks cannot change. Ask the principal to unlock it.",
            code=EXAM_LOCKED_CODE,
        )


def _exam_locked_for_change(session: Session, exam_id: uuid.UUID) -> Exam:
    """The exam with its marks writes serialised (the advisory lock marks writes take) and its
    row locked and re-read, so a lock, an unlock and a marks write never interleave."""
    repo.lock_exam_marks(session, exam_id)
    exam = repo.exam_for_update(session, exam_id)
    if exam is None:
        raise NotFound("Exam not found")
    return exam


def lock_exam(session: Session, ctx: UserContext, exam_id: uuid.UUID) -> ExamOut:
    """Lock an exam's marks (``exam.manage``; FR-MRK-008): marks writes and sheet previews then
    answer 409 ``exam_locked``. 409 ``exam_already_locked`` when it is locked. Audit
    ``exam.locked`` in the same transaction."""
    exam = _exam_locked_for_change(session, exam_id)
    if exam.locked_at is not None:
        raise Conflict("This exam is already locked.", code="exam_already_locked")
    updated = repo.update_exam_lock(
        session, exam.id, {"locked_at": dt.datetime.now(dt.UTC), "locked_by": ctx.user_id}
    )
    _audit(
        session,
        "exam.locked",
        "exam",
        exam.id,
        {"academic_year_id": exam.academic_year_id, "relock": exam.unlocked_at is not None},
    )
    log.info("academics.exam_locked", resource_id=exam.id)
    return _exam_out(updated)


def unlock_exam(
    session: Session, ctx: UserContext, exam_id: uuid.UUID, data: ExamUnlockIn
) -> ExamOut:
    """Unlock a locked exam so its marks can be corrected (``exam.unlock``: the principal only,
    step-up MFA; FR-MRK-010) with a mandatory reason (no Aadhaar numbers, 422). Corrections made
    while it stays unlocked need no second person; each is kept in the marks history with this
    reason unless the write gives its own (FR-MRK-012). 409 ``exam_not_locked``. Audit
    ``exam.unlocked`` in the same transaction (the reason stays on the exam, not in the audit
    summary)."""
    if contains_full_aadhaar(data.reason):
        _refuse([_error("reason", AADHAAR_CODE)])
    exam = _exam_locked_for_change(session, exam_id)
    if exam.locked_at is None:
        raise Conflict("This exam is not locked.", code="exam_not_locked")
    locked_at = exam.locked_at
    updated = repo.update_exam_lock(
        session,
        exam.id,
        {
            "locked_at": None,
            "locked_by": None,
            "unlocked_at": dt.datetime.now(dt.UTC),
            "unlocked_by": ctx.user_id,
            "unlock_reason": data.reason,
        },
    )
    _audit(
        session,
        "exam.unlocked",
        "exam",
        exam.id,
        {
            "academic_year_id": exam.academic_year_id,
            "locked_at": locked_at.isoformat(),
            "reason_chars": len(data.reason),
        },
    )
    log.info("academics.exam_unlocked", resource_id=exam.id)
    return _exam_out(updated)


# --- marks ----------------------------------------------------------------------------------------


def percent_of(rows: Iterable[tuple[Decimal | None, Decimal, bool]]) -> float | None:
    """FR-MRK-005: sum of marks / sum of maximum marks over the papers with marks (absent papers
    left out), as a percentage with one decimal rounded half up; None when no paper has marks."""
    got = total = Decimal(0)
    for marks, max_marks, absent in rows:
        if absent or marks is None:
            continue
        got += marks
        total += max_marks
    if total <= 0:
        return None
    return float((got * 100 / total).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP))


def section_marks(
    session: Session, ctx: UserContext, section_id: uuid.UUID, exam_id: uuid.UUID
) -> MarksGridOut:
    """A section's marks for one exam (``marks.read``; FR-MRK-003)."""
    sec = _section(session, ctx, section_id, MARKS_READ)
    exam = _exam(session, exam_id)
    roster = _roster(session, ctx, sec.section, MARKS_READ)
    rows = repo.exam_marks(session, exam.id, [s.student_id for s in roster])
    after_lock = repo.marks_changed_after_lock(session, exam.id, [s.student_id for s in roster])
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
                        subject=m.subject,
                        max_marks=m.max_marks,
                        marks=m.marks,
                        absent=m.absent,
                        version=m.version,
                        changed_after_lock=m.id in after_lock,
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


def marks_history(
    session: Session, ctx: UserContext, section_id: uuid.UUID, exam_id: uuid.UUID
) -> MarksHistoryOut:
    """Every change of the section's marks for one exam, newest first, at most
    :data:`HISTORY_LIMIT` (``marks.read``; FR-MRK-013): previous and new value, who, when, the
    reason and whether it was after the exam was first locked. Students outside the caller's
    roster are left out."""
    sec = _section(session, ctx, section_id, MARKS_READ)
    exam = _exam(session, exam_id)
    roster = {s.student_id: s for s in _roster(session, ctx, sec.section, MARKS_READ)}
    rows = repo.mark_history(session, exam.id, list(roster), limit=HISTORY_LIMIT)
    names = identity.members_for_users(session, {r.changed_by for r in rows})
    return MarksHistoryOut(
        section=sec.out,
        exam=_exam_out(exam),
        changes=[_change_out(r, roster[r.student_id], names) for r in rows],
    )


def _change_out(
    row: ExamMarkHistory,
    student: RosterStudentOut,
    names: Mapping[uuid.UUID, tuple[uuid.UUID, str]],
) -> MarkChangeOut:
    who = names.get(row.changed_by)
    return MarkChangeOut(
        student=student,
        subject=row.subject,
        previous_max_marks=row.previous_max_marks,
        previous_marks=row.previous_marks,
        previous_absent=row.previous_absent,
        new_max_marks=row.new_max_marks,
        new_marks=row.new_marks,
        new_absent=row.new_absent,
        source="import" if row.source == "import" else "mark",
        after_lock=row.after_lock,
        reason=row.reason,
        changed_by_name=who[1] if who else None,
        changed_at=row.changed_at,
    )


def _history_rows(
    exam: Exam,
    section_id: uuid.UUID,
    existing: Iterable[ExamMark],
    data: MarksWrite,
    ctx: UserContext,
) -> list[dict[str, Any]]:
    """FR-MRK-012: one history row per stored mark this write changes (new marks have none)."""
    by_key = {(m.student_id, m.subject.casefold()): m for m in existing}
    after_lock = exam.unlocked_at is not None
    reason = data.reason or (exam.unlock_reason if after_lock else None)
    rows: list[dict[str, Any]] = []
    for e in data.entries:
        old = by_key.get((e.student_id, e.subject.casefold()))
        if old is None or (
            old.max_marks == e.max_marks
            and old.marks == e.marks
            and old.absent == e.absent
            and old.section_id == section_id
        ):
            continue
        rows.append(
            {
                "id": new_id(),
                "exam_id": exam.id,
                "mark_id": old.id,
                "student_id": e.student_id,
                "section_id": section_id,
                "subject": old.subject,
                "previous_max_marks": old.max_marks,
                "previous_marks": old.marks,
                "previous_absent": old.absent,
                "new_max_marks": e.max_marks,
                "new_marks": e.marks,
                "new_absent": e.absent,
                "source": data.source,
                "after_lock": after_lock,
                "reason": reason,
                "changed_by": ctx.user_id,
            }
        )
    return rows


def _marks_in_other_sections(
    ctx: UserContext, section_id: uuid.UUID, existing: Iterable[Any], entries: Sequence[MarkIn]
) -> list[dict[str, str]]:
    """Errors for papers a scoped recorder finds already recorded under another section
    (:data:`OTHER_SECTION_CODE`); none for a school-wide recorder."""
    if ctx.scope_for(MARKS_RECORD).school_wide:
        return []
    elsewhere = {
        (m.student_id, m.subject.casefold()) for m in existing if m.section_id != section_id
    }
    return [
        _error(f"entries.{i}.subject", OTHER_SECTION_CODE)
        for i, entry in enumerate(entries)
        if (entry.student_id, entry.subject.casefold()) in elsewhere
    ]


def _check_expected_mark_versions(existing: Iterable[ExamMark], entries: Sequence[MarkIn]) -> None:
    """Optimistic concurrency per mark (YA-05): with the exam's marks locked, every entry that
    carries ``expected_version`` must still find that version (0: no mark). Otherwise 412
    ``marks_changed`` and nothing is saved."""
    checked = [e for e in entries if e.expected_version is not None]
    if not checked:
        return
    versions = {(m.student_id, m.subject.casefold()): m.version for m in existing}
    stale = sum(
        1
        for e in checked
        if versions.get((e.student_id, e.subject.casefold()), 0) != e.expected_version
    )
    if stale:
        raise PreconditionFailed(
            f"Someone else changed {stale} mark(s) since you opened them. "
            "Reload to see their changes, then save again.",
            code="marks_changed",
        )


def _check_mark_entries(
    errors: list[dict[str, str]], entries: Sequence[MarkIn], roster: Collection[uuid.UUID]
) -> set[str]:
    """Per-entry checks of a marks write (FR-MRK-002) into ``errors``; the subjects written
    (case-folded)."""
    cfg = load_config().marks
    seen: set[tuple[uuid.UUID, str]] = set()
    subjects: set[str] = set()
    for i, entry in enumerate(entries):
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
    return subjects


def record_marks(
    session: Session,
    ctx: UserContext,
    section_id: uuid.UUID,
    exam_id: uuid.UUID,
    data: MarksWrite,
) -> WriteResultOut:
    """Store a section's marks for one exam, all or nothing (``marks.record``; FR-MRK-002). 422
    when a student is not in the roster, marks exceed the maximum, absent papers carry marks,
    a subject is too long or holds an Aadhaar-like number, or an entry repeats; and, for a scoped
    recorder, when the paper is already recorded under another section
    (``recorded_in_another_section``); 412 ``marks_changed`` when an entry's
    ``expected_version`` is stale (YA-05); 409 ``exam_locked`` when the exam is locked
    (FR-MRK-009). Subjects match whatever their case: a paper already stored corrects in place
    under its first spelling. Every change of a stored mark is appended to the marks history in
    the same transaction (FR-MRK-012). Audit ``marks.recorded``."""
    cfg = load_config().marks
    sec = _section(session, ctx, section_id, MARKS_RECORD)
    exam = _exam(session, exam_id)
    _ensure_unlocked(exam)  # fail fast; checked again under the exam's lock below
    errors: list[dict[str, str]] = []
    if data.reason is not None and contains_full_aadhaar(data.reason):
        errors.append(_error("reason", AADHAAR_CODE))
    if exam.academic_year_id != sec.section.academic_year_id:
        errors.append(_error("exam_id", "exam_other_year"))
    if len(data.entries) > cfg.max_entries:
        errors.append(_error("entries", "too_many_entries"))
    _refuse(errors)
    roster = {s.student_id for s in _roster(session, ctx, sec.section, MARKS_RECORD)}
    subjects = _check_mark_entries(errors, data.entries, roster)
    _refuse(errors)
    # A paper is one per exam, student and subject whatever its case ("MATHS" from a sheet
    # corrects "Maths" typed earlier): keep the spelling already stored for the student, else
    # the exam's first spelling (one column per subject in the grid).
    spelling: dict[str, str] = {}
    for subject in repo.exam_subjects(session, exam.id):
        spelling.setdefault(subject.casefold(), subject)
    exam = _exam_locked_for_change(session, exam.id)
    _ensure_unlocked(exam)
    existing = repo.exam_marks(session, exam.id, {e.student_id for e in data.entries})
    stored = {(m.student_id, m.subject.casefold()): m.subject for m in existing}
    _refuse(_marks_in_other_sections(ctx, sec.section.id, existing, data.entries))
    _check_expected_mark_versions(existing, data.entries)

    def _subject(entry: MarkIn) -> str:
        folded = entry.subject.casefold()
        return stored.get((entry.student_id, folded)) or spelling.get(folded, entry.subject)

    inserted, updated = repo.upsert_marks(
        session,
        [
            {
                "id": new_id(),
                "exam_id": exam.id,
                "student_id": e.student_id,
                "section_id": sec.section.id,
                "subject": _subject(e),
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
    history = _history_rows(exam, sec.section.id, existing, data, ctx)
    repo.insert_mark_history(session, history)
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
            "history": len(history),
            "after_lock": exam.unlocked_at is not None,
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
    problems, nothing stored; the file is deleted once read; 409 ``exam_locked`` for a locked
    exam, before the file is read (FR-MRK-009). Audit ``marks.sheet_read``."""
    with tenant_session(ctx.tenant_id, ctx.user_id) as session:
        sec = _section(session, ctx, section_id, MARKS_RECORD)
        _ensure_unlocked(_exam(session, exam_id))
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


def absent_students(session: Session, on_date: dt.date) -> list[uuid.UUID]:
    """Students marked ``absent`` on ``on_date`` (not ``late`` or ``leave``), as marked now.
    System read for the WhatsApp absence alerts (R2-3G, FR-MSG-013; ADR-0043 decision 11): IDs
    only; the caller decides who is told."""
    return repo.students_with_status(session, on_date, "absent")


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


# --- "Ask the school" attendance summary (FA-04; FR-KB-070..073) ----------------------------------


@dataclass(frozen=True, slots=True)
class SectionAttendance:
    """Counts of one section's register over a date range (no student ids, no names)."""

    section_id: uuid.UUID
    class_id: uuid.UUID
    label: str
    """``"<class display name> <section name>"``."""
    students: int
    """Students on the roll the caller reaches."""
    days_marked: int
    present: int
    late: int
    absent: int
    leave: int

    @property
    def marks(self) -> int:
        return self.present + self.late + self.absent + self.leave

    @property
    def rate(self) -> float | None:
        """Present or late marks / all marks, in percent; ``None`` when nothing is marked."""
        if not self.marks:
            return None
        return 100.0 * (self.present + self.late) / self.marks


@dataclass(frozen=True, slots=True)
class AttendanceRates:
    """``attendance_rates_for_ask``: the sections of the current academic year the caller
    reaches with ``attendance.read``, in class order (FA-04)."""

    year_label: str | None
    start: dt.date
    end: dt.date
    sections: tuple[SectionAttendance, ...]


def attendance_rates_for_ask(
    session: Session, ctx: UserContext, *, start: dt.date, end: dt.date
) -> AttendanceRates:
    """Per section counts of the register from ``start`` to ``end`` (inclusive) for "Ask the
    school" (``attendance.read``; class teachers: their sections). The roll is the caller's
    reach through ``student.read_basic`` AND ``attendance.read`` (as on the register screen);
    only those students' marks are counted. Counts only: never a student id or name. Sections
    the caller does not reach are left out, as are sections with nobody on the roll."""
    if not ctx.has(ATTENDANCE_READ):
        return AttendanceRates(year_label=None, start=start, end=end, sections=())
    year = tenancy.get_current_academic_year(session)
    if year is None:
        return AttendanceRates(year_label=None, start=start, end=end, sections=())
    classes = {c.id: c for c in tenancy.list_classes(session)}
    sections = sorted(
        tenancy.list_sections(session, academic_year_id=year.id, include_archived=False),
        key=lambda s: (classes[s.class_id].sort_order if s.class_id in classes else 0, s.name),
    )
    out: list[SectionAttendance] = []
    for section in sections:
        try:
            ensure_section_visible(ctx, ATTENDANCE_READ, section)
        except NotFound:
            continue
        roll = set(
            students.list_students_in_scope(
                session, ctx, section_ids=[section.id], permissions=(ATTENDANCE_READ,)
            )
        )
        if not roll:
            continue
        rows = [
            r for r in repo.section_marks(session, section.id, start, end) if r.student_id in roll
        ]
        counts = _counts(r.status for r in rows)
        klass = classes.get(section.class_id)
        out.append(
            SectionAttendance(
                section_id=section.id,
                class_id=section.class_id,
                label=f"{klass.display_en if klass else 'Class'} {section.name}",
                students=len(roll),
                days_marked=len({r.on_date for r in rows}),
                present=counts["present"],
                late=counts["late"],
                absent=counts["absent"],
                leave=counts["leave"],
            )
        )
    log.info("academics.attendance.ask_summary", count=len(out))
    return AttendanceRates(year_label=year.label, start=start, end=end, sections=tuple(out))


# --- full export and offboarding ------------------------------------------------------------------


def export_records(session: Session) -> list[RecordTable]:
    """Worker only: attendance, exams and marks of the current school for its full data export
    (``app.admin``; the caller checked ``tenant.export_all`` and audits the export)."""
    return repo.export_tables(session)


_PURGE = purging.PurgeTables(
    deleted=(
        "sis.year_results",
        "sis.exam_mark_history",
        "sis.exam_marks",
        "sis.exams",
        "sis.attendance_marks",
        "sis.calendar_entries",
        "sis.school_calendars",
    ),
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
    "BEFORE_ENROLMENT_CODE",
    "CALENDAR_MANAGE",
    "CALENDAR_READ",
    "COMPLETENESS_READ",
    "COMPLETENESS_REMIND",
    "EXAM_LOCKED_CODE",
    "EXAM_MANAGE",
    "EXAM_UNLOCK",
    "HOLIDAY_CODE",
    "MARKS_READ",
    "MARKS_RECORD",
    "RECORDS_CHANGED_EVENT",
    "YEAR_RESULT_CONFIRM",
    "YEAR_RESULT_READ",
    "AttendanceRates",
    "AttendanceSummary",
    "DayMark",
    "ExamResult",
    "PeriodAttendance",
    "SectionAttendance",
    "SectionPeriodAttendance",
    "absent_students",
    "add_entry",
    "attendance_completeness",
    "attendance_day",
    "attendance_history",
    "attendance_month",
    "attendance_period",
    "attendance_rates_for_ask",
    "attendance_summary",
    "calendar_month",
    "confirm_results",
    "create_exam",
    "exam_results",
    "export_records",
    "get_calendar",
    "held_back",
    "list_exams",
    "lock_exam",
    "marks_completeness",
    "marks_history",
    "percent_of",
    "preview_attendance_sheet",
    "preview_marks_sheet",
    "progression",
    "progression_csv",
    "purge_tenant_data",
    "record_attendance",
    "record_marks",
    "remove_entry",
    "section_marks",
    "send_completeness_reminder",
    "set_pattern",
    "subject_report",
    "suggestions",
    "tc_suggestions",
    "tenant_data_counts",
    "today_ist",
    "unlock_exam",
    "year_results",
]
