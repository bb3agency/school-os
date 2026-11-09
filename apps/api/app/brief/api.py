"""Weekly management brief routes (docs/09 "Weekly brief"; FA-05; FR-BRF-001..FR-BRF-015).

Every route declares ``require(brief.read, scope="school")`` (owner and principal). The brief
holds counts only; each section is still shown only with the permission of the screen behind
it. Another school's stored week is 404 (RLS).
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response

from app.authz.context import UserContext
from app.authz.dependencies import TenantDB, require
from app.authz.http import OptionalIfMatch, etag
from app.brief import service
from app.brief.schemas import (
    BriefHistoryOut,
    BriefPreferencesIn,
    BriefPreferencesOut,
    WeeklyBriefOut,
)

router = APIRouter(prefix="/api/v1/brief", tags=["weekly-brief"])

Reader = Annotated[UserContext, Depends(require(service.READ, scope="school"))]


@router.get("/weekly", response_model=WeeklyBriefOut)
def latest_brief(ctx: Reader, db: TenantDB) -> WeeklyBriefOut:
    """The newest stored weekly brief (permission ``brief.read``). Before the first Monday
    job has run for the school, a live preview of the current week so far (``stored: false``).
    Counts only; student counts below ``small_cell_min`` are ``null``."""
    return service.latest(db, ctx)


@router.get("/weekly/history", response_model=BriefHistoryOut)
def brief_history(
    ctx: Reader,
    db: TenantDB,
    limit: Annotated[int | None, Query(ge=1, le=52)] = None,
) -> BriefHistoryOut:
    """The stored weeks, newest first (at most 52; permission ``brief.read``)."""
    return service.history(db, ctx, limit)


@router.get("/weekly/{brief_id}", response_model=WeeklyBriefOut)
def get_brief(ctx: Reader, db: TenantDB, brief_id: uuid.UUID) -> WeeklyBriefOut:
    """One stored week, compared with the week before it (permission ``brief.read``)."""
    return service.get(db, ctx, brief_id)


@router.get("/preferences", response_model=BriefPreferencesOut)
def get_preferences(ctx: Reader, db: TenantDB, response: Response) -> BriefPreferencesOut:
    """How you receive the weekly brief: in-app notice (on by default) and e-mail (off by
    default). ``ETag`` for ``If-Match``."""
    out = service.get_preferences(db, ctx)
    response.headers["ETag"] = etag(out.version)
    return out


@router.put("/preferences", response_model=BriefPreferencesOut)
def put_preferences(
    ctx: Reader,
    db: TenantDB,
    body: BriefPreferencesIn,
    version: OptionalIfMatch,
    response: Response,
) -> BriefPreferencesOut:
    """Change how you receive the weekly brief (your own choice only; audited). ``If-Match``
    (``W/"0"`` before the first save) is honoured when sent; stale: 412 ``version_mismatch``."""
    out = service.set_preferences(db, ctx, body, version)
    response.headers["ETag"] = etag(out.version)
    return out
