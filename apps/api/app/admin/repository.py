"""Database access for the admin module. Only ``app.admin.service`` calls this module.

Every function takes a ``core.db.tenant_session()``: RLS limits each statement to that school.
Bound parameters only. The app may update only the workflow columns of ``ops.tenant_exports``
and the rules of ``ops.retention_settings``, and never deletes either (migration 0031).
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Mapping
from typing import Any

from sqlalchemy import and_, func, insert, or_, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.admin.models import RetentionSetting, TenantExport
from app.core.record_tables import dump_table
from app.core.records import RecordTable

LIVE_STATUSES = ("queued", "running")
ONE_LIVE_INDEX = "tenant_exports_one_live"


def current_tenant_id(session: Session) -> uuid.UUID:
    value: object = session.execute(text("SELECT core.current_tenant()")).scalar_one()
    if value is None:
        raise RuntimeError("tenant context is not set; use core.db.tenant_session()")
    return uuid.UUID(str(value))


def now(session: Session) -> dt.datetime:
    value: dt.datetime = session.execute(select(func.now())).scalar_one()
    return value


# --- ops.tenant_exports -----------------------------------------------------------------------


def is_one_live_violation(exc: IntegrityError) -> bool:
    diag = getattr(exc.orig, "diag", None)
    return getattr(diag, "constraint_name", None) == ONE_LIVE_INDEX


def insert_export(session: Session, **values: Any) -> TenantExport:
    return session.execute(
        insert(TenantExport).values(**values).returning(TenantExport)
    ).scalar_one()


def live_export(session: Session) -> TenantExport | None:
    stmt = select(TenantExport).where(TenantExport.status.in_(LIVE_STATUSES)).limit(1)
    return session.execute(stmt).scalar_one_or_none()


def get_export(
    session: Session, export_id: uuid.UUID, *, lock: bool = False
) -> TenantExport | None:
    stmt = select(TenantExport).where(TenantExport.id == export_id)
    if lock:
        stmt = stmt.with_for_update()
    return session.execute(stmt).scalar_one_or_none()


def update_export(
    session: Session, export_id: uuid.UUID, values: Mapping[str, Any]
) -> TenantExport:
    stmt = (
        update(TenantExport)
        .where(TenantExport.id == export_id)
        .values(**values, version=TenantExport.version + 1)
        .returning(TenantExport)
        .execution_options(synchronize_session=False, populate_existing=True)
    )
    return session.execute(stmt).scalar_one()


def list_exports(
    session: Session, *, after: tuple[dt.datetime, uuid.UUID] | None, limit: int
) -> list[TenantExport]:
    """This school's full exports, newest first."""
    stmt = select(TenantExport)
    if after is not None:
        created, last = after
        stmt = stmt.where(
            or_(
                TenantExport.created_at < created,
                and_(TenantExport.created_at == created, TenantExport.id < last),
            )
        )
    stmt = stmt.order_by(TenantExport.created_at.desc(), TenantExport.id.desc()).limit(limit)
    return list(session.scalars(stmt))


def due_for_purge(
    session: Session, *, now: dt.datetime, failed_before: dt.datetime, limit: int = 100
) -> list[TenantExport]:
    """Ready exports past ``expires_at`` and failed ones requested before ``failed_before``
    whose archive was not deleted yet."""
    stmt = (
        select(TenantExport)
        .where(
            TenantExport.files_deleted_at.is_(None),
            or_(
                and_(TenantExport.status == "ready", TenantExport.expires_at <= now),
                and_(TenantExport.status == "failed", TenantExport.created_at <= failed_before),
            ),
        )
        .order_by(TenantExport.created_at)
        .limit(limit)
        .with_for_update(skip_locked=True)
    )
    return list(session.scalars(stmt))


# --- ops.retention_settings -------------------------------------------------------------------


def get_retention(session: Session, *, lock: bool = False) -> RetentionSetting | None:
    stmt = select(RetentionSetting).limit(1)
    if lock:
        stmt = stmt.with_for_update()
    return session.execute(stmt).scalar_one_or_none()


def insert_retention(session: Session, **values: Any) -> RetentionSetting:
    return session.execute(
        insert(RetentionSetting).values(**values).returning(RetentionSetting)
    ).scalar_one()


def update_retention(
    session: Session, *, expected_version: int, rules: Mapping[str, int], updated_by: uuid.UUID
) -> RetentionSetting | None:
    stmt = (
        update(RetentionSetting)
        .where(RetentionSetting.version == expected_version)
        .values(rules=dict(rules), updated_by=updated_by, version=RetentionSetting.version + 1)
        .returning(RetentionSetting)
        .execution_options(synchronize_session=False, populate_existing=True)
    )
    return session.execute(stmt).scalar_one_or_none()


def retention_record_table(session: Session) -> RecordTable:
    return dump_table(session, RetentionSetting.__table__, name="retention_settings")
