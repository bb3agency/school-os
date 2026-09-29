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

Email (``app.notifications.email``; off unless ``SOS_EMAIL_PROVIDER`` is set):
:func:`request_email` queues ``notification.email.requested`` (IDs only) in the caller's
transaction and audits ``notification.email_requested``; the worker task
``notifications.send_email`` (:func:`send_requested_email`) resolves the address, language and
template values and sends after commit. Staff invitation emails are queued when a person is
invited (``identity.INVITED_HOOKS``) and on request (:func:`resend_invitation_email`).
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.audit import service as audit
from app.audit.schemas import SummaryError, sanitize_summary
from app.authz.context import UserContext
from app.authz.http import Page, decode_cursor, encode_cursor
from app.authz.kv import KVUnavailable, kv_store
from app.core import purge as purging
from app.core.config import get_settings
from app.core.db import tenant_session
from app.core.errors import Conflict, NotFound, RateLimited, ValidationFailed
from app.core.ids import new_id
from app.core.logging import get_context, get_logger
from app.identity import service as identity
from app.identity.schemas import UserOut
from app.notifications import email, templates
from app.notifications import repository as repo
from app.notifications.schemas import InvitationEmailOut, NotificationOut
from app.ops import service as ops
from app.tenancy import service as tenancy

log = get_logger(__name__)
_USER_PAGE = 200

EMAIL_EVENT: Final = "notification.email.requested"
EMAIL_TASK: Final = "notifications.send_email"
INVITATION_TEMPLATE: Final = "invitation.staff"
ops.register_outbox_route(EMAIL_EVENT, EMAIL_TASK)
_DATE_FORMATS: Final = {
    "DD/MM/YYYY": "%d/%m/%Y",
    "DD-MM-YYYY": "%d-%m-%Y",
    "YYYY-MM-DD": "%Y-%m-%d",
}
IST: Final = ZoneInfo("Asia/Kolkata")


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


# --- email ----------------------------------------------------------------------------------


def _request_id() -> str | None:
    value = get_context().get("request_id")
    return value if isinstance(value, str) else None


def request_email(
    session: Session,
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    template_key: str,
    via: str,
) -> bool:
    """Queue one email to a member of the current school, sent by the worker after commit.

    Returns False (nothing queued or audited) when email is off. The outbox payload and the
    audit event ``notification.email_requested`` carry IDs and codes only; the address, the
    language and the template values are read by the worker when it sends. ``via`` says what
    asked for it (a code, e.g. ``invite`` or ``resend``).
    """
    if template_key not in email.email_catalog():
        raise templates.TemplateError(f"unknown email template {template_key!r}")
    if not get_settings().email_enabled:
        return False
    if repo.current_tenant(session) != tenant_id:
        raise RuntimeError("request_email() must run in the tenant_session of tenant_id")
    message_id = new_id()
    ops.enqueue_event(
        session,
        EMAIL_EVENT,
        {"message_id": message_id, "user_id": user_id, "template_key": template_key},
    )
    audit.record(
        session,
        action="notification.email_requested",
        resource_type="user",
        resource_id=user_id,
        summary={"message_id": message_id, "template_key": template_key, "via": via},
        request_id=_request_id(),
    )
    log.info("notifications.email.requested", action=template_key, resource_id=message_id)
    return True


def _cooldown(membership_id: uuid.UUID) -> bool:
    """True if an invitation email for this membership may be queued now (then starts the
    cool-down). Fails open when Valkey is unavailable: only ``user.manage`` holders can ask."""
    try:
        return kv_store().set(
            f"sos:rl:invitation-email:{membership_id}",
            b"1",
            ttl_s=email.resend_cooldown_s(),
            nx=True,
        )
    except KVUnavailable:
        log.warning("notifications.email.cooldown_unavailable")
        return True


def _invitation_expires(user: UserOut) -> dt.datetime:
    return user.created_at + dt.timedelta(days=email.invitation_valid_days())


def _invitation_problem(user: UserOut, now: dt.datetime) -> str | None:
    if user.status != "invited":
        return "not_invited"
    if _invitation_expires(user) <= now:
        return "invitation_expired"
    if not user.email:
        return "email_missing"
    return None


def queue_invitation_email(session: Session, tenant_id: uuid.UUID, user_id: uuid.UUID) -> None:
    """``identity.INVITED_HOOKS``: email a newly invited person who has an address."""
    if not get_settings().email_enabled:
        return
    user = identity.get_user(session, user_id)
    if _invitation_problem(user, dt.datetime.now(dt.UTC)) is not None:
        return
    _cooldown(user.membership_id)
    request_email(
        session,
        tenant_id=tenant_id,
        user_id=user_id,
        template_key=INVITATION_TEMPLATE,
        via="invite",
    )


_INVITATION_PROBLEMS: Final = {
    "not_invited": "This person has already accepted or is no longer invited.",
    "invitation_expired": "The invitation has expired. Remove the person and invite them again.",
    "email_missing": "Add an email address to this person's profile first.",
}


def resend_invitation_email(
    session: Session, ctx: UserContext, user_id: uuid.UUID
) -> InvitationEmailOut:
    """``POST /users/{user_id}/invitation-email`` (``user.manage``, step-up): send the staff
    invitation email again. 404 for anyone who is not a member of this school; 409
    ``email_disabled``, ``not_invited``, ``invitation_expired``, ``email_missing``; 429 within
    the cool-down after the last one."""
    user = identity.get_user(session, user_id)
    if not get_settings().email_enabled:
        raise Conflict("Email is not switched on for this school.", code="email_disabled")
    problem = _invitation_problem(user, dt.datetime.now(dt.UTC))
    if problem is not None:
        raise Conflict(_INVITATION_PROBLEMS[problem], code=problem)
    if not _cooldown(user.membership_id):
        minutes = max(1, email.resend_cooldown_s() // 60)
        raise RateLimited(
            f"An invitation email was sent recently. Wait {minutes} minutes and try again."
        )
    request_email(
        session,
        tenant_id=ctx.tenant_id,
        user_id=user.id,
        template_key=INVITATION_TEMPLATE,
        via="resend",
    )
    return InvitationEmailOut(
        user_id=user.id,
        membership_id=user.membership_id,
        status="queued",
        expires_at=_invitation_expires(user),
    )


def _app_url(path: str) -> str:
    base = (get_settings().email_app_url or "").rstrip("/")
    return f"{base}{path}"


def send_requested_email(
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    template_key: str,
    *,
    sender: email.EmailSender | None = None,
    now: dt.datetime | None = None,
) -> str:
    """Worker (outbox ``notification.email.requested``): render and send one email.

    Returns an outcome code (``sent``, ``disabled``, ``no_member``, ``not_invited``,
    ``invitation_expired``, ``email_missing``); raises :class:`email.EmailUnavailable` for the
    task to retry and :class:`email.EmailRejected` when the provider refuses for good. Only IDs
    and codes are logged.
    """
    provider = sender or email.get_sender()
    if provider is None:
        return "disabled"
    moment = now or dt.datetime.now(dt.UTC)
    with tenant_session(tenant_id) as session:
        try:
            user = identity.get_user(session, user_id)
        except NotFound:
            return "no_member"
        school = tenancy.get_tenant(session)
    if template_key != INVITATION_TEMPLATE:
        raise templates.TemplateError(f"no email parameters for {template_key!r}")
    problem = _invitation_problem(user, moment)
    if problem is not None or not user.email:
        outcome = problem or "email_missing"
        log.info("notifications.email.skipped", action=template_key, outcome=outcome)
        return outcome
    expires = _invitation_expires(user).astimezone(IST)
    params: dict[str, Any] = {
        "school": school.name,
        "sign_in_url": _app_url(f"/{user.preferred_language}"),
        "expires_on": expires.strftime(_DATE_FORMATS[school.settings.date_format]),
    }
    content = email.render_email(template_key, params, user.preferred_language)
    provider.send(
        email.EmailMessage(
            to=user.email,
            subject=content.subject,
            text=content.text,
            template_key=template_key,
            language=content.language,
        )
    )
    log.info("notifications.email.sent", action=template_key, user_id=user_id, outcome="sent")
    return "sent"


def _register_hooks() -> None:
    if queue_invitation_email not in identity.INVITED_HOOKS:
        identity.INVITED_HOOKS.append(queue_invitation_email)


_register_hooks()


__all__ = [
    "EMAIL_EVENT",
    "EMAIL_TASK",
    "INVITATION_TEMPLATE",
    "PermissionSelector",
    "Recipients",
    "SummaryError",
    "list_for_me",
    "mark_all_read",
    "mark_read",
    "notify",
    "purge_read",
    "purge_tenant_data",
    "queue_invitation_email",
    "request_email",
    "resend_invitation_email",
    "send_requested_email",
    "tenant_data_counts",
    "unread_count",
]


# --- offboarding purge (FR-PLT-005, ADR-0029) ------------------------------------------------
# In-app notifications.
# Registered with app.tenancy at import; the offboarding job counts them as sos_app and deletes
# them as sos_purger (children before parents) inside the school's tenant_session.
_PURGE = purging.PurgeTables(
    deleted=("ops.notifications",),
)


def tenant_data_counts(session: Session) -> dict[str, int]:
    """Rows of the current school in this module's tables (offboarding inventory)."""
    return _PURGE.count(session)


def purge_tenant_data(session: Session) -> dict[str, int]:
    """Delete the current school's rows of this module (offboarding only: the database allows it
    only as ``sos_purger`` for a school in ``offboarding``)."""
    return _PURGE.delete(session)


tenancy.register_data_owner(
    tenancy.TenantDataOwner(name="notifications", count=tenant_data_counts, purge=purge_tenant_data)
)
