"""``core.public_handles`` (migration 0055_public_handles; docs/05 §15.5; ADR-0047).

Describes the table for typed queries only; DDL (RLS, the ``definer_access`` and
``purge_expired_only`` policies, column grants, the guardian FK into ``sis.guardians``, the
update guard trigger) lives in the migration, so this module never imports another module's
models.
"""

from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import Integer, LargeBinary, Text, UniqueConstraint, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.model_base import Base

SCHEMA = "core"


class PublicHandle(Base):
    """One public key of one school; only ``SHA-256(key)`` is stored (``key_hash``)."""

    __tablename__ = "public_handles"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id"),
        UniqueConstraint("kind", "key_hash"),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    tenant_id: Mapped[uuid.UUID]
    kind: Mapped[str] = mapped_column(Text)
    key_hash: Mapped[bytes] = mapped_column(LargeBinary)
    target_type: Mapped[str] = mapped_column(Text)
    target_id: Mapped[uuid.UUID]
    guardian_id: Mapped[uuid.UUID | None]
    expires_at: Mapped[dt.datetime | None]
    revoked_at: Mapped[dt.datetime | None]
    revoked_reason: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[uuid.UUID | None]
    created_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())
    first_viewed_at: Mapped[dt.datetime | None]
    last_viewed_at: Mapped[dt.datetime | None]
    view_count: Mapped[int] = mapped_column(Integer, server_default=text("0"))


__all__ = ["PublicHandle"]
