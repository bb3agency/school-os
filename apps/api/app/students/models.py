"""Student record tables in schema ``sis`` (migrations 0008_sis_students and 0022_promotions,
docs/05 §5).

Models describe tables for typed queries only; DDL (RLS, triggers, grants) lives in the
migration. FKs into other modules' tables (``core.sections``, ``core.academic_years``,
``core.users``) are enforced by the database and not declared here, so this module never imports
another module's models.
"""

from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    Boolean,
    Date,
    ForeignKeyConstraint,
    Integer,
    LargeBinary,
    Numeric,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column

from app.core.model_base import Base

SCHEMA = "sis"

STUDENT_STATUSES = ("provisional", "active", "left", "graduated")
ENROLLMENT_STATUSES = ("active", "transferred", "completed")
SOURCES = (
    "admission_register",
    "aadhaar_as_printed",
    "udise_plus",
    "board_registration",
    "birth_certificate",
    "parent_form",
    "tc_incoming",
    "manual_entry",
)
VERIFICATION_STATUSES = ("unverified", "verified", "rejected")
RELATIONSHIPS = ("father", "mother", "guardian")


class Student(Base):
    """A student: stable internal id independent of the admission number (FR-STU-001)."""

    __tablename__ = "students"
    __table_args__ = (UniqueConstraint("tenant_id", "id"), {"schema": SCHEMA})

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    tenant_id: Mapped[uuid.UUID]
    status: Mapped[str] = mapped_column(Text, server_default=text("'active'"))
    admission_no: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())
    version: Mapped[int] = mapped_column(Integer, server_default=text("1"))


class Enrollment(Base):
    __tablename__ = "enrollments"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id"),
        ForeignKeyConstraint(
            ["tenant_id", "student_id"], ["sis.students.tenant_id", "sis.students.id"]
        ),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    tenant_id: Mapped[uuid.UUID]
    student_id: Mapped[uuid.UUID]
    section_id: Mapped[uuid.UUID]
    academic_year_id: Mapped[uuid.UUID]
    roll_no: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, server_default=text("'active'"))
    started_on: Mapped[dt.date | None] = mapped_column(Date)
    ended_on: Mapped[dt.date | None] = mapped_column(Date)
    created_by: Mapped[uuid.UUID | None]
    created_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())
    version: Mapped[int] = mapped_column(Integer, server_default=text("1"))


class AttributeDefinition(Base):
    """Global (``tenant_id`` NULL, read-only for the app) or school-specific attribute."""

    __tablename__ = "attribute_definitions"
    __table_args__ = {"schema": SCHEMA}  # noqa: RUF012

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    tenant_id: Mapped[uuid.UUID | None]
    key: Mapped[str] = mapped_column(Text)
    data_type: Mapped[str] = mapped_column(Text)
    classification: Mapped[str] = mapped_column(Text)
    is_identity: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    validation: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    canonical_policy: Mapped[dict[str, Any]] = mapped_column(JSONB)
    label_en: Mapped[str] = mapped_column(Text)
    label_te: Mapped[str] = mapped_column(Text)
    sort_order: Mapped[int] = mapped_column(Integer, server_default=text("1000"))
    created_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())


class AttributeValue(Base):
    """One observed value of one attribute from one source (FR-STU-002, FR-STU-003)."""

    __tablename__ = "attribute_values"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id"),
        ForeignKeyConstraint(
            ["tenant_id", "student_id"], ["sis.students.tenant_id", "sis.students.id"]
        ),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    tenant_id: Mapped[uuid.UUID]
    student_id: Mapped[uuid.UUID]
    attribute_key: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(Text)
    value_text: Mapped[str | None] = mapped_column(Text)
    value_date: Mapped[dt.date | None] = mapped_column(Date)
    value_norm: Mapped[str | None] = mapped_column(Text)
    value_ciphertext: Mapped[bytes | None] = mapped_column(LargeBinary)
    value_blind_index: Mapped[bytes | None] = mapped_column(LargeBinary)
    key_version: Mapped[int | None] = mapped_column(Integer)
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(4, 3))
    verification_status: Mapped[str] = mapped_column(Text, server_default=text("'unverified'"))
    verified_by: Mapped[uuid.UUID | None]
    verified_at: Mapped[dt.datetime | None]
    evidence_document_id: Mapped[uuid.UUID | None]
    import_batch_id: Mapped[uuid.UUID | None]
    change_request_id: Mapped[uuid.UUID | None]
    recorded_by: Mapped[uuid.UUID]
    recorded_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())
    superseded_by: Mapped[uuid.UUID | None]


class StudentProfile(Base):
    """C2-only projection for lists and search, maintained with every value write."""

    __tablename__ = "student_profiles"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "student_id"], ["sis.students.tenant_id", "sis.students.id"]
        ),
        {"schema": SCHEMA},
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    student_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    full_name: Mapped[str | None] = mapped_column(Text)
    full_name_norm: Mapped[str | None] = mapped_column(Text)
    full_name_translit: Mapped[str | None] = mapped_column(Text)
    dob: Mapped[dt.date | None] = mapped_column(Date)
    gender: Mapped[str | None] = mapped_column(Text)
    father_name_norm: Mapped[str | None] = mapped_column(Text)
    mother_name_norm: Mapped[str | None] = mapped_column(Text)
    current_section_id: Mapped[uuid.UUID | None]
    status: Mapped[str | None] = mapped_column(Text)
    search_tsv: Mapped[Any | None] = mapped_column(TSVECTOR)
    updated_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())


class Guardian(Base):
    """Parent or guardian; phone and address are C3 ciphertext (FR-STU-008)."""

    __tablename__ = "guardians"
    __table_args__ = (UniqueConstraint("tenant_id", "id"), {"schema": SCHEMA})

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    tenant_id: Mapped[uuid.UUID]
    full_name: Mapped[str] = mapped_column(Text)
    full_name_norm: Mapped[str | None] = mapped_column(Text)
    phone_ciphertext: Mapped[bytes | None] = mapped_column(LargeBinary)
    phone_blind_index: Mapped[bytes | None] = mapped_column(LargeBinary)
    address_ciphertext: Mapped[bytes | None] = mapped_column(LargeBinary)
    key_version: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())
    version: Mapped[int] = mapped_column(Integer, server_default=text("1"))


class StudentGuardian(Base):
    __tablename__ = "student_guardians"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "student_id"], ["sis.students.tenant_id", "sis.students.id"]
        ),
        ForeignKeyConstraint(
            ["tenant_id", "guardian_id"], ["sis.guardians.tenant_id", "sis.guardians.id"]
        ),
        {"schema": SCHEMA},
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    student_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    guardian_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    relationship: Mapped[str] = mapped_column(Text)
    is_primary: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    created_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())


PROMOTION_OUTCOMES = ("promoted", "held_back", "graduated")


class PromotionRun(Base):
    """One committed year-end promotion (migration 0022_promotions; FR-TEN-011)."""

    __tablename__ = "promotion_runs"
    __table_args__ = (UniqueConstraint("tenant_id", "id"), {"schema": SCHEMA})

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    tenant_id: Mapped[uuid.UUID]
    from_academic_year_id: Mapped[uuid.UUID]
    to_academic_year_id: Mapped[uuid.UUID]
    status: Mapped[str] = mapped_column(Text, server_default=text("'committed'"))
    promoted_count: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    held_back_count: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    graduated_count: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    skipped_count: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    plan_fingerprint: Mapped[str] = mapped_column(Text)
    committed_by: Mapped[uuid.UUID | None]
    committed_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())
    undone_by: Mapped[uuid.UUID | None]
    undone_at: Mapped[dt.datetime | None]
    created_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())
    version: Mapped[int] = mapped_column(Integer, server_default=text("1"))


class PromotionItem(Base):
    """What one promotion did to one student: the enrolment it closed and the one it opened."""

    __tablename__ = "promotion_items"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "run_id"], ["sis.promotion_runs.tenant_id", "sis.promotion_runs.id"]
        ),
        {"schema": SCHEMA},
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    run_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    student_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    outcome: Mapped[str] = mapped_column(Text)
    from_enrollment_id: Mapped[uuid.UUID]
    from_enrollment_version: Mapped[int] = mapped_column(Integer)
    to_enrollment_id: Mapped[uuid.UUID | None]
    to_enrollment_version: Mapped[int | None] = mapped_column(Integer)
    previous_student_status: Mapped[str] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())
