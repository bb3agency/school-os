"""Notification routes (docs/09 §4 Notifications; FR-NOT-001).

Every route needs only an active membership (``session.authenticated``) and works on the
caller's own notifications; another person's notification answers 404 (BOLA). Titles and bodies
are rendered in the language of the ``Accept-Language`` header (``te`` or ``en``, default en).
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query, Response

from app.authz.catalog import AUTHENTICATED
from app.authz.context import UserContext
from app.authz.dependencies import TenantDB, require
from app.authz.http import Cursor, Limit, Page
from app.notifications import service, templates
from app.notifications.schemas import MarkedReadOut, NotificationOut, UnreadCountOut

router = APIRouter(prefix="/api/v1/notifications", tags=["notifications"])

Member = Annotated[UserContext, Depends(require(AUTHENTICATED))]
AcceptLanguage = Annotated[str | None, Header(alias="Accept-Language", max_length=200)]


def _language(response: Response, accept_language: str | None) -> templates.Language:
    language = templates.negotiate_language(accept_language)
    response.headers["Content-Language"] = language
    response.headers["Vary"] = "Accept-Language"
    return language


@router.get("", response_model=Page[NotificationOut])
def list_notifications(
    ctx: Member,
    db: TenantDB,
    response: Response,
    *,
    accept_language: AcceptLanguage = None,
    limit: Limit = 50,
    cursor: Cursor = None,
    unread: Annotated[bool, Query(description="Only unread notifications.")] = False,
) -> Page[NotificationOut]:
    """Your notifications, newest first, in your language (English or Telugu)."""
    return service.list_for_me(
        db,
        ctx,
        language=_language(response, accept_language),
        limit=limit,
        cursor=cursor,
        unread_only=unread,
    )


@router.get("/unread-count", response_model=UnreadCountOut)
def unread_count(ctx: Member, db: TenantDB) -> UnreadCountOut:
    """How many of your notifications are unread (for the bell badge)."""
    return UnreadCountOut(count=service.unread_count(db, ctx))


@router.post("/read-all", response_model=MarkedReadOut)
def mark_all_read(ctx: Member, db: TenantDB) -> MarkedReadOut:
    """Mark all your notifications as read."""
    return MarkedReadOut(updated=service.mark_all_read(db, ctx))


@router.post("/{notification_id}/read", response_model=NotificationOut)
def mark_read(
    ctx: Member,
    db: TenantDB,
    notification_id: uuid.UUID,
    response: Response,
    accept_language: AcceptLanguage = None,
) -> NotificationOut:
    """Mark one of your notifications as read (repeating it changes nothing)."""
    return service.mark_read(
        db, ctx, notification_id, language=_language(response, accept_language)
    )
