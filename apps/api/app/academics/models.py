"""Attendance, exams, marks and school calendar tables (migrations 0035_student_insights,
0084_exam_lock_enrol, 0089_school_calendar; docs/05 §6.4).

Typed mappings for queries only; DDL (RLS, CHECKs, composite FKs, column grants, the offboarding
purge policy) lives in the migration.
"""

from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal

from sqlalchemy import Boolean, Date, Integer, Numeric, SmallInteger, Text, text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from app.core.model_base import Base


class AttendanceMark(Base):
    __tablename__ = "attendance_marks"
    __table_args__ = {"schema": "sis"}  # noqa: RUF012

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    tenant_id: Mapped[uuid.UUID]
    student_id: Mapped[uuid.UUID]
    section_id: Mapped[uuid.UUID]
    on_date: Mapped[dt.date] = mapped_column(Date)
    status: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(Text, server_default=text("'mark'"))
    recorded_by: Mapped[uuid.UUID]
    created_at: Mapped[dt.datetime] = mapped_column(server_default=text("now()"))
    updated_at: Mapped[dt.datetime] = mapped_column(server_default=text("now()"))
    version: Mapped[int] = mapped_column(Integer, server_default=text("1"))


class Exam(Base):
    __tablename__ = "exams"
    __table_args__ = {"schema": "sis"}  # noqa: RUF012

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    tenant_id: Mapped[uuid.UUID]
    academic_year_id: Mapped[uuid.UUID]
    name: Mapped[str] = mapped_column(Text)
    held_on: Mapped[dt.date] = mapped_column(Date)
    # FT-01 (0083): locked exams refuse marks writes; the last unlock and its reason.
    locked_at: Mapped[dt.datetime | None]
    locked_by: Mapped[uuid.UUID | None]
    unlocked_at: Mapped[dt.datetime | None]
    unlocked_by: Mapped[uuid.UUID | None]
    unlock_reason: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[uuid.UUID]
    created_at: Mapped[dt.datetime] = mapped_column(server_default=text("now()"))
    updated_at: Mapped[dt.datetime] = mapped_column(server_default=text("now()"))
    version: Mapped[int] = mapped_column(Integer, server_default=text("1"))


class ExamMark(Base):
    __tablename__ = "exam_marks"
    __table_args__ = {"schema": "sis"}  # noqa: RUF012

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    tenant_id: Mapped[uuid.UUID]
    exam_id: Mapped[uuid.UUID]
    student_id: Mapped[uuid.UUID]
    section_id: Mapped[uuid.UUID]
    subject: Mapped[str] = mapped_column(Text)
    max_marks: Mapped[Decimal] = mapped_column(Numeric(6, 2))
    marks: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))
    absent: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    source: Mapped[str] = mapped_column(Text, server_default=text("'mark'"))
    recorded_by: Mapped[uuid.UUID]
    created_at: Mapped[dt.datetime] = mapped_column(server_default=text("now()"))
    updated_at: Mapped[dt.datetime] = mapped_column(server_default=text("now()"))
    version: Mapped[int] = mapped_column(Integer, server_default=text("1"))


class ExamMarkHistory(Base):
    """One change of a stored mark (append-only; FT-01, FR-MRK-012)."""

    __tablename__ = "exam_mark_history"
    __table_args__ = {"schema": "sis"}  # noqa: RUF012

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    tenant_id: Mapped[uuid.UUID]
    exam_id: Mapped[uuid.UUID]
    mark_id: Mapped[uuid.UUID]
    student_id: Mapped[uuid.UUID]
    section_id: Mapped[uuid.UUID]
    subject: Mapped[str] = mapped_column(Text)
    previous_max_marks: Mapped[Decimal] = mapped_column(Numeric(6, 2))
    previous_marks: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))
    previous_absent: Mapped[bool] = mapped_column(Boolean)
    new_max_marks: Mapped[Decimal] = mapped_column(Numeric(6, 2))
    new_marks: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))
    new_absent: Mapped[bool] = mapped_column(Boolean)
    source: Mapped[str] = mapped_column(Text)
    after_lock: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    reason: Mapped[str | None] = mapped_column(Text)
    changed_by: Mapped[uuid.UUID]
    changed_at: Mapped[dt.datetime] = mapped_column(server_default=text("now()"))


class SchoolCalendar(Base):
    """One academic year's weekly pattern (FT-06, FR-CAL-001)."""

    __tablename__ = "school_calendars"
    __table_args__ = {"schema": "sis"}  # noqa: RUF012

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    tenant_id: Mapped[uuid.UUID]
    academic_year_id: Mapped[uuid.UUID]
    weekly_off: Mapped[list[int]] = mapped_column(ARRAY(SmallInteger))
    saturdays_off: Mapped[list[int]] = mapped_column(ARRAY(SmallInteger))
    created_by: Mapped[uuid.UUID]
    updated_by: Mapped[uuid.UUID]
    created_at: Mapped[dt.datetime] = mapped_column(server_default=text("now()"))
    updated_at: Mapped[dt.datetime] = mapped_column(server_default=text("now()"))
    version: Mapped[int] = mapped_column(Integer, server_default=text("1"))


class CalendarEntry(Base):
    """A holiday, vacation or extra working day, whole school or one class (FR-CAL-002)."""

    __tablename__ = "calendar_entries"
    __table_args__ = {"schema": "sis"}  # noqa: RUF012

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    tenant_id: Mapped[uuid.UUID]
    academic_year_id: Mapped[uuid.UUID]
    class_id: Mapped[uuid.UUID | None]
    kind: Mapped[str] = mapped_column(Text)
    name: Mapped[str] = mapped_column(Text)
    starts_on: Mapped[dt.date] = mapped_column(Date)
    ends_on: Mapped[dt.date] = mapped_column(Date)
    suggestion_key: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[uuid.UUID]
    created_at: Mapped[dt.datetime] = mapped_column(server_default=text("now()"))


__all__ = [
    "AttendanceMark",
    "CalendarEntry",
    "Exam",
    "ExamMark",
    "ExamMarkHistory",
    "SchoolCalendar",
]
