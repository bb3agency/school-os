"""Audit viewer and CSV export routes (FR-AUD-005, US-1001; permission ``audit.read``)."""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse

from app.audit import export, viewer
from app.audit.viewer import AuditEventOut, AuditFilters, AuditVerifyOut
from app.authz.context import UserContext
from app.authz.dependencies import TenantDB, require
from app.authz.http import Cursor, Limit, Page, decode_cursor, encode_cursor
from app.core.errors import ValidationFailed
from app.core.logging import get_context
from app.identity.principal import Principal, get_principal, require_recent_auth

router = APIRouter(prefix="/api/v1/audit", tags=["audit"])

AuditReader = Annotated[UserContext, Depends(require("audit.read"))]
ActorFilter = Annotated[uuid.UUID | None, Query(description="Acting user id")]
ResourceTypeFilter = Annotated[str | None, Query(pattern=r"^[a-z][a-z0-9_]{0,63}$")]
ActionFilter = Annotated[str | None, Query(pattern=r"^[a-z_]+(\.[a-z_]+)+$", max_length=100)]
FromFilter = Annotated[dt.datetime | None, Query(alias="from")]


def recent_sign_in(principal: Annotated[Principal, Depends(get_principal)]) -> None:
    """Step-up for exports (docs/07 §5.2: creating any export needs MFA within 5 minutes;
    ``audit.read`` itself is not a step-up permission, so the viewer does not need it)."""
    require_recent_auth(principal)


@router.get("/events", response_model=Page[AuditEventOut])
def list_audit_events(
    *,
    ctx: AuditReader,
    db: TenantDB,
    limit: Limit = 50,
    cursor: Cursor = None,
    actor: ActorFilter = None,
    resource_type: ResourceTypeFilter = None,
    resource_id: uuid.UUID | None = None,
    action: ActionFilter = None,
    from_: FromFilter = None,
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


_CSV_DOC: dict[int | str, dict[str, Any]] = {
    200: {
        "description": "CSV file (UTF-8 with BOM), oldest event first",
        "content": {
            "text/csv": {
                "schema": {"type": "string"},
                "example": "seq,occurred_at_utc,occurred_at_ist,actor_type,actor_id,action,"
                "resource_type,resource_id,request_id,summary\r\n"
                "41,2026-09-01T04:30:00Z,2026-09-01 10:00:00,user,…,section.created,section,…,"
                'req_…,"{""fields"":[""name_en""]}"\r\n',
            }
        },
    }
}


@router.get("/export", response_class=StreamingResponse, responses=_CSV_DOC)
def export_audit_events(
    *,
    ctx: AuditReader,
    _step_up: Annotated[None, Depends(recent_sign_in)],
    db: TenantDB,
    actor: ActorFilter = None,
    resource_type: ResourceTypeFilter = None,
    resource_id: uuid.UUID | None = None,
    action: ActionFilter = None,
    from_: FromFilter = None,
    to: dt.datetime | None = None,
) -> StreamingResponse:
    """Download the school audit log as a CSV file (permission ``audit.read`` and a recent
    sign-in with MFA, 428 ``step_up_required``).

    Same filters as ``GET /audit/events``: acting user, resource type/id, action and a time
    range ``[from, to)``. Oldest event first; IDs, codes and summaries only, never names or
    contact details. At most 200,000 events per file (422 ``too_many_events``: choose a shorter
    date range). Every export is recorded in the audit log (``audit.exported``) before the file
    is sent.
    """
    filters = AuditFilters(
        actor_id=actor,
        resource_type=resource_type,
        resource_id=resource_id,
        action=action,
        occurred_from=from_,
        occurred_to=to,
    )
    request_id = get_context().get("request_id")
    plan = export.start(
        db,
        tenant_id=ctx.tenant_id,
        user_id=ctx.user_id,
        filters=filters,
        request_id=request_id if isinstance(request_id, str) else None,
    )
    return StreamingResponse(
        export.stream_csv(plan),
        media_type=export.MEDIA_TYPE,
        headers={
            "Content-Disposition": f'attachment; filename="{plan.filename}"',
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
            "X-Audit-Export-Rows": str(plan.rows),
        },
    )
