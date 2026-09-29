"""Behaviour notes, flags, actions and settings tables (migration 0035_student_insights;
docs/05 §6.4).

Typed mappings for queries only; DDL (RLS, CHECKs, composite FKs, column grants, the offboarding
purge policy) lives in the migration. ``*_ciphertext`` columns hold C3 text encrypted with the
school's DEK (:mod:`app.insights.crypto`); they are never logged or returned undecrypted.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from sqlalchemy import Date, Integer, LargeBinary, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.model_base import Base


class BehaviourNote(Base):
    __tablename__ = "behaviour_notes"
    __table_args__ = {"schema": "sis"}  # noqa: RUF012

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    tenant_id: Mapped[uuid.UUID]
    student_id: Mapped[uuid.UUID]
    section_id: Mapped[uuid.UUID]
    category: Mapped[str] = mapped_column(Text)
    noted_on: Mapped[dt.date] = mapped_column(Date)
    body_ciphertext: Mapped[bytes] = mapped_column(LargeBinary)
    key_version: Mapped[int] = mapped_column(Integer)
    created_by: Mapped[uuid.UUID]
    created_by_membership: Mapped[uuid.UUID]
    created_at: Mapped[dt.datetime] = mapped_column(server_default=text("now()"))


class InsightFlag(Base):
    __tablename__ = "insight_flags"
    __table_args__ = {"schema": "sis"}  # noqa: RUF012

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    tenant_id: Mapped[uuid.UUID]
    student_id: Mapped[uuid.UUID]
    section_id: Mapped[uuid.UUID]
    indicator: Mapped[str] = mapped_column(Text)
    rule: Mapped[str] = mapped_column(Text)
    rules_version: Mapped[int] = mapped_column(Integer)
    basis: Mapped[str] = mapped_column(Text)
    evidence: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    status: Mapped[str] = mapped_column(Text, server_default=text("'open'"))
    owner_membership_id: Mapped[uuid.UUID | None]
    raised_on: Mapped[dt.date] = mapped_column(Date)
    due_on: Mapped[dt.date] = mapped_column(Date)
    raised_by: Mapped[uuid.UUID | None]
    first_action_at: Mapped[dt.datetime | None]
    closed_at: Mapped[dt.datetime | None]
    closed_by: Mapped[uuid.UUID | None]
    close_reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = mapped_column(server_default=text("now()"))
    updated_at: Mapped[dt.datetime] = mapped_column(server_default=text("now()"))
    version: Mapped[int] = mapped_column(Integer, server_default=text("1"))


class FlagAction(Base):
    __tablename__ = "flag_actions"
    __table_args__ = {"schema": "sis"}  # noqa: RUF012

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    tenant_id: Mapped[uuid.UUID]
    flag_id: Mapped[uuid.UUID]
    kind: Mapped[str] = mapped_column(Text)
    acted_on: Mapped[dt.date] = mapped_column(Date)
    note_ciphertext: Mapped[bytes | None] = mapped_column(LargeBinary)
    key_version: Mapped[int | None] = mapped_column(Integer)
    created_by: Mapped[uuid.UUID]
    created_by_membership: Mapped[uuid.UUID]
    created_at: Mapped[dt.datetime] = mapped_column(server_default=text("now()"))


class InsightSettings(Base):
    __tablename__ = "insight_settings"
    __table_args__ = {"schema": "sis"}  # noqa: RUF012

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    tenant_id: Mapped[uuid.UUID]
    rules: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    updated_by: Mapped[uuid.UUID | None]
    created_at: Mapped[dt.datetime] = mapped_column(server_default=text("now()"))
    updated_at: Mapped[dt.datetime] = mapped_column(server_default=text("now()"))
    version: Mapped[int] = mapped_column(Integer, server_default=text("1"))


__all__ = ["BehaviourNote", "FlagAction", "InsightFlag", "InsightSettings"]
