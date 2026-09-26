"""Notifications public API (C11, FR-NOT-001).

Producers (change requests, DQ, imports, documents, break-glass, ...) call :func:`notify` inside
their own ``tenant_session`` transaction, so a notification exists exactly when the event it
announces commits. Readers use :func:`list_for_me`, :func:`unread_count`, :func:`mark_read` and
:func:`mark_all_read`; each is limited to the caller's own membership (BOLA: another person's
notification answers 404, exactly like a random id).

``params`` hold IDs, counts and codes only (validated with the audit-summary sanitiser, which
rejects personal keys, long digit runs, emails and phone numbers) and must match the template's
declared params exactly. Rendering happens at read time in English or Telugu.

Recipients are membership IDs of the current school, or a :class:`PermissionSelector` resolved
through ``identity.service`` to the active, unexpired members whose roles grant the permission.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from app.audit.schemas import SummaryError, sanitize_summary
from app.authz.context import UserContext
from app.authz.http import Page, decode_cursor, encode_cursor
from app.core.errors import NotFound, ValidationFailed
from app.core.ids import new_id
from app.core.logging import get_logger
from app.identity import service as identity
from app.notifications import repository as repo
from app.notifications import templates
from app.notifications.schemas import NotificationOut

log = get_logger(__name__)
_USER_PAGE = 200


@dataclass(frozen=True, slots=True)
class PermissionSelector:
    """Every active member of the current school whose roles grant ``permission``."""

    permission: str


Recipients = Sequence[uuid.UUID] | PermissionSelector


def _holders(session: Session, permission: str) -> list[uuid.UUID]:
    role_keys = {r.key for r in identity.list_roles(session) if permission in r.permissions}
    if not role_keys:
        return []
    now = dt.datetime.now(dt.UTC)
    out: list[uuid.UUID] = []
    after: uuid.UUID | None = None
    while True:
        users, after = identity.list_users(session, limit=_USER_PAGE, after=after)
        out.extend(
            u.membership_id
            for u in users
            if u.status == "active"
            and (u.expires_at is None or u.expires_at > now)
            and role_keys.intersection(u.roles)
        )
        if after is None:
            return out


def _unique(ids: Iterable[uuid.UUID]) -> list[uuid.UUID]:
    seen: dict[uuid.UUID, None] = {}
    for i in ids:
        seen.setdefault(i, None)
    return list(seen)


def notify(
    session: Session,
    *,
    tenant_id: uuid.UUID,
    recipients: Recipients,
    template_key: str,
    params: Mapping[str, Any],
    resource_id: uuid.UUID | None = None,
    dedupe_key: str | None = None,
) -> int:
    """Create one notification per recipient in the caller's transaction; returns how many.

    ``tenant_id`` must be the session's school (checked). ``resource_id`` links the notification
    to the template's ``resource_type``. With ``dedupe_key``, a recipient who already has a
    notification with that key is skipped (safe to retry). Unknown templates, params that are
    not exactly the declared ones, and personal data in params raise ``TemplateError`` /
    ``SummaryError`` (programming errors, caught by tests).
    """
    template = templates.get(template_key)
    clean = sanitize_summary(params)
    templates.check_params(template, clean)
    if repo.current_tenant(session) != tenant_id:
        raise RuntimeError("notify() must run in the tenant_session of tenant_id")
    if resource_id is not None and template.resource_type is None:
        raise templates.TemplateError(f"{template_key} does not link to a resource")
    targets = (
        _holders(session, recipients.permission)
        if isinstance(recipients, PermissionSelector)
        else _unique(recipients)
    )
    rows = [
        {
            "id": new_id(),
            "tenant_id": tenant_id,
            "recipient_membership_id": member,
            "template_key": template_key,
            "params": clean,
            "resource_type": template.resource_type if resource_id is not None else None,
            "resource_id": resource_id,
            "dedupe_key": dedupe_key,
        }
        for member in targets
    ]
    created = repo.insert_many(session, rows)
    log.info("notifications.created", action=template_key, count=created)
    return created


def _out(row: Mapping[Any, Any], language: templates.Language) -> NotificationOut:
    message = templates.render(row["template_key"], row["params"], language)
    return NotificationOut(
        id=row["id"],
        template_key=row["template_key"],
        language=language,
        title=message.title,
        body=message.body,
        params=dict(row["params"]),
        resource_type=row["resource_type"],
        resource_id=row["resource_id"],
        created_at=row["created_at"],
        read_at=row["read_at"],
    )


def _bad_cursor() -> ValidationFailed:
    return ValidationFailed(
        [{"field": "cursor", "code": "invalid", "message_key": "errors.invalid_cursor"}]
    )


def _after(cursor: str | None) -> tuple[dt.datetime, uuid.UUID] | None:
    value = decode_cursor(cursor)
    if value is None:
        return None
    try:
        created = dt.datetime.fromisoformat(str(value["t"]))
        last = uuid.UUID(str(value["i"]))
    except (KeyError, ValueError) as exc:
        raise _bad_cursor() from exc
    if created.tzinfo is None:
        raise _bad_cursor()
    return created, last


def list_for_me(
    session: Session,
    ctx: UserContext,
    *,
    language: templates.Language,
    limit: int,
    cursor: str | None = None,
    unread_only: bool = False,
) -> Page[NotificationOut]:
    """The caller's notifications, newest first (cursor pagination)."""
    rows = repo.list_for_recipient(
        session, ctx.membership_id, limit=limit + 1, after=_after(cursor), unread_only=unread_only
    )
    page = rows[:limit]
    next_cursor = None
    if len(rows) > limit and page:
        last = page[-1]
        next_cursor = encode_cursor({"t": last["created_at"].isoformat(), "i": str(last["id"])})
    return Page[NotificationOut](data=[_out(r, language) for r in page], next_cursor=next_cursor)


def unread_count(session: Session, ctx: UserContext) -> int:
    return repo.count_unread(session, ctx.membership_id)


def mark_read(
    session: Session, ctx: UserContext, notification_id: uuid.UUID, *, language: templates.Language
) -> NotificationOut:
    """Mark one of the caller's notifications read; someone else's (or unknown) answers 404."""
    row = repo.mark_read(session, ctx.membership_id, notification_id)
    if row is None:
        raise NotFound("Notification not found")
    return _out(row, language)


def mark_all_read(session: Session, ctx: UserContext) -> int:
    return repo.mark_all_read(session, ctx.membership_id)


def purge_read(session: Session, *, now: dt.datetime | None = None) -> int:
    """Delete this school's notifications read more than the retention period ago (90 days)."""
    cutoff = (now or dt.datetime.now(dt.UTC)) - dt.timedelta(days=templates.read_retention_days())
    return repo.purge_read_before(session, cutoff)


__all__ = [
    "PermissionSelector",
    "Recipients",
    "SummaryError",
    "list_for_me",
    "mark_all_read",
    "mark_read",
    "notify",
    "purge_read",
    "unread_count",
]
