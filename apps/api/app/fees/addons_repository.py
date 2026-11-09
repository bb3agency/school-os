"""Database access of add-on fees (FM-02; FR-FEE-081..100). Internal to ``app.fees``; every
query runs in the caller's ``tenant_session`` (RLS) with bound parameters.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from collections.abc import Collection, Mapping, Sequence
from decimal import Decimal
from typing import Any

from sqlalchemy import Table, func, insert, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.core.record_tables import dump_table
from app.core.records import RecordTable
from app.fees.addon_models import FeeAddon, FeeAddonAssignment
from app.fees.models import Adjustment, LedgerEntry


def list_addons(session: Session, academic_year_id: uuid.UUID) -> list[FeeAddon]:
    stmt = (
        select(FeeAddon)
        .where(FeeAddon.academic_year_id == academic_year_id)
        .order_by(FeeAddon.label, FeeAddon.id)
    )
    return list(session.scalars(stmt))


def count_addons(session: Session, academic_year_id: uuid.UUID) -> int:
    value: int = session.execute(
        select(func.count())
        .select_from(FeeAddon)
        .where(FeeAddon.academic_year_id == academic_year_id)
    ).scalar_one()
    return value


def get_addon(session: Session, addon_id: uuid.UUID, *, lock: bool = False) -> FeeAddon | None:
    stmt = select(FeeAddon).where(FeeAddon.id == addon_id)
    if lock:
        stmt = stmt.with_for_update().execution_options(populate_existing=True)
    row: FeeAddon | None = session.scalars(stmt).one_or_none()
    return row


def insert_addon(session: Session, **values: Any) -> FeeAddon:
    return session.scalars(insert(FeeAddon).values(**values).returning(FeeAddon)).one()


def update_addon(session: Session, addon_id: uuid.UUID, values: Mapping[str, Any]) -> FeeAddon:
    stmt = (
        update(FeeAddon)
        .where(FeeAddon.id == addon_id)
        .values(**values, version=FeeAddon.version + 1)
        .returning(FeeAddon)
        .execution_options(populate_existing=True)
    )
    return session.scalars(stmt).one()


def assigned_counts(
    session: Session, addon_ids: Collection[uuid.UUID], *, active_only: bool = True
) -> dict[uuid.UUID, int]:
    if not addon_ids:
        return {}
    stmt = (
        select(FeeAddonAssignment.addon_id, func.count())
        .where(FeeAddonAssignment.addon_id.in_(list(addon_ids)))
        .group_by(FeeAddonAssignment.addon_id)
    )
    if active_only:
        stmt = stmt.where(FeeAddonAssignment.status == "active")
    return {r[0]: int(r[1]) for r in session.execute(stmt)}


def active_on_heads(
    session: Session, account_ids: Collection[uuid.UUID], head_id: uuid.UUID
) -> dict[uuid.UUID, FeeAddonAssignment]:
    """The active assignment of each account on ``head_id`` (at most one, unique index)."""
    if not account_ids:
        return {}
    stmt = (
        select(FeeAddonAssignment)
        .where(
            FeeAddonAssignment.account_id.in_(list(account_ids)),
            FeeAddonAssignment.head_id == head_id,
            FeeAddonAssignment.status == "active",
        )
        .with_for_update()
    )
    return {r.account_id: r for r in session.scalars(stmt)}


def assignments_of_addon(session: Session, addon_id: uuid.UUID) -> list[FeeAddonAssignment]:
    stmt = (
        select(FeeAddonAssignment)
        .where(FeeAddonAssignment.addon_id == addon_id)
        .order_by(FeeAddonAssignment.assigned_at, FeeAddonAssignment.id)
    )
    return list(session.scalars(stmt))


def insert_assignment(session: Session, **values: Any) -> FeeAddonAssignment:
    return session.scalars(
        insert(FeeAddonAssignment).values(**values).returning(FeeAddonAssignment)
    ).one()


def end_assignment(
    session: Session,
    assignment_id: uuid.UUID,
    *,
    end_instalment: int,
    ended_by: uuid.UUID,
) -> FeeAddonAssignment:
    stmt = (
        update(FeeAddonAssignment)
        .where(FeeAddonAssignment.id == assignment_id)
        .values(
            status="ended",
            end_instalment=end_instalment,
            ended_by=ended_by,
            ended_at=func.now(),
            version=FeeAddonAssignment.version + 1,
        )
        .returning(FeeAddonAssignment)
        .execution_options(populate_existing=True)
    )
    return session.scalars(stmt).one()


def addon_charges(
    session: Session, assignment_ids: Collection[uuid.UUID]
) -> dict[uuid.UUID, dict[int, Decimal]]:
    """Add-on charges posted per assignment and instalment."""
    if not assignment_ids:
        return {}
    stmt = select(
        LedgerEntry.addon_assignment_id, LedgerEntry.instalment_no, LedgerEntry.amount
    ).where(LedgerEntry.addon_assignment_id.in_(list(assignment_ids)))
    out: dict[uuid.UUID, dict[int, Decimal]] = defaultdict(dict)
    for aid, no, amount in session.execute(stmt):
        if aid is not None:
            out[aid][int(no)] = Decimal(amount)
    return dict(out)


def insert_removals(session: Session, rows: Sequence[Mapping[str, Any]]) -> int:
    """``addon_removal`` adjustments; one standing per (assignment, instalment) (unique index),
    so asking twice proposes nothing more."""
    if not rows:
        return 0
    stmt = pg_insert(Adjustment).values([dict(r) for r in rows]).on_conflict_do_nothing()
    return len(session.execute(stmt.returning(Adjustment.id)).all())


def export_tables(session: Session) -> list[RecordTable]:
    """Templates and assignments of the current school for its full data export (FR-FEE-027)."""
    out: list[RecordTable] = []
    for model, name in (
        (FeeAddon, "fees_addons"),
        (FeeAddonAssignment, "fees_addon_assignments"),
    ):
        table = model.__table__
        if not isinstance(table, Table):  # pragma: no cover - declarative table
            raise TypeError(name)
        out.append(dump_table(session, table, name=name, order_by=("created_at", "id")))
    return out


__all__ = [
    "active_on_heads",
    "addon_charges",
    "assigned_counts",
    "assignments_of_addon",
    "count_addons",
    "end_assignment",
    "export_tables",
    "get_addon",
    "insert_addon",
    "insert_assignment",
    "insert_removals",
    "list_addons",
    "update_addon",
]
