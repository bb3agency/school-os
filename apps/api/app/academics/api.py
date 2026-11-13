"""Attendance, exams, marks and school calendar routes (docs/09 Attendance and marks; M5;
US-1701..US-1703; FR-ATT-*, FR-MRK-*, FR-CAL-*).

Every route declares its guard; class teachers hold the permissions for their sections only
(07 §6.2 S) and the service answers 404 for any other section. Sheet previews store nothing:
the entries go back to the person, who sends them to the write route (``source=import``).
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Response

from app.academics import service, year_end
from app.academics.completeness_schemas import (
    AttendanceCompletenessOut,
    CompletenessReminderIn,
    CompletenessReminderOut,
    MarksCompletenessOut,
)
from app.academics.schemas import (
    AttendanceDayOut,
    AttendanceMonthOut,
    AttendanceSheetOut,
    AttendanceWrite,
    CalendarEntryAddedOut,
    CalendarEntryIn,
    CalendarMonthOut,
    CalendarPatternIn,
    CalendarSuggestionsOut,
    ExamCreate,
    ExamOut,
    ExamUnlockIn,
    MarksGridOut,
    MarksHistoryOut,
    MarksSheetOut,
    MarksWrite,
    Month,
    SchoolCalendarOut,
    SheetIn,
    WriteResultOut,
)
from app.academics.year_end_schemas import (
    HeldBackOut,
    ProgressionOut,
    YearResultsOut,
    YearResultsWrite,
    YearResultsWriteOut,
)
from app.authz.context import UserContext
from app.authz.dependencies import TenantDB, require, require_any
from app.authz.http import IdempotencyDep, OptionalIfMatch, etag

router = APIRouter(prefix="/api/v1", tags=["attendance and marks"])

AttendanceReader = Annotated[UserContext, Depends(require(service.ATTENDANCE_READ))]
AttendanceRecorder = Annotated[UserContext, Depends(require(service.ATTENDANCE_RECORD))]
ExamReader = Annotated[
    UserContext,
    Depends(require_any(service.MARKS_READ, service.MARKS_RECORD, service.EXAM_MANAGE)),
]
ExamManager = Annotated[UserContext, Depends(require(service.EXAM_MANAGE, scope="school"))]
ExamUnlocker = Annotated[
    UserContext, Depends(require(service.EXAM_UNLOCK, scope="school", step_up=True))
]
MarksReader = Annotated[UserContext, Depends(require(service.MARKS_READ))]
CalendarReader = Annotated[UserContext, Depends(require(service.CALENDAR_READ))]
CalendarManager = Annotated[UserContext, Depends(require(service.CALENDAR_MANAGE, scope="school"))]
MarksRecorder = Annotated[UserContext, Depends(require(service.MARKS_RECORD))]
CompletenessReader = Annotated[
    UserContext, Depends(require(service.COMPLETENESS_READ, scope="school"))
]
CompletenessReminder = Annotated[
    UserContext, Depends(require(service.COMPLETENESS_REMIND, scope="school"))
]


# --- completeness (FT-14) -------------------------------------------------------------------------


@router.get("/completeness/attendance", response_model=AttendanceCompletenessOut)
def attendance_completeness(
    ctx: CompletenessReader,
    db: TenantDB,
    date: Annotated[dt.date | None, Query(description="IST date; default today")] = None,
) -> AttendanceCompletenessOut:
    """How complete attendance is for a date in every section of the current year: students,
    students with a status, the day's calendar kind and the class teacher it waits on
    (``completeness.read``; counts only, no student is named). 409 ``no_current_year``; 422
    ``future_date``, ``date_out_of_range``."""
    return service.attendance_completeness(db, ctx, date)


@router.get("/completeness/exams/{exam_id}", response_model=MarksCompletenessOut)
def marks_completeness(
    ctx: CompletenessReader,
    db: TenantDB,
    exam_id: uuid.UUID,
    class_id: Annotated[uuid.UUID | None, Query(description="Only this class")] = None,
) -> MarksCompletenessOut:
    """How complete marks entry is for an exam: per section and subject entered for the exam,
    students and students with a mark or absent, and the class teacher it waits on
    (``completeness.read``; counts only). 404 for an exam of another school; 422
    ``unknown_class``."""
    return service.marks_completeness(db, ctx, exam_id, class_id)


@router.post("/completeness/reminders", response_model=CompletenessReminderOut)
def send_completeness_reminder(
    ctx: CompletenessReminder, db: TenantDB, body: CompletenessReminderIn
) -> CompletenessReminderOut:
    """Remind the section's class teacher in the app to finish attendance (today) or an exam's
    marks (``completeness.remind``). Once per section and day or exam (``sent`` 0 for a
    repeat). 409 ``reminders_off`` (the default), ``no_class_teacher``, ``already_complete``;
    422 ``exam_required``; 404 for another school's section or exam."""
    return service.send_completeness_reminder(db, ctx, body)


# --- attendance -----------------------------------------------------------------------------------


@router.get("/sections/{section_id}/attendance", response_model=AttendanceDayOut)
def attendance_day(
    ctx: AttendanceReader,
    db: TenantDB,
    section_id: uuid.UUID,
    date: Annotated[dt.date | None, Query(description="IST date; default today")] = None,
) -> AttendanceDayOut:
    """One day of the section's register: every student of the section (roll-number order)
    with their status, or none when not marked (``attendance.read``; class teachers: their
    sections, 404 for others)."""
    return service.attendance_day(db, ctx, section_id, date or service.today_ist())


@router.get("/sections/{section_id}/attendance/month", response_model=AttendanceMonthOut)
def attendance_month(
    ctx: AttendanceReader,
    db: TenantDB,
    section_id: uuid.UUID,
    month: Annotated[Month, Query(description="YYYY-MM")],
) -> AttendanceMonthOut:
    """The month register (school days x students) for the A4 print view
    (``attendance.read``)."""
    return service.attendance_month(db, ctx, section_id, month)


@router.post("/sections/{section_id}/attendance", response_model=WriteResultOut)
def record_attendance(
    ctx: AttendanceRecorder, db: TenantDB, section_id: uuid.UUID, body: AttendanceWrite
) -> WriteResultOut:
    """Save statuses for one or more dates, all or nothing (``attendance.record``). Saving a
    day again corrects it. 422 ``not_in_section``, ``future_date``, ``date_out_of_range``,
    ``before_enrolment`` (before the student's explicit enrolment start) or ``duplicate_entry``
    per entry. An entry may carry ``expected_version`` (the ``version`` read
    from the day): 412 ``attendance_changed`` when someone else changed that mark since, and
    nothing is saved. The early-warning rules look at these students again."""
    return service.record_attendance(db, ctx, section_id, body)


@router.post("/sections/{section_id}/attendance/sheet", response_model=AttendanceSheetOut)
def preview_attendance_sheet(
    ctx: AttendanceRecorder, section_id: uuid.UUID, body: SheetIn
) -> AttendanceSheetOut:
    """Read an uploaded attendance sheet (``attendance.record``): first column admission or
    roll number, then one date per column, cells P/A/L/LV. Returns the entries and every
    problem (``before_enrolment`` for a date before the student's explicit start); stores no
    attendance. The uploaded file is deleted once read. 422
    ``aadhaar_full_number_rejected``, ``student_column_missing``, ``too_many_dates``...; 409
    ``document_not_ready`` while the virus check runs."""
    return service.preview_attendance_sheet(ctx, section_id, body)


# --- exams ----------------------------------------------------------------------------------------


@router.get("/exams", response_model=list[ExamOut])
def list_exams(
    ctx: ExamReader,
    db: TenantDB,
    academic_year_id: Annotated[
        uuid.UUID | None, Query(description="Default: the current academic year")
    ] = None,
) -> list[ExamOut]:
    """Exams of an academic year by date (``marks.read``, ``marks.record`` or
    ``exam.manage``)."""
    return service.list_exams(db, ctx, academic_year_id)


@router.post("/exams", response_model=ExamOut, status_code=201)
def create_exam(ctx: ExamManager, db: TenantDB, body: ExamCreate, idem: IdempotencyDep) -> Response:
    """Add an exam to the current academic year (``exam.manage``). 422 ``exam_name_taken`` or
    ``date_out_of_range``. Accepts ``Idempotency-Key``."""
    return idem.run(
        db,
        body,
        lambda: service.create_exam(db, ctx, body),
        headers=lambda out: {"Location": f"/api/v1/exams/{out.id}", "ETag": etag(out.version)},
    )


@router.post("/exams/{exam_id}/lock", response_model=ExamOut)
def lock_exam(ctx: ExamManager, db: TenantDB, exam_id: uuid.UUID) -> ExamOut:
    """Lock an exam's marks (``exam.manage``; FR-MRK-008): marks writes and marks-sheet
    previews then answer 409 ``exam_locked``. 409 ``exam_already_locked``; 404 for an exam of
    another school. Audited ``exam.locked``."""
    return service.lock_exam(db, ctx, exam_id)


@router.post("/exams/{exam_id}/unlock", response_model=ExamOut)
def unlock_exam(ctx: ExamUnlocker, db: TenantDB, exam_id: uuid.UUID, body: ExamUnlockIn) -> ExamOut:
    """Unlock a locked exam so its marks can be corrected (``exam.unlock``: the principal only,
    step-up MFA, 428 without it; FR-MRK-010) with a mandatory reason. 409 ``exam_not_locked``.
    Audited ``exam.unlocked``; every later change is kept in the marks history."""
    return service.unlock_exam(db, ctx, exam_id, body)


# --- marks ----------------------------------------------------------------------------------------


@router.get("/sections/{section_id}/exams/{exam_id}/marks", response_model=MarksGridOut)
def section_marks(
    ctx: MarksReader, db: TenantDB, section_id: uuid.UUID, exam_id: uuid.UUID
) -> MarksGridOut:
    """The section's marks for one exam with each student's overall percentage
    (``marks.read``)."""
    return service.section_marks(db, ctx, section_id, exam_id)


@router.get("/sections/{section_id}/exams/{exam_id}/marks/history", response_model=MarksHistoryOut)
def marks_history(
    ctx: MarksReader, db: TenantDB, section_id: uuid.UUID, exam_id: uuid.UUID
) -> MarksHistoryOut:
    """Every change of the section's marks for one exam, newest first (``marks.read``;
    FR-MRK-013): previous and new value, who, when, the reason and whether it was after the
    exam was first locked."""
    return service.marks_history(db, ctx, section_id, exam_id)


@router.post("/sections/{section_id}/exams/{exam_id}/marks", response_model=WriteResultOut)
def record_marks(
    ctx: MarksRecorder,
    db: TenantDB,
    section_id: uuid.UUID,
    exam_id: uuid.UUID,
    body: MarksWrite,
) -> WriteResultOut:
    """Save marks per student and subject with the maximum marks, or absent, all or nothing
    (``marks.record``). 422 ``not_in_section``, ``marks_over_max``, ``absent_has_no_marks``,
    ``exam_other_year``...; an entry may carry ``expected_version`` (the mark's ``version``):
    412 ``marks_changed`` when someone else changed that mark since, and nothing is saved. 409
    ``exam_locked`` when the exam is locked (FR-MRK-009). Changes of stored marks are kept in
    the marks history with the optional ``reason`` (FR-MRK-012)."""
    return service.record_marks(db, ctx, section_id, exam_id, body)


@router.post("/sections/{section_id}/exams/{exam_id}/marks/sheet", response_model=MarksSheetOut)
def preview_marks_sheet(
    ctx: MarksRecorder, section_id: uuid.UUID, exam_id: uuid.UUID, body: SheetIn
) -> MarksSheetOut:
    """Read an uploaded marks sheet (``marks.record``): row 1 admission or roll number and
    subjects, row 2 maximum marks, then one row per student (``AB`` = absent). Returns entries
    and problems; stores no marks; the file is deleted once read. 409 ``exam_locked`` for a
    locked exam."""
    return service.preview_marks_sheet(ctx, section_id, exam_id, body)


# --- school calendar (FT-06; FR-CAL-*) ---------------------------------------------------------


@router.get("/academic-years/{year_id}/calendar", response_model=SchoolCalendarOut)
def get_calendar(
    ctx: CalendarReader, db: TenantDB, year_id: uuid.UUID, response: Response
) -> SchoolCalendarOut:
    """The year's school calendar: weekly pattern, holidays, vacations and extra working days
    (``student.read_basic``; FR-CAL-001). Before it is set up: the default pattern with
    ``set_up`` false. ``ETag`` carries the version (0 before set-up)."""
    out = service.get_calendar(db, ctx, year_id)
    response.headers["ETag"] = etag(out.version)
    return out


@router.put("/academic-years/{year_id}/calendar", response_model=SchoolCalendarOut)
def set_calendar_pattern(
    *,
    ctx: CalendarManager,
    db: TenantDB,
    year_id: uuid.UUID,
    body: CalendarPatternIn,
    version: OptionalIfMatch,
    response: Response,
) -> SchoolCalendarOut:
    """Save the weekly pattern (``tenant.structure.manage``; FR-CAL-001). The first save sets
    up the calendar: from then on attendance on a day off is refused (422 ``holiday``).
    ``If-Match`` (``W/"0"`` before set-up) is honoured when sent: 412 ``calendar_changed``.
    Audited ``calendar.set_up`` / ``calendar.pattern_changed``."""
    out = service.set_pattern(db, ctx, year_id, body, expected_version=version)
    response.headers["ETag"] = etag(out.version)
    return out


@router.get("/academic-years/{year_id}/calendar/month", response_model=CalendarMonthOut)
def calendar_month(
    ctx: CalendarReader,
    db: TenantDB,
    year_id: uuid.UUID,
    month: Annotated[Month, Query(description="YYYY-MM")],
    class_id: Annotated[
        uuid.UUID | None, Query(description="A class: its exceptions apply; default whole school")
    ] = None,
) -> CalendarMonthOut:
    """Every date of a month with its kind (working, weekly off, holiday...) and the month's
    working days (``student.read_basic``; FR-CAL-009)."""
    return service.calendar_month(db, ctx, year_id, month, class_id)


@router.get("/academic-years/{year_id}/calendar/suggestions", response_model=CalendarSuggestionsOut)
def calendar_suggestions(
    ctx: CalendarManager, db: TenantDB, year_id: uuid.UUID
) -> CalendarSuggestionsOut:
    """Unverified national and Andhra Pradesh holiday suggestions inside the year that are not
    in the calendar yet (``tenant.structure.manage``; FR-CAL-013). The school checks each date
    and adds the ones it keeps."""
    return service.suggestions(db, ctx, year_id)


@router.post(
    "/academic-years/{year_id}/calendar/entries",
    response_model=CalendarEntryAddedOut,
    status_code=201,
)
def add_calendar_entry(
    ctx: CalendarManager,
    db: TenantDB,
    year_id: uuid.UUID,
    body: CalendarEntryIn,
    idem: IdempotencyDep,
) -> Response:
    """Add a holiday, vacation or extra working day, for the whole school or one class
    (``tenant.structure.manage``; FR-CAL-002, FR-CAL-003). 409 ``calendar_not_set_up``,
    ``calendar_overlap``, ``suggestion_already_added``; 422 ``date_out_of_range``,
    ``dates_reversed``, ``entry_too_long``, ``unknown_class``, ``unknown_suggestion``. The answer
    says how many attendance marks already fall inside it. Accepts ``Idempotency-Key``. Audited
    ``calendar.entry_added``."""
    return idem.run(db, body, lambda: service.add_entry(db, ctx, year_id, body), status_code=201)


@router.delete("/academic-years/{year_id}/calendar/entries/{entry_id}", status_code=204)
def remove_calendar_entry(
    ctx: CalendarManager, db: TenantDB, year_id: uuid.UUID, entry_id: uuid.UUID
) -> Response:
    """Remove a holiday, vacation or extra working day (``tenant.structure.manage``;
    FR-CAL-005). Audited ``calendar.entry_removed``."""
    service.remove_entry(db, ctx, year_id, entry_id)
    return Response(status_code=204)


# --- year-end progression pack (FT-08; D85; FR-YRE-*) ---------------------------------------------

YearResultReader = Annotated[
    UserContext, Depends(require(service.YEAR_RESULT_READ, scope="school"))
]
YearResultConfirmer = Annotated[
    UserContext, Depends(require(service.YEAR_RESULT_CONFIRM, scope="school"))
]
PortalExporter = Annotated[
    UserContext, Depends(require(year_end.EXPORT_PORTAL, scope="school", step_up=True))
]


@router.get("/sections/{section_id}/year-results", response_model=YearResultsOut)
def year_results(ctx: YearResultReader, db: TenantDB, section_id: uuid.UUID) -> YearResultsOut:
    """Every student finishing the year in the section with SchoolOS's proposed annual result
    (from the year's marks; days attended from the register) and the confirmed result, who
    confirmed it and when (``year_result.read``, school-wide; FR-YRE-002). Any year's section,
    also after the promotion. 404 for another school's section."""
    return service.year_results(db, ctx, section_id)


@router.post("/sections/{section_id}/year-results", response_model=YearResultsWriteOut)
def confirm_year_results(
    ctx: YearResultConfirmer, db: TenantDB, section_id: uuid.UUID, body: YearResultsWrite
) -> YearResultsWriteOut:
    """Confirm or change annual results, all or nothing (``year_result.confirm``: the
    principal; FR-YRE-004). ``detained`` needs a reason (422 ``reason_required``); 422
    ``not_in_section``, ``duplicate``, ``aadhaar_full_number_rejected``; 412
    ``year_result_changed`` for a stale ``expected_version``. In a no-detention class a
    ``detained`` result is saved with the warning ``no_detention_class`` (never refused).
    Audited ``year_result.confirmed``. Nothing changes a student's class: the promotion does."""
    return service.confirm_results(db, ctx, section_id, body)


@router.get("/academic-years/{year_id}/year-results/held-back", response_model=HeldBackOut)
def year_held_back(ctx: YearResultReader, db: TenantDB, year_id: uuid.UUID) -> HeldBackOut:
    """Students of the year confirmed as detained, for the promotion screen to pre-fill its
    held-back list (``year_result.read``; FR-YRE-008), with those in no-detention classes and
    the count still unconfirmed. The promotion routes are unchanged."""
    return service.held_back(db, ctx, year_id)


@router.get("/academic-years/{year_id}/udise-progression", response_model=ProgressionOut)
def udise_progression(ctx: YearResultReader, db: TenantDB, year_id: uuid.UUID) -> ProgressionOut:
    """UDISE+ progression check sheet (``year_result.read``; FR-YRE-010): per student finishing
    the year, class and section, the confirmed result, % marks and days attended, the PEN, and
    the blanks to fill before the portal."""
    return service.progression(db, ctx, year_id)


_CSV_DOC: dict[int | str, dict[str, Any]] = {
    200: {
        "description": "CSV file (UTF-8 with BOM), one row per student",
        "content": {"text/csv": {"schema": {"type": "string"}}},
    }
}


@router.get(
    "/academic-years/{year_id}/udise-progression/csv",
    response_class=Response,
    responses=_CSV_DOC,
)
def udise_progression_csv(ctx: PortalExporter, db: TenantDB, year_id: uuid.UUID) -> Response:
    """Download the progression check sheet as CSV (``export.portal`` with step-up MFA, 428
    without it, and ``year_result.read`` school-wide, 403 without it; FR-YRE-013). Audited
    ``year_end.progression_exported`` before the file is sent."""
    file = service.progression_csv(db, ctx, year_id)
    return Response(
        content=file.body,
        media_type=year_end.CSV_MEDIA_TYPE,
        headers={
            "Content-Disposition": f'attachment; filename="{file.filename}"',
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )
