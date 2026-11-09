"""Database access for the Tally voucher export (0088_tally_vouchers; FM-03).

Callers inside ``app.tally`` only. Every function runs in a ``core.db.tenant_session``: RLS limits
each statement to that school and ``tenant_id`` is taken from the session's context. Bound
parameters only.
"""

from __future__ import annotations

import uuid
from collections.abc import Collection, Mapping, Sequence
from typing import Any

from sqlalchemy import func, insert, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.tally.models import ExportedVoucher, ExportSettings, Party, VoucherExport
from app.tally.repository import current_tenant_id


def get_settings(session: Session, *, lock: bool = False) -> ExportSettings | None:
    stmt = select(ExportSettings)
    if lock:
        stmt = stmt.with_for_update()
    return session.scalars(stmt, execution_options={"populate_existing": True}).one_or_none()


def insert_settings(session: Session, values: Mapping[str, Any]) -> ExportSettings:
    return session.scalars(
        insert(ExportSettings)
        .values(tenant_id=current_tenant_id(session), **values)
        .returning(ExportSettings)
    ).one()


def update_settings(
    session: Session, settings_id: uuid.UUID, values: Mapping[str, Any]
) -> ExportSettings:
    return session.scalars(
        update(ExportSettings)
        .where(ExportSettings.id == settings_id)
        .values(**values, version=ExportSettings.version + 1)
        .returning(ExportSettings),
        execution_options={"populate_existing": True},
    ).one()


def exported_keys(session: Session, keys: Collection[str]) -> set[str]:
    """Which of ``keys`` are already in an earlier file."""
    if not keys:
        return set()
    found: set[str] = set()
    chunk = list(keys)
    for i in range(0, len(chunk), 5000):
        found.update(
            session.scalars(
                select(ExportedVoucher.voucher_key).where(
                    ExportedVoucher.voucher_key.in_(chunk[i : i + 5000])
                )
            )
        )
    return found


def insert_export(session: Session, values: Mapping[str, Any]) -> VoucherExport:
    return session.scalars(
        insert(VoucherExport)
        .values(tenant_id=current_tenant_id(session), **values)
        .returning(VoucherExport)
    ).one()


def record_vouchers(session: Session, rows: Sequence[Mapping[str, Any]]) -> None:
    """First export of each key; a key exported before keeps its first export."""
    if not rows:
        return
    tenant_id = current_tenant_id(session)
    for i in range(0, len(rows), 2000):
        session.execute(
            pg_insert(ExportedVoucher)
            .values([{"tenant_id": tenant_id, **r} for r in rows[i : i + 2000]])
            .on_conflict_do_nothing(index_elements=["tenant_id", "voucher_key"])
        )


def party_by_name(session: Session, ledger_name: str) -> Party | None:
    """The present ledger of the last snapshot with this name (Tally compares names without
    case), or ``None``."""
    return session.scalars(
        select(Party)
        .where(Party.present.is_(True), func.lower(Party.ledger_name) == ledger_name.lower())
        .order_by(Party.ledger_name, Party.id)
        .limit(1)
    ).one_or_none()


def list_exports(session: Session, limit: int) -> list[VoucherExport]:
    return list(
        session.scalars(
            select(VoucherExport)
            .order_by(VoucherExport.created_at.desc(), VoucherExport.id.desc())
            .limit(limit)
        )
    )


__all__ = [
    "exported_keys",
    "get_settings",
    "insert_export",
    "insert_settings",
    "list_exports",
    "party_by_name",
    "record_vouchers",
    "update_settings",
]
