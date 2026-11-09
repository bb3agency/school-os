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
invited (``identity.INVITED_HOOKS``) and on request (:func:`resend_invitation_email`). The
school owner's invitation at provisioning (FR-PLT-002, D62) is queued the same way when the
control plane's school-chain event ``tenant.owner_invited`` is written into the school's chain
(``audit.on_platform_event``; exactly once, in that tenant transaction): the control plane never
reads the owner's address.
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
from app.audit.schemas import ActorType, SummaryError, sanitize_summary
from app.authz.context import UserContext
from app.authz.http import Page, decode_cursor, encode_cursor
from app.authz.kv import KVUnavailable, kv_store
from app.core import holds, retention
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
from app.notifications.subjects import erase_for_subject, subject_report
from app.ops import service as ops
from app.tenancy import service as tenancy

log = get_logger(__name__)
_USER_PAGE = 200

EMAIL_EVENT: Final = "notification.email.requested"
EMAIL_TASK: Final = "notifications.send_email"
INVITATION_TEMPLATE: Final = "invitation.staff"
OWNER_INVITED_EVENT: Final = "tenant.owner_invited"
"""School-chain copy of the control plane's owner invite (``platform.tenant_audit.OWNER_INVITED``;
tenant-side modules may not import ``app.platform``, a test keeps the two equal)."""
OWNER_INVITE_RESENT_EVENT: Final = "tenant.owner_invite_resent"
"""School-chain copy of an operator's owner-invite re-send (``platform.tenant_audit``)."""
_OWNER_ROLE: Final = "owner"
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


def holders(session: Session, permission: str) -> list[uuid.UUID]:
    """Membership IDs of the active, unexpired members whose roles grant ``permission`` (what a
    :class:`PermissionSelector` resolves to), for producers that first filter them by their own
    per-member choices (``app.brief``, FR-BRF-011)."""
    return _holders(session, permission)


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
    return len(
        _create(
            session,
            tenant_id=tenant_id,
            recipients=recipients,
            template_key=template_key,
            params=params,
            resource_id=resource_id,
            dedupe_key=dedupe_key,
        )
    )


def _create(
    session: Session,
    *,
    tenant_id: uuid.UUID,
    recipients: Recipients,
    template_key: str,
    params: Mapping[str, Any],
    resource_id: uuid.UUID | None,
    dedupe_key: str | None,
) -> list[tuple[uuid.UUID, uuid.UUID]]:
    """:func:`notify`; returns ``(recipient membership, notification id)`` per created row."""
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
    created = repo.insert_new(session, rows)
    log.info("notifications.created", action=template_key, count=len(created))
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


READ_RETENTION_CATEGORY: Final = "notifications_read"


def purge_read(session: Session, *, now: dt.datetime | None = None) -> int:
    """Delete this school's notifications read more than the retention period ago (the
    school's setting, FR-ADM-002, else ``retention.read_days`` in templates.yaml: 90 days)."""
    # Legal holds (ADR-0059): a hold on notifications keeps every read notification.
    if holds.check(
        session, job="notifications.purge_read", record_class="notifications"
    ).whole_class:
        return 0
    keep = retention.days(session, READ_RETENTION_CATEGORY, default=templates.read_retention_days())
    cutoff = (now or dt.datetime.now(dt.UTC)) - dt.timedelta(days=keep)
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
    actor_type: ActorType = "user",
    notification_id: uuid.UUID | None = None,
) -> bool:
    """Queue one email to a member of the current school, sent by the worker after commit.

    Returns False (nothing queued or audited) when email is off. The outbox payload and the
    audit event ``notification.email_requested`` carry IDs and codes only; the address, the
    language and the template values are read by the worker when it sends. ``via`` says what
    asked for it (a code, e.g. ``invite``, ``resend`` or ``owner_invite``). ``actor_type`` is
    ``system`` when no signed-in person asked (the owner invite at provisioning).
    ``notification_id`` names the recipient's in-app notice whose values (counts and codes) the
    worker reads for a 100 % alert email (D70).
    """
    if template_key not in email.email_catalog():
        raise templates.TemplateError(f"unknown email template {template_key!r}")
    if not get_settings().email_enabled:
        return False
    if repo.current_tenant(session) != tenant_id:
        raise RuntimeError("request_email() must run in the tenant_session of tenant_id")
    message_id = new_id()
    payload: dict[str, Any] = {
        "message_id": message_id,
        "user_id": user_id,
        "template_key": template_key,
    }
    summary: dict[str, Any] = {"message_id": message_id, "template_key": template_key, "via": via}
    if notification_id is not None:
        payload["notification_id"] = notification_id
        summary["notification_id"] = notification_id
    ops.enqueue_event(session, EMAIL_EVENT, payload)
    audit.record(
        session,
        action="notification.email_requested",
        resource_type="user",
        resource_id=user_id,
        summary=summary,
        actor_type=actor_type,
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


def queue_invitation_email(
    session: Session,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    *,
    via: str = "invite",
    actor_type: ActorType = "user",
) -> None:
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
        via=via,
        actor_type=actor_type,
    )


def queue_owner_invitation_email(
    session: Session,
    tenant_id: uuid.UUID,
    resource_id: uuid.UUID | None,
    _summary: Mapping[str, Any],
) -> None:
    """``audit.on_platform_event("tenant.owner_invited")`` (FR-PLT-002, D62): queue the school
    owner's invitation email in the tenant transaction that copies the control plane's owner
    invite into the school's chain (once per invite). ``resource_id`` is the owner's user ID."""
    if resource_id is None:
        log.warning("notifications.email.owner_invite_without_user", outcome="skipped")
        return
    queue_invitation_email(session, tenant_id, resource_id, via="owner_invite", actor_type="system")


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
            f"An invitation email was sent recently. Wait {minutes} minutes and try again.",
            retry_after_s=email.resend_cooldown_s(),
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


# --- 100 % alert emails (docs/16 §5.10, §19 Q16; FR-PLT-020, FR-PLT-021; D70) ---------------

LIMIT_EMAILS: Final = {
    "billing.ai_answers_used_up": "billing.ai_answers_quota",
    "billing.plan_limit_reached": "billing.plan_limit",
    # FA-05 (FR-BRF-012): the weekly brief e-mail of members who opted in, sent with the week's
    # in-app notice (counts only, a sign-in link).
    "brief.weekly": "brief.weekly_ready",
}
"""Email template -> the in-app notice template whose values it is sent with."""
LIMIT_EMAIL_VIA: Final = "limit_alert"


def notify_and_email(
    session: Session,
    *,
    tenant_id: uuid.UUID,
    recipients: Sequence[uuid.UUID],
    template_key: str,
    params: Mapping[str, Any],
    dedupe_key: str,
    email_template: str,
    resource_id: uuid.UUID | None = None,
) -> int:
    """:func:`notify` (with ``dedupe_key``), and queue ``email_template`` for each recipient whose
    notice is new, in the same transaction (D70). The notice's dedupe key therefore makes the
    email once per recipient and key: reruns create no notice and queue no email. Recipients
    without an active membership or an address get the notice only. A failure to queue an
    email is logged (codes only) and rolled back to a savepoint: the notice is kept. Returns how
    many notices were created."""
    if LIMIT_EMAILS.get(email_template) != template_key:
        raise templates.TemplateError(f"{email_template!r} is not sent with {template_key!r}")
    created = _create(
        session,
        tenant_id=tenant_id,
        recipients=recipients,
        template_key=template_key,
        params=params,
        resource_id=resource_id,
        dedupe_key=dedupe_key,
    )
    if created and get_settings().email_enabled:
        for member, notification_id in created:
            _queue_limit_email(session, tenant_id, member, notification_id, email_template)
    return len(created)


def _queue_limit_email(
    session: Session,
    tenant_id: uuid.UUID,
    membership_id: uuid.UUID,
    notification_id: uuid.UUID,
    email_template: str,
) -> None:
    try:
        with session.begin_nested():
            user_id = identity.user_id_for_membership(session, membership_id)
            user = identity.get_user(session, user_id) if user_id is not None else None
            problem = _limit_email_problem(user)
            if user is None or problem is not None:
                log.info("notifications.email.skipped", action=email_template, outcome=problem)
                return
            request_email(
                session,
                tenant_id=tenant_id,
                user_id=user.id,
                template_key=email_template,
                via=LIMIT_EMAIL_VIA,
                actor_type="system",
                notification_id=notification_id,
            )
    except Exception as exc:  # the email only; the in-app notice stays
        log.warning(
            "notifications.email.queue_failed",
            action=email_template,
            error_type=type(exc).__name__,
        )


def _limit_email_problem(user: UserOut | None) -> str | None:
    if user is None:
        return "no_member"
    if user.status != "active":
        return "not_active"
    if not user.email or user.contact_hidden:
        return "email_missing"
    return None


def _limit_email_params(
    session: Session, user: UserOut, template_key: str, notification_id: uuid.UUID | None
) -> tuple[dict[str, Any] | None, str]:
    """(values, "") for a 100 % alert email, else (None, outcome code)."""
    problem = _limit_email_problem(user)
    if problem is not None:
        return None, problem
    notice = (
        repo.get_for_recipient(session, user.membership_id, notification_id)
        if notification_id is not None
        else None
    )
    if notice is None or notice["template_key"] != LIMIT_EMAILS[template_key]:
        return None, "no_notification"
    values = dict(notice["params"])
    if template_key == "billing.ai_answers_used_up":
        return {"level": values["level"], "included": values["included"]}, ""
    if template_key == "brief.weekly":
        return {}, ""  # FR-BRF-010: no counts in the e-mail (school and link only)
    return {"metric": values["metric"]}, ""


def _app_url(path: str) -> str:
    base = (get_settings().email_app_url or "").rstrip("/")
    return f"{base}{path}"


def send_requested_email(
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    template_key: str,
    *,
    notification_id: uuid.UUID | None = None,
    sender: email.EmailSender | None = None,
    now: dt.datetime | None = None,
    event_id: uuid.UUID | None = None,
) -> str:
    """Worker (outbox ``notification.email.requested``): render and send one email.

    At most once per outbox event (audit YM-06): with ``event_id``, the event is claimed in its
    own committed transaction right before the provider call, and a later run of the same event
    answers ``already_sent`` without sending. A provider refusal to retry
    (:class:`email.EmailUnavailable`) releases the claim; an unknown outcome
    (:class:`email.EmailOutcomeUnknown`) and a permanent refusal keep it.

    Returns an outcome code (``sent``, ``disabled``, ``no_member``, ``not_invited``,
    ``invitation_expired``, ``email_missing``; for a 100 % alert email also ``not_active`` and
    ``no_notification``); raises :class:`email.EmailUnavailable` for the task to retry and
    :class:`email.EmailRejected` when the provider refuses for good. Only IDs and codes are
    logged.
    """
    if template_key != INVITATION_TEMPLATE and template_key not in LIMIT_EMAILS:
        raise templates.TemplateError(f"no email parameters for {template_key!r}")
    provider = sender or email.get_sender()
    if provider is None:
        return "disabled"
    moment = now or dt.datetime.now(dt.UTC)
    limit_values: dict[str, Any] | None = None
    with tenant_session(tenant_id) as session:
        try:
            user = identity.get_user(session, user_id)
        except NotFound:
            return "no_member"
        school = tenancy.get_tenant(session)
        if template_key in LIMIT_EMAILS:
            limit_values, skipped = _limit_email_params(
                session, user, template_key, notification_id
            )
            if limit_values is None:
                log.info("notifications.email.skipped", action=template_key, outcome=skipped)
                return skipped
    if limit_values is not None and user.email:
        content = email.render_email(
            template_key,
            {**limit_values, "school": school.name, "sign_in_url": _app_url("/")},
            user.preferred_language,
        )
        return _deliver(
            provider,
            user,
            template_key,
            user.email,
            content,
            tenant_id=tenant_id,
            event_id=event_id,
        )
    problem = _invitation_problem(user, moment)
    if problem is not None or not user.email:
        outcome = problem or "email_missing"
        log.info("notifications.email.skipped", action=template_key, outcome=outcome)
        return outcome
    expires = _invitation_expires(user).astimezone(IST)
    params: dict[str, Any] = {
        "school": school.name,
        # No URL carries a locale (ADR-0036 note, 2026-09-30): the web app's home, whose
        # language comes from the browser's cookie or Accept-Language, never the link.
        "sign_in_url": _app_url("/"),
        "expires_on": expires.strftime(_DATE_FORMATS[school.settings.date_format]),
    }
    content = email.render_email(template_key, params, user.preferred_language)
    return _deliver(
        provider, user, template_key, user.email, content, tenant_id=tenant_id, event_id=event_id
    )


def _deliver(
    provider: email.EmailSender,
    user: UserOut,
    template_key: str,
    address: str,
    content: email.EmailContent,
    *,
    tenant_id: uuid.UUID,
    event_id: uuid.UUID | None,
) -> str:
    if event_id is not None and ops.claim_event_consumer(tenant_id, event_id) == "consumed":
        log.info(
            "notifications.email.skipped",
            action=template_key,
            user_id=user.id,
            resource_id=event_id,
            outcome="already_sent",
        )
        return "already_sent"
    try:
        provider.send(
            email.EmailMessage(
                to=address,
                subject=content.subject,
                text=content.text,
                template_key=template_key,
                language=content.language,
            )
        )
    except email.EmailOutcomeUnknown:
        raise  # it may have gone out: the claim stays (at most once)
    except email.EmailUnavailable:
        if event_id is not None:
            ops.release_event_consumer(tenant_id, event_id)  # certainly not sent: retry may
        raise
    log.info("notifications.email.sent", action=template_key, user_id=user.id, outcome="sent")
    return "sent"


def _invited_owners(session: Session) -> list[UserOut]:
    out: list[UserOut] = []
    after: uuid.UUID | None = None
    while True:
        users, after = identity.list_users(session, limit=_USER_PAGE, after=after)
        out.extend(u for u in users if u.status == "invited" and _OWNER_ROLE in u.roles)
        if after is None:
            return out


def queue_owner_invitation_resend(
    session: Session,
    tenant_id: uuid.UUID,
    _resource_id: uuid.UUID | None,
    _summary: Mapping[str, Any],
) -> None:
    """``audit.on_platform_event("tenant.owner_invite_resent")`` (FR-PLT-002, D62, D65): an
    operator asked to send the owner's invitation again (in any status but offboarding; the
    control plane cannot see whether the owner accepted, so this decides). Emails this school's
    invited owner(s) unless one was sent within the staff invitation cool-down. Sends nothing,
    logging an outcome code only, when no owner is still invited (``owner_not_invited``: the
    owner accepted), an invitation expired or has no address, or within the cool-down."""
    if not get_settings().email_enabled:
        return
    moment = dt.datetime.now(dt.UTC)
    invited = _invited_owners(session)
    if not invited:
        log.info(
            "notifications.email.skipped", action=INVITATION_TEMPLATE, outcome="owner_not_invited"
        )
        return
    for user in invited:
        problem = _invitation_problem(user, moment)
        if problem is not None:
            log.info("notifications.email.skipped", action=INVITATION_TEMPLATE, outcome=problem)
            continue
        if not _cooldown(user.membership_id):
            log.info("notifications.email.skipped", action=INVITATION_TEMPLATE, outcome="cooldown")
            continue
        request_email(
            session,
            tenant_id=tenant_id,
            user_id=user.id,
            template_key=INVITATION_TEMPLATE,
            via="owner_resend",
            actor_type="system",
        )


def _register_hooks() -> None:
    if queue_invitation_email not in identity.INVITED_HOOKS:
        identity.INVITED_HOOKS.append(queue_invitation_email)
    audit.on_platform_event(OWNER_INVITED_EVENT, queue_owner_invitation_email)
    audit.on_platform_event(OWNER_INVITE_RESENT_EVENT, queue_owner_invitation_resend)


_register_hooks()


__all__ = [
    "EMAIL_EVENT",
    "EMAIL_TASK",
    "INVITATION_TEMPLATE",
    "LIMIT_EMAILS",
    "OWNER_INVITED_EVENT",
    "OWNER_INVITE_RESENT_EVENT",
    "READ_RETENTION_CATEGORY",
    "PermissionSelector",
    "Recipients",
    "SummaryError",
    "erase_for_subject",
    "holders",
    "list_for_me",
    "mark_all_read",
    "mark_read",
    "notify",
    "notify_and_email",
    "purge_read",
    "purge_tenant_data",
    "queue_invitation_email",
    "queue_owner_invitation_email",
    "queue_owner_invitation_resend",
    "request_email",
    "resend_invitation_email",
    "send_requested_email",
    "subject_report",
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
