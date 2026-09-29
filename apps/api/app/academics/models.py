"""Attendance, exams and marks tables (migration 0035_student_insights; docs/05 §6.4).

Typed mappings for queries only; DDL (RLS, CHECKs, composite FKs, column grants, the offboarding
purge policy) lives in the migration.
"""

from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal

from sqlalchemy import Boolean, Date, Integer, Numeric, Text, text
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


__all__ = ["AttendanceMark", "Exam", "ExamMark"]
