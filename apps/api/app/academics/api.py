"""Attendance, exams and marks routes (docs/09 Attendance and marks; M5; US-1701..US-1703;
FR-ATT-*, FR-MRK-*).

Every route declares its guard; class teachers hold the permissions for their sections only
(07 §6.2 S) and the service answers 404 for any other section. Sheet previews store nothing:
the entries go back to the person, who sends them to the write route (``source=import``).
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response

from app.academics import service
from app.academics.schemas import (
    AttendanceDayOut,
    AttendanceMonthOut,
    AttendanceSheetOut,
    AttendanceWrite,
    ExamCreate,
    ExamOut,
    MarksGridOut,
    MarksSheetOut,
    MarksWrite,
    Month,
    SheetIn,
    WriteResultOut,
)
from app.authz.context import UserContext
from app.authz.dependencies import TenantDB, require, require_any
from app.authz.http import IdempotencyDep, etag

router = APIRouter(prefix="/api/v1", tags=["attendance and marks"])

AttendanceReader = Annotated[UserContext, Depends(require(service.ATTENDANCE_READ))]
AttendanceRecorder = Annotated[UserContext, Depends(require(service.ATTENDANCE_RECORD))]
ExamReader = Annotated[
    UserContext,
    Depends(require_any(service.MARKS_READ, service.MARKS_RECORD, service.EXAM_MANAGE)),
]
ExamManager = Annotated[UserContext, Depends(require(service.EXAM_MANAGE, scope="school"))]
MarksReader = Annotated[UserContext, Depends(require(service.MARKS_READ))]
MarksRecorder = Annotated[UserContext, Depends(require(service.MARKS_RECORD))]


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
    day again corrects it. 422 ``not_in_section``, ``future_date``, ``date_out_of_range`` or
    ``duplicate_entry`` per entry. The early-warning rules look at these students again."""
    return service.record_attendance(db, ctx, section_id, body)


@router.post("/sections/{section_id}/attendance/sheet", response_model=AttendanceSheetOut)
def preview_attendance_sheet(
    ctx: AttendanceRecorder, section_id: uuid.UUID, body: SheetIn
) -> AttendanceSheetOut:
    """Read an uploaded attendance sheet (``attendance.record``): first column admission or
    roll number, then one date per column, cells P/A/L/LV. Returns the entries and every
    problem; stores no attendance. The uploaded file is deleted once read. 422
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


# --- marks ----------------------------------------------------------------------------------------


@router.get("/sections/{section_id}/exams/{exam_id}/marks", response_model=MarksGridOut)
def section_marks(
    ctx: MarksReader, db: TenantDB, section_id: uuid.UUID, exam_id: uuid.UUID
) -> MarksGridOut:
    """The section's marks for one exam with each student's overall percentage
    (``marks.read``)."""
    return service.section_marks(db, ctx, section_id, exam_id)


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
    ``exam_other_year``..."""
    return service.record_marks(db, ctx, section_id, exam_id, body)


@router.post("/sections/{section_id}/exams/{exam_id}/marks/sheet", response_model=MarksSheetOut)
def preview_marks_sheet(
    ctx: MarksRecorder, section_id: uuid.UUID, exam_id: uuid.UUID, body: SheetIn
) -> MarksSheetOut:
    """Read an uploaded marks sheet (``marks.record``): row 1 admission or roll number and
    subjects, row 2 maximum marks, then one row per student (``AB`` = absent). Returns entries
    and problems; stores no marks; the file is deleted once read."""
    return service.preview_marks_sheet(ctx, section_id, exam_id, body)
