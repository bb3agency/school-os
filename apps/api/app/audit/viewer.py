"""Read side of the school's audit log (FR-AUD-005, US-1001).

Returns summaries only (IDs, field names, counts, codes; never hashes or ip hashes). Runs in the
caller's ``tenant_session``; RLS already limits rows to the school and the explicit
``tenant_id`` filter keeps the per-tenant indexes usable.
"""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict
from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from app.audit.models import events


class AuditEventOut(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: uuid.UUID
    seq: int
    occurred_at: dt.datetime
    actor_type: str
    actor_id: uuid.UUID | None
    action: str
    resource_type: str
    resource_id: uuid.UUID | None
    summary: dict[str, Any]
    request_id: str | None


@dataclass(frozen=True, slots=True)
class AuditFilters:
    """Viewer and CSV export filters; the time range is ``[occurred_from, occurred_to)``."""

    actor_id: uuid.UUID | None = None
    resource_type: str | None = None
    resource_id: uuid.UUID | None = None
    action: str | None = None
    occurred_from: dt.datetime | None = None
    occurred_to: dt.datetime | None = None


def filtered(stmt: Select[Any], tenant_id: uuid.UUID, filters: AuditFilters) -> Select[Any]:
    """``stmt`` limited to the school (RLS does too; this keeps the indexes usable) and the
    filters."""
    stmt = stmt.where(events.c.tenant_id == tenant_id)
    if filters.actor_id is not None:
        stmt = stmt.where(events.c.actor_id == filters.actor_id)
    if filters.resource_type is not None:
        stmt = stmt.where(events.c.resource_type == filters.resource_type)
    if filters.resource_id is not None:
        stmt = stmt.where(events.c.resource_id == filters.resource_id)
    if filters.action is not None:
        stmt = stmt.where(events.c.action == filters.action)
    if filters.occurred_from is not None:
        stmt = stmt.where(events.c.occurred_at >= filters.occurred_from)
    if filters.occurred_to is not None:
        stmt = stmt.where(events.c.occurred_at < filters.occurred_to)
    return stmt


EVENT_COLUMNS = (
    events.c.id,
    events.c.seq,
    events.c.occurred_at,
    events.c.actor_type,
    events.c.actor_id,
    events.c.action,
    events.c.resource_type,
    events.c.resource_id,
    events.c.summary,
    events.c.request_id,
)


def list_events(
    session: Session,
    tenant_id: uuid.UUID,
    *,
    limit: int,
    before_seq: int | None = None,
    actor_id: uuid.UUID | None = None,
    resource_type: str | None = None,
    resource_id: uuid.UUID | None = None,
    action: str | None = None,
    occurred_from: dt.datetime | None = None,
    occurred_to: dt.datetime | None = None,
) -> tuple[list[AuditEventOut], int | None]:
    """Newest first; returns (page, seq to continue before) with ``limit`` ≤ 200."""
    filters = AuditFilters(
        actor_id=actor_id,
        resource_type=resource_type,
        resource_id=resource_id,
        action=action,
        occurred_from=occurred_from,
        occurred_to=occurred_to,
    )
    stmt = filtered(select(*EVENT_COLUMNS), tenant_id, filters)
    if before_seq is not None:
        stmt = stmt.where(events.c.seq < before_seq)
    rows = session.execute(stmt.order_by(events.c.seq.desc()).limit(limit + 1)).mappings().all()
    page = [AuditEventOut.model_validate(dict(r)) for r in rows[:limit]]
    return page, (page[-1].seq if len(rows) > limit and page else None)
