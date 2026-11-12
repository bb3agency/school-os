"""``fin.cheque_status_events`` (migration 0103_bank_matching; FM-08; FR-FEE-131..137; docs/05
§15.1).

Describes the table for typed queries only; DDL (RLS, CHECKs, the append-only and order triggers,
grants) lives in the migration. Private to ``app.fees``.
"""

from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import Date, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.model_base import Base
from app.fees.models import _args


class ChequeStatusEvent(Base):
    __tablename__ = "cheque_status_events"
    __table_args__ = _args()

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    tenant_id: Mapped[uuid.UUID]
    receipt_id: Mapped[uuid.UUID]
    status: Mapped[str] = mapped_column(Text)
    event_date: Mapped[dt.date] = mapped_column(Date)
    source: Mapped[str] = mapped_column(Text)
    statement_line_id: Mapped[uuid.UUID | None]
    cancellation_id: Mapped[uuid.UUID | None]
    recorded_by: Mapped[uuid.UUID]
    recorded_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())
    created_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())


CHEQUE_MODELS: tuple[type[Base], ...] = (ChequeStatusEvent,)
