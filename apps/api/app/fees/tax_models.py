"""``fin.tax_certificates`` (migration 0083_fee_tax_cert; FM-06; FR-FEE-046..060).

Describes the table for typed queries only; DDL (RLS, CHECKs, the frozen trigger, column grants)
lives in the migration.
"""

from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy import Date, Integer, LargeBinary, Text, UniqueConstraint, func
from sqlalchemy import text as sql_text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.model_base import Base
from app.fees.models import MONEY, SCHEMA


class TaxCertificate(Base):
    __tablename__ = "tax_certificates"
    __table_args__ = (UniqueConstraint("tenant_id", "id"), {"schema": SCHEMA})

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    tenant_id: Mapped[uuid.UUID]
    student_id: Mapped[uuid.UUID]
    financial_year: Mapped[str] = mapped_column(Text)
    serial_no: Mapped[int] = mapped_column(Integer)
    serial_number: Mapped[str] = mapped_column(Text)
    as_of: Mapped[dt.date] = mapped_column(Date)
    amount: Mapped[Decimal] = mapped_column(MONEY)
    receipt_count: Mapped[int] = mapped_column(Integer)
    content: Mapped[dict[str, Any]] = mapped_column(JSONB)
    content_sha256: Mapped[bytes] = mapped_column(LargeBinary)
    basis_sha256: Mapped[bytes] = mapped_column(LargeBinary)
    status: Mapped[str] = mapped_column(Text, server_default=sql_text("'issued'"))
    superseded_by: Mapped[uuid.UUID | None]
    issued_by: Mapped[uuid.UUID]
    issued_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())
    document_id: Mapped[uuid.UUID | None]
    pdf_status: Mapped[str] = mapped_column(Text, server_default=sql_text("'queued'"))
    pdf_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())
    version: Mapped[int] = mapped_column(Integer, server_default=sql_text("1"))


__all__ = ["TaxCertificate"]
