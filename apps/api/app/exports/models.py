"""Export tables in schema ``ops`` (migration 0017_exports, docs/05 §7.3).

Typed mappings for queries only; DDL (RLS, CHECKs, the frozen-request trigger, composite FKs to
core.memberships and ops.job_runs, column grants) lives in the migration.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from sqlalchemy import ARRAY, BigInteger, Boolean, Integer, LargeBinary, Text, Uuid, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.model_base import Base

SCHEMA = "ops"


class Export(Base):
    __tablename__ = "exports"
    __table_args__ = {"schema": SCHEMA}  # noqa: RUF012

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    tenant_id: Mapped[uuid.UUID]
    kind: Mapped[str] = mapped_column(Text)
    profile_key: Mapped[str | None] = mapped_column(Text)
    profile_version: Mapped[int | None] = mapped_column(Integer)
    layout_version: Mapped[int] = mapped_column(Integer)
    formats: Mapped[list[str]] = mapped_column(ARRAY(Text))
    language: Mapped[str] = mapped_column(Text, server_default=text("'en'"))
    scope: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    columns: Mapped[list[str] | None] = mapped_column(ARRAY(Text))
    include_sensitive: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    student_ids: Mapped[list[uuid.UUID]] = mapped_column(ARRAY(Uuid(as_uuid=True)))
    student_count: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(Text, server_default=text("'queued'"))
    error_code: Mapped[str | None] = mapped_column(Text)
    job_id: Mapped[uuid.UUID | None]
    requested_by: Mapped[uuid.UUID]
    requested_by_membership: Mapped[uuid.UUID]
    created_at: Mapped[dt.datetime] = mapped_column(server_default=text("now()"))
    started_at: Mapped[dt.datetime | None]
    finished_at: Mapped[dt.datetime | None]
    expires_at: Mapped[dt.datetime | None]
    files_deleted_at: Mapped[dt.datetime | None]
    updated_at: Mapped[dt.datetime] = mapped_column(server_default=text("now()"))
    version: Mapped[int] = mapped_column(Integer, server_default=text("1"))


class ExportFile(Base):
    __tablename__ = "export_files"
    __table_args__ = {"schema": SCHEMA}  # noqa: RUF012

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    tenant_id: Mapped[uuid.UUID]
    export_id: Mapped[uuid.UUID]
    format: Mapped[str] = mapped_column(Text)
    object_key: Mapped[str] = mapped_column(Text)
    content_type: Mapped[str] = mapped_column(Text)
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    sha256: Mapped[bytes] = mapped_column(LargeBinary)
    created_at: Mapped[dt.datetime] = mapped_column(server_default=text("now()"))
