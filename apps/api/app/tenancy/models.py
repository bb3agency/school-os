"""Tenancy tables in schema ``core`` (migrations 0003_core_schema, 0023_api_gaps; docs/05 §4).

``sections.class_teacher_membership_id`` references ``core.memberships`` (identity module); that
composite FK is enforced by the database only and not declared here, so this module never imports
another module's models.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from sqlalchemy import (
    Boolean,
    Date,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    LargeBinary,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.model_base import Base

SCHEMA = "core"

TENANT_STATUSES = ("provisioning", "active", "suspended", "offboarding", "deleted")
PLAN_TIERS = ("shared", "dedicated")
DEPLOYMENT_MODES = ("shared", "dedicated")


class Tenant(Base):
    """One school. The app sees only its own row (policy ``own_tenant``)."""

    __tablename__ = "tenants"
    __table_args__ = {"schema": SCHEMA}  # noqa: RUF012

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(Text, unique=True)
    name: Mapped[str] = mapped_column(Text)
    boards: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default=text("'{}'"))
    state_code: Mapped[str] = mapped_column(Text, server_default=text("'AP'"))
    status: Mapped[str] = mapped_column(Text, server_default=text("'provisioning'"))
    plan_tier: Mapped[str] = mapped_column(Text, server_default=text("'shared'"))
    deployment_mode: Mapped[str] = mapped_column(Text, server_default=text("'shared'"))
    settings: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    created_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())
    version: Mapped[int] = mapped_column(Integer, server_default=text("1"))


class TenantKey(Base):
    """Wrapped per-tenant DEK + HMAC key (envelope encryption, docs/05 §9)."""

    __tablename__ = "tenant_keys"
    __table_args__ = {"schema": SCHEMA}  # noqa: RUF012

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("core.tenants.id"), primary_key=True)
    key_version: Mapped[int] = mapped_column(Integer, primary_key=True)
    wrapped_dek: Mapped[bytes] = mapped_column(LargeBinary)
    wrapped_hmac: Mapped[bytes] = mapped_column(LargeBinary)
    kms_key_arn: Mapped[str] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())
    retired_at: Mapped[dt.datetime | None]


class AcademicYear(Base):
    __tablename__ = "academic_years"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id"),
        UniqueConstraint("tenant_id", "label"),
        ForeignKeyConstraint(["tenant_id"], ["core.tenants.id"]),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    tenant_id: Mapped[uuid.UUID]
    label: Mapped[str] = mapped_column(Text)
    starts_on: Mapped[dt.date] = mapped_column(Date)
    ends_on: Mapped[dt.date] = mapped_column(Date)
    is_current: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    archived_at: Mapped[dt.datetime | None]
    created_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())
    version: Mapped[int] = mapped_column(Integer, server_default=text("1"))


class SchoolClass(Base):
    """A class/grade (``NUR``, ``LKG``, ``UKG``, ``I`` .. ``XII``); not per academic year."""

    __tablename__ = "classes"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id"),
        UniqueConstraint("tenant_id", "code"),
        ForeignKeyConstraint(["tenant_id"], ["core.tenants.id"]),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    tenant_id: Mapped[uuid.UUID]
    code: Mapped[str] = mapped_column(Text)
    display_en: Mapped[str] = mapped_column(Text)
    display_te: Mapped[str] = mapped_column(Text)
    sort_order: Mapped[int] = mapped_column(Integer)
    archived_at: Mapped[dt.datetime | None]
    created_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())
    version: Mapped[int] = mapped_column(Integer, server_default=text("1"))


class Section(Base):
    """A section of a class in one academic year (e.g. IX-A 2026-27)."""

    __tablename__ = "sections"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id"),
        UniqueConstraint("tenant_id", "academic_year_id", "class_id", "name"),
        ForeignKeyConstraint(
            ["tenant_id", "class_id"], ["core.classes.tenant_id", "core.classes.id"]
        ),
        ForeignKeyConstraint(
            ["tenant_id", "academic_year_id"],
            ["core.academic_years.tenant_id", "core.academic_years.id"],
        ),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    tenant_id: Mapped[uuid.UUID]
    class_id: Mapped[uuid.UUID]
    academic_year_id: Mapped[uuid.UUID]
    name: Mapped[str] = mapped_column(Text)
    class_teacher_membership_id: Mapped[uuid.UUID | None]
    archived_at: Mapped[dt.datetime | None]
    created_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())
    version: Mapped[int] = mapped_column(Integer, server_default=text("1"))
