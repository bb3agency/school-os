"""Sending tables in schema ``msg`` (migration 0061_messaging_sends; docs/05 §15.3).

DDL (RLS, grants, composite FKs into ``ops.parent_notices`` and ``core.users``) lives in the
migration; these classes are for typed queries only.
"""

from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy import ARRAY, Boolean, Date, Integer, Numeric, Text, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.model_base import Base

SCHEMA = "msg"


class Broadcast(Base):
    """One notice broadcast: its preview counts and, once confirmed, its sending state."""

    __tablename__ = "broadcasts"
    __table_args__ = (UniqueConstraint("tenant_id", "id"), {"schema": SCHEMA})

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    tenant_id: Mapped[uuid.UUID]
    notice_id: Mapped[uuid.UUID]
    section_ids: Mapped[list[uuid.UUID]] = mapped_column(ARRAY(UUID(as_uuid=True)))
    status: Mapped[str] = mapped_column(Text, server_default=text("'previewed'"))
    resend: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    resend_reason: Mapped[str | None] = mapped_column(Text)
    recipients: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    skipped: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    languages: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    language_fallbacks: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    already_sent: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    estimated_cost_inr: Mapped[Decimal] = mapped_column(Numeric(12, 2), server_default=text("0"))
    queued: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    requested_by: Mapped[uuid.UUID]
    confirmed_by: Mapped[uuid.UUID | None]
    confirmed_at: Mapped[dt.datetime | None]
    completed_at: Mapped[dt.datetime | None]
    expires_at: Mapped[dt.datetime]
    created_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())
    version: Mapped[int] = mapped_column(Integer, server_default=text("1"))


class SendRun(Base):
    """One automatic job run per school and IST day (absence alerts, fee reminders)."""

    __tablename__ = "send_runs"
    __table_args__ = (UniqueConstraint("tenant_id", "id"), {"schema": SCHEMA})

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    tenant_id: Mapped[uuid.UUID]
    job: Mapped[str] = mapped_column(Text)
    run_on: Mapped[dt.date] = mapped_column(Date)
    started_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())
    finished_at: Mapped[dt.datetime | None]
    considered: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    queued: Mapped[int] = mapped_column(Integer, server_default=text("0"))


class Closure(Base):
    """One urgent closure message the principal sent (FP-02, migration 0093_msg_reach_closure;
    FR-MSG-047..055). Insert-only; the reason is a config code, never free text in the message.
    ``notice_text`` (0100_closure_reopen, FR-MSG-056): the order text the principal typed, shown
    only on the closure's parent page."""

    __tablename__ = "closures"
    __table_args__ = (UniqueConstraint("tenant_id", "id"), {"schema": SCHEMA})

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    tenant_id: Mapped[uuid.UUID]
    whole_school: Mapped[bool] = mapped_column(Boolean)
    section_ids: Mapped[list[uuid.UUID]] = mapped_column(
        ARRAY(UUID(as_uuid=True)), server_default=text("'{}'")
    )
    closed_from: Mapped[dt.date] = mapped_column(Date)
    closed_to: Mapped[dt.date] = mapped_column(Date)
    reason_code: Mapped[str] = mapped_column(Text)
    recipients: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    skipped: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    languages: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    estimated_cost_inr: Mapped[Decimal] = mapped_column(Numeric(12, 2), server_default=text("0"))
    queued: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    sent_by: Mapped[uuid.UUID]
    sent_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())
    created_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())
    notice_text: Mapped[str | None] = mapped_column(Text)


class ClosureReopening(Base):
    """The "school reopens" message of one closure (FP-02 follow-up, migration
    0100_closure_reopen; FR-MSG-059..064). Insert-only; at most one per closure."""

    __tablename__ = "closure_reopenings"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id"),
        UniqueConstraint("tenant_id", "closure_id"),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    tenant_id: Mapped[uuid.UUID]
    closure_id: Mapped[uuid.UUID]
    reopens_on: Mapped[dt.date] = mapped_column(Date)
    recipients: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    skipped: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    languages: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    estimated_cost_inr: Mapped[Decimal] = mapped_column(Numeric(12, 2), server_default=text("0"))
    queued: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    sent_by: Mapped[uuid.UUID]
    sent_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())
    created_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())


__all__ = ["SCHEMA", "Broadcast", "Closure", "ClosureReopening", "SendRun"]
