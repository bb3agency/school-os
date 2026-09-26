"""Audit viewer routes (FR-AUD-005, US-1001; permission ``audit.read``)."""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.audit import viewer
from app.audit.viewer import AuditEventOut, AuditVerifyOut
from app.authz.context import UserContext
from app.authz.dependencies import TenantDB, require
from app.authz.http import Cursor, Limit, Page, decode_cursor, encode_cursor
from app.core.errors import ValidationFailed

router = APIRouter(prefix="/api/v1/audit", tags=["audit"])

AuditReader = Annotated[UserContext, Depends(require("audit.read"))]


@router.get("/events", response_model=Page[AuditEventOut])
def list_audit_events(
    *,
    ctx: AuditReader,
    db: TenantDB,
    limit: Limit = 50,
    cursor: Cursor = None,
    actor: Annotated[uuid.UUID | None, Query(description="Acting user id")] = None,
    resource_type: Annotated[str | None, Query(pattern=r"^[a-z][a-z0-9_]{0,63}$")] = None,
    resource_id: uuid.UUID | None = None,
    action: Annotated[str | None, Query(pattern=r"^[a-z_]+(\.[a-z_]+)+$", max_length=100)] = None,
    from_: Annotated[dt.datetime | None, Query(alias="from")] = None,
    to: dt.datetime | None = None,
) -> Page[AuditEventOut]:
    """School audit log, newest first (permission ``audit.read``).

    Filters: acting user, resource type/id, action, and a time range ``[from, to)``. Each event
    shows a summary of IDs, field names and codes only.
    """
    after = decode_cursor(cursor)
    before_seq = None
    if after is not None:
        seq = after.get("seq")
        if not isinstance(seq, int) or seq < 1:
            raise ValidationFailed(
                [{"field": "cursor", "code": "invalid", "message_key": "errors.invalid_cursor"}]
            )
        before_seq = seq
    page, next_seq = viewer.list_events(
        db,
        ctx.tenant_id,
        limit=limit,
        before_seq=before_seq,
        actor_id=actor,
        resource_type=resource_type,
        resource_id=resource_id,
        action=action,
        occurred_from=from_,
        occurred_to=to,
    )
    return Page[AuditEventOut](
        data=page, next_cursor=encode_cursor({"seq": next_seq}) if next_seq else None
    )


@router.get("/verify", response_model=AuditVerifyOut)
def verify_audit_chain(ctx: AuditReader, db: TenantDB) -> AuditVerifyOut:
    """Check that the school's audit chain is unbroken (permission ``audit.read``; US-1001 AC2)."""
    return viewer.verify(db, ctx.tenant_id)
