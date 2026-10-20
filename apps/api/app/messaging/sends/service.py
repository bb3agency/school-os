"""WhatsApp sending: broadcasts, automatic messages, the sender and the reports (wave R2-3G;
ADR-0043 decisions 3, 10, 11; ADR-0046; owner decisions D25, D27, D31; US-2204..US-2208;
FR-MSG-011..017, FR-MSG-022).

- **Notice broadcasts (FR-MSG-012, US-2204).** An approved M4 notice, sections of the current
  year. The preview counts recipients, guardians left out by reason, languages and the estimated
  Meta cost (``config.yaml`` rate x count x (1 + GST); D27) and returns a preview token; the
  confirmation (``Idempotency-Key``) recomputes the audience and refuses with 409
  ``preview_stale`` when the counts moved, then queues one message per guardian (a guardian with
  two children in the audience gets one). A notice goes to a guardian once; "Send again" needs a
  reason and uses its own dedupe key. No separate approval: the notice itself was approved by a
  ``notice.approve`` holder (maker-checker of M4, FR-NOTICE-004/005); the broadcast is audited.
- **Automatic messages.** Absence alerts (FR-MSG-013) at the school's time from
  ``academics.service.absent_students`` (``absent`` only; one per student, guardian and day; no
  correction messages), fee reminders (FR-MSG-014) at 10:00 IST from
  ``fees.service.reminder_dues`` (one per account, instalment and offset) and receipt messages
  (FR-MSG-015) from the ``fee.receipt.message`` outbox event. Each needs its switch, an opt-in in
  its category, and not a no-message date; a run happens once per school and IST day
  (``msg.send_runs``).
- **The sender (FR-MSG-011, FR-MSG-016).** Every minute and when woken, per active school: the
  purpose switch, quiet hours (21:00-07:00 IST; absence alerts and receipts until 20:00), the
  daily cap (Meta's limit minus 10%, D25) and staleness are checked; a message that may not go
  now waits (``next_attempt_at``) instead of being dropped. The parent link (ADR-0046; target
  types per the R2-3H contract) is issued just before the send and its token goes only into the
  URL button. Transient errors retry after 5 and 20 minutes (3 attempts within an hour);
  permanent ones are final. **Nothing is sent while a school is suspended** (D31): the sweep
  only visits active schools, the woken task is held by ``ops.TenantTask`` and the sender checks
  the school's status again.
- **Manual sends (R2-3J).** Fee reminders chosen on the dues list (FR-MSG-025: preview with a
  stateless token that pins who gets one, confirm with ``Idempotency-Key``, one per account and
  guardian and IST day, dedupe key ``manual:<date>``) and "Send on WhatsApp" for one receipt
  (FR-MSG-026: the receipt message path, dedupe key ``receipt``, so it is never sent twice).
  The automatic switches (fee reminders, fee receipts) govern the automatic messages only; a
  fee message a person queued (``created_by``) is not skipped as ``purpose_off``. Quiet hours,
  the cap, the account pause and D31 apply to every message.
- **Notice withdrawal (R2-3J, FR-NOTICE-009, FR-PUB-007).** The outbox event
  ``notice.withdrawn`` cancels the notice's queued messages (``skipped`` / ``notice_withdrawn``)
  and revokes its parent links again (circulars revokes them in the withdrawing transaction);
  the sender also skips a notice message whose notice is no longer approved.
- **Logs and audit (invariant 5, FR-MSG-022).** IDs, codes and counts only; phone numbers stay
  in ``sis.guardians`` and are decrypted only by ``messaging.service.deliver`` in the worker.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import uuid
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Final, Literal

from sqlalchemy.orm import Session

from app.academics import service as academics
from app.authz.context import UserContext
from app.authz.http import decode_cursor, encode_cursor
from app.circulars import service as circulars
from app.core.db import tenant_session
from app.core.errors import Conflict, Forbidden, NotFound, ValidationFailed
from app.core.ids import new_id
from app.core.languages import Language, contains_telugu, parent_language
from app.core.logging import get_logger
from app.fees import service as fees
from app.messaging import repository as mrepo
from app.messaging import service as messaging
from app.messaging.config import load_config
from app.messaging.models import Message, MessagingSettings
from app.messaging.sends import repository as repo
from app.messaging.sends import rules
from app.messaging.sends.models import Broadcast
from app.messaging.sends.schemas import (
    AutomaticOut,
    BroadcastConfirmIn,
    BroadcastOut,
    BroadcastPage,
    BroadcastPreviewIn,
    BroadcastPreviewOut,
    BroadcastReportOut,
    FeeReminderPreviewOut,
    FeeRemindersConfirmIn,
    FeeReminderSelectionIn,
    FeeRemindersOut,
    ReceiptMessageOut,
    ReceiptWhatsAppOut,
    RevokeLinkOut,
    SectionCountsOut,
    SendRunOut,
)
from app.ops import service as ops
from app.public import service as public
from app.students import service as students
from app.tenancy import service as tenancy

SEND: Final = "message.send"
READ: Final = "message.read"
SEND_EVENT: Final = "messaging.send"
SEND_TASK: Final = "messaging.send_due"
RECEIPT_TASK: Final = "messaging.receipt_message"
NOTICE_WITHDRAWN_TASK: Final = "messaging.notice_withdrawn"
FEE_COLLECT: Final = "fee.collect"
REMINDER_TOKEN_PREFIX: Final = "fr_"  # noqa: S105 - a preview digest prefix, not a secret
# Purposes whose switch is about the automatic messages only (R2-3J): a person may still send
# one by hand (FR-MSG-025, FR-MSG-026). Notices are always sent by hand and keep their switch.
MANUAL_ALLOWED: Final = frozenset({"fee_reminder", "fee_receipt"})
TOKEN_PREFIX: Final = "bp_"  # noqa: S105 - a preview id prefix, not a secret
SENDING_PURPOSES: Final = frozenset(
    {"school_notice", "absence_alert", "fee_reminder", "fee_receipt"}
)
# Parent link kind per purpose (ADR-0046). The target type is the R2-3H pages' contract
# (app.public.config.PARENT_TARGET_TYPES, applied by public.service.issue_parent_link); the
# message's source is the link's target (notice, fee account, receipt).
LINKS: Final[dict[str, Literal["parent_notice", "parent_dues", "parent_receipt"]]] = {
    "school_notice": "parent_notice",
    "fee_reminder": "parent_dues",
    "fee_receipt": "parent_receipt",
}
_SWITCH: Final = {
    "school_notice": "notices_enabled",
    "absence_alert": "absence_alerts_enabled",
    "fee_reminder": "fee_reminders_enabled",
    "fee_receipt": "fee_receipts_enabled",
}

ops.register_outbox_route(SEND_EVENT, SEND_TASK)
ops.register_outbox_route(fees.receipt_message_event(), RECEIPT_TASK)
ops.register_outbox_route(circulars.notice_withdrawn_event(), NOTICE_WITHDRAWN_TASK)

log = get_logger(__name__)


class PreviewInvalid(Conflict):
    code = "preview_invalid"


class PreviewStale(Conflict):
    code = "preview_stale"


class SendingBlocked(Conflict):
    """409 with the blocking reason as its code (messaging_paused, template_not_approved, ...)."""

    def __init__(self, reason: str) -> None:
        super().__init__(_BLOCK_TEXT.get(reason, "WhatsApp messages cannot be sent now."))
        self.code = reason


_BLOCK_TEXT: Final = {
    "messaging_not_connected": "Connect the school's WhatsApp number first.",
    "messaging_paused": "Sending is paused. Resume it in WhatsApp settings first.",
    "messaging_currency_not_inr": "Set INR billing in Meta's Billing Hub first.",
    "template_not_approved": "Meta has not approved this message yet.",
    "template_paused": "This message is paused. Check WhatsApp settings.",
    "notices_off": "Turn on notices in WhatsApp settings first.",
    "receipt_cancelled": "This receipt is cancelled. Cancelled receipts are not sent.",
    "no_opted_in_guardian": "No guardian of this student agreed to get fee messages.",
    "already_queued": "This receipt was already sent to every guardian who agreed.",
}


def _error(field_name: str, code: str) -> dict[str, str]:
    return {"field": field_name, "code": code, "message_key": f"errors.{code}"}


def _settings(session: Session) -> MessagingSettings | None:
    return mrepo.get_settings(session)


def _switch_on(row: MessagingSettings | None, purpose: str) -> bool:
    attr = _SWITCH.get(purpose)
    if attr is None:
        return True
    return bool(row is not None and getattr(row, attr))


# --- audience (broadcast preview and confirm) ---------------------------------------------------


@dataclass
class _Plan:
    guardians: int = 0
    recipients: list[tuple[uuid.UUID, uuid.UUID, Language, bool]] = field(default_factory=list)
    skipped: Counter[str] = field(default_factory=Counter)
    languages: Counter[str] = field(default_factory=Counter)
    fallbacks: int = 0
    already_sent: int = 0


def _sections(
    session: Session, section_ids: Sequence[uuid.UUID]
) -> tuple[uuid.UUID, list[uuid.UUID]]:
    year = tenancy.get_current_academic_year(session)
    if year is None:
        raise ValidationFailed([_error("section_ids", "no_current_year")])
    wanted = list(dict.fromkeys(section_ids))
    for section_id in wanted:
        try:
            section = tenancy.get_section(session, section_id)
        except NotFound:
            raise ValidationFailed([_error("section_ids", "not_found")]) from None
        if section.academic_year_id != year.id:
            raise ValidationFailed([_error("section_ids", "not_current_year")])
    return year.id, wanted


def _notice(session: Session, notice_id: uuid.UUID) -> circulars.NoticeForSending:
    notice = circulars.notice_for_sending(session, notice_id)
    if notice is None:
        raise NotFound("Notice not found")
    if not notice.approved:
        raise Conflict("Approve the notice before sending it.", code="notice_not_approved")
    return notice


def _telugu_title(notice: circulars.NoticeForSending) -> str | None:
    """The notice's Telugu title when it is really Telugu (FR-LANG-004); while Telugu is hidden
    the approved notice repeats the English text there, which never goes out as Telugu."""
    title = notice.title_te.strip()
    return title if title and contains_telugu(title) else None


def _plan(
    session: Session,
    notice: circulars.NoticeForSending,
    section_ids: Sequence[uuid.UUID],
    *,
    resend: bool,
) -> _Plan:
    year_id, wanted = _sections(session, section_ids)
    enrolled = students.enrolled_in_sections(session, academic_year_id=year_id, section_ids=wanted)
    student_ids = [e.student_id for e in enrolled]
    order = {sid: i for i, sid in enumerate(student_ids)}
    first_child: dict[uuid.UUID, Any] = {}
    for brief in sorted(
        students.guardians_of_students(session, student_ids),
        key=lambda b: (order.get(b.student_id, 0), not b.is_primary),
    ):
        first_child.setdefault(brief.guardian_id, brief)
    plan = _Plan(guardians=len(first_child))
    guardian_ids = list(first_child)
    current = mrepo.current_optins(session, guardian_ids)
    already = (
        set() if resend else repo.guardians_sent_notice(session, notice.notice_id, guardian_ids)
    )
    school_language = tenancy.parent_language(session)
    te_ok = messaging.telugu_usable(session, "school_notice") and _telugu_title(notice) is not None
    for guardian_id, brief in first_child.items():
        row = current.get((guardian_id, "notices"))
        reason: str | None = None
        if guardian_id in already:
            reason = "already_sent"
        elif row is None:
            reason = "no_optin"
        elif row.status != "opted_in":
            reason = "opted_out"
        elif not brief.has_phone:
            reason = "no_mobile_number"
        elif not messaging.optin_for_current_number(session, guardian_id, row):
            reason = "optin_for_other_number"
        if reason is not None:
            plan.skipped[reason] += 1
            if reason == "already_sent":
                plan.already_sent += 1
            continue
        language: Language = parent_language(school_language, brief.preferred_language)
        fallback = False
        if language == "te" and not te_ok:
            language, fallback = "en", True
        plan.languages[language] += 1
        plan.fallbacks += int(fallback)
        plan.recipients.append((guardian_id, brief.student_id, language, fallback))
    return plan


def _blocked(session: Session) -> str | None:
    reason = messaging.account_block(session, "school_notice")
    if reason is not None:
        return reason
    if not _switch_on(_settings(session), "school_notice"):
        return "notices_off"
    return None


def _cost(count: int) -> str:
    cfg = load_config().cost
    return str(rules.estimate_cost(count, cfg.utility_rate_inr, cfg.gst_percent))


def preview_broadcast(
    session: Session, ctx: UserContext, notice_id: uuid.UUID, data: BroadcastPreviewIn
) -> BroadcastPreviewOut:
    """Recipients, guardians left out by reason, languages and the estimated Meta cost of
    sending an approved notice to the chosen sections (US-2204 AC1; ``message.send``). Errors:
    404 notice; 409 ``notice_not_approved``; 422 ``section_ids``. Nothing is sent."""
    messaging.ensure_available()
    if len(set(data.section_ids)) > load_config().broadcasts.max_sections:
        raise ValidationFailed([_error("section_ids", "too_many")])
    notice = _notice(session, notice_id)
    plan = _plan(session, notice, data.section_ids, resend=data.resend)
    now = mrepo.now(session)
    expires = now + dt.timedelta(minutes=load_config().broadcasts.preview_minutes)
    sections = list(dict.fromkeys(data.section_ids))
    row = repo.insert_broadcast(
        session,
        id=new_id(),
        notice_id=notice.notice_id,
        section_ids=sections,
        resend=data.resend,
        recipients=len(plan.recipients),
        skipped=dict(plan.skipped),
        languages=dict(plan.languages),
        language_fallbacks=plan.fallbacks,
        already_sent=plan.already_sent,
        estimated_cost_inr=_cost(len(plan.recipients)),
        requested_by=ctx.user_id,
        expires_at=expires,
    )
    cfg = load_config().cost
    return BroadcastPreviewOut(
        preview_token=TOKEN_PREFIX + row.id.hex,
        notice_id=notice.notice_id,
        section_ids=sections,
        guardians=plan.guardians,
        recipients=len(plan.recipients),
        skipped=dict(plan.skipped),
        languages=dict(plan.languages),
        language_fallbacks=plan.fallbacks,
        already_sent=plan.already_sent,
        estimated_cost_inr=_cost(len(plan.recipients)),
        utility_rate_inr=str(cfg.utility_rate_inr),
        gst_percent=str(cfg.gst_percent),
        blocked_reason=_blocked(session),
        expires_at=expires,
    )


def _preview_row(session: Session, ctx: UserContext, notice_id: uuid.UUID, token: str) -> Broadcast:
    if not token.startswith(TOKEN_PREFIX):
        raise PreviewInvalid("Preview again before sending.")
    try:
        broadcast_id = uuid.UUID(hex=token[len(TOKEN_PREFIX) :])
    except ValueError:
        raise PreviewInvalid("Preview again before sending.") from None
    row = repo.get_broadcast(session, broadcast_id, lock=True)
    if (
        row is None
        or row.notice_id != notice_id
        or row.status != "previewed"
        or row.requested_by != ctx.user_id
    ):
        raise PreviewInvalid("Preview again before sending.")
    if row.expires_at <= mrepo.now(session):
        raise Conflict("The preview has expired. Preview again.", code="preview_expired")
    return row


def confirm_broadcast(
    session: Session, ctx: UserContext, notice_id: uuid.UUID, data: BroadcastConfirmIn
) -> BroadcastOut:
    """Queue the previewed broadcast (US-2204 AC2; ``message.send``, ``Idempotency-Key``): one
    message per recipient, sent outside quiet hours by the sender. Errors: 409
    ``preview_invalid``, ``preview_expired``, ``preview_stale`` (the audience changed: preview
    again), ``notice_not_approved`` and the blocking reasons (``messaging_paused``,
    ``template_not_approved``, ``messaging_currency_not_inr``, ``messaging_not_connected``,
    ``notices_off``); 422 ``resend_reason`` for "Send again". Audit
    ``messaging.broadcast.sent`` (notice, counts) and ``messaging.message.queued`` per message."""
    messaging.ensure_available()
    notice = _notice(session, notice_id)
    row = _preview_row(session, ctx, notice_id, data.preview_token)
    if set(data.section_ids) != set(row.section_ids):
        raise PreviewInvalid("Preview again before sending.")
    if row.resend and data.resend_reason is None:
        raise ValidationFailed([_error("resend_reason", "required")])
    if not row.resend and data.resend_reason is not None:
        raise ValidationFailed([_error("resend_reason", "not_allowed")])
    blocked = _blocked(session)
    if blocked is not None:
        raise SendingBlocked(blocked)
    plan = _plan(session, notice, row.section_ids, resend=row.resend)
    if len(plan.recipients) != row.recipients or dict(plan.skipped) != dict(row.skipped or {}):
        raise PreviewStale("Who would get this notice has changed. Preview again.")
    school = tenancy.school_display_name(session)
    by_language: dict[str, dict[str, str]] = {
        "en": {"school_name": school, "notice_title": notice.title_en}
    }
    te_title = _telugu_title(notice)
    if te_title is not None:
        by_language["te"] = {"school_name": school, "notice_title": te_title}
    dedupe = f"resend:{row.id.hex}" if row.resend else "notice"
    queued = 0
    for guardian_id, student_id, _, _ in plan.recipients:
        out = messaging.queue_message(
            session,
            purpose="school_notice",
            guardian_id=guardian_id,
            student_id=student_id,
            source_type="parent_notice",
            source_id=notice.notice_id,
            dedupe_key=dedupe,
            variables_by_language=by_language,
            created_by=ctx.user_id,
            broadcast_id=row.id,
        )
        queued += int(out is not None)
    now = mrepo.now(session)
    repo.update_broadcast(
        session,
        row.id,
        {
            "status": "sending" if queued else "done",
            "confirmed_by": ctx.user_id,
            "confirmed_at": now,
            "completed_at": None if queued else now,
            "queued": queued,
            "resend_reason": data.resend_reason,
        },
    )
    messaging.record_audit(
        session,
        action="messaging.broadcast.sent",
        resource_type="parent_notice",
        resource_id=notice.notice_id,
        summary={
            "broadcast_id": row.id,
            "sections": len(row.section_ids),
            "recipients": len(plan.recipients),
            "queued": queued,
            "skipped": dict(plan.skipped),
            "languages": dict(plan.languages),
            "resend": row.resend,
        },
    )
    if queued:
        ops.enqueue_event(session, SEND_EVENT, {"broadcast_id": str(row.id)})
    fresh = repo.get_broadcast(session, row.id)
    if fresh is None:  # pragma: no cover - updated in this transaction
        raise NotFound("Broadcast not found")
    return _broadcast_out(session, fresh, {fresh.id: {"queued": queued}}, {})


# --- reports ------------------------------------------------------------------------------------


def _broadcast_out(
    session: Session,
    row: Broadcast,
    counts: Mapping[uuid.UUID, Mapping[str, int]],
    titles: Mapping[uuid.UUID, str],
) -> BroadcastOut:
    title = titles.get(row.notice_id)
    if title is None:
        notice = circulars.notice_for_sending(session, row.notice_id)
        title = notice.title_en if notice is not None else None
    return BroadcastOut(
        id=row.id,
        notice_id=row.notice_id,
        notice_title=title,
        status=row.status,
        section_ids=list(row.section_ids),
        resend=row.resend,
        recipients=row.recipients,
        skipped={str(k): int(v) for k, v in (row.skipped or {}).items()},
        languages={str(k): int(v) for k, v in (row.languages or {}).items()},
        language_fallbacks=row.language_fallbacks,
        estimated_cost_inr=f"{row.estimated_cost_inr:.2f}",
        queued=row.queued,
        counts=dict(counts.get(row.id, {})),
        requested_by=row.requested_by,
        confirmed_at=row.confirmed_at,
        completed_at=row.completed_at,
        created_at=row.created_at,
    )


def _reach(session: Session, ctx: UserContext) -> set[uuid.UUID] | None:
    """``None`` for a school-wide ``message.read`` holder; otherwise the students a scoped reader
    (class teacher) reaches, whose messages alone are counted for them (FR-MSG-017)."""
    if ctx.scope_for(READ).school_wide:
        return None
    return set(students.list_students_in_scope(session, ctx, permissions=(READ,)))


def list_broadcasts(
    session: Session,
    ctx: UserContext,
    *,
    notice_id: uuid.UUID | None,
    limit: int,
    cursor: str | None,
) -> BroadcastPage:
    """Confirmed broadcasts, newest first, with live counts by status (FR-MSG-017;
    ``message.read``; a class teacher's counts cover the students of their sections only)."""
    messaging.ensure_available()
    if notice_id is not None and circulars.notice_for_sending(session, notice_id) is None:
        raise NotFound("Notice not found")
    reach = _reach(session, ctx)
    after = None
    decoded = decode_cursor(cursor)
    if decoded is not None:
        try:
            after = (dt.datetime.fromisoformat(str(decoded["at"])), uuid.UUID(str(decoded["id"])))
        except (KeyError, ValueError):
            raise ValidationFailed([_error("cursor", "invalid")]) from None
    rows = repo.list_broadcasts(session, notice_id=notice_id, after=after, limit=limit + 1)
    more = len(rows) > limit
    rows = rows[:limit]
    counts = repo.status_counts(session, [r.id for r in rows], student_ids=reach)
    titles: dict[uuid.UUID, str] = {}
    for notice in {r.notice_id for r in rows}:
        found = circulars.notice_for_sending(session, notice)
        if found is not None:
            titles[notice] = found.title_en
    next_cursor = (
        encode_cursor({"at": rows[-1].created_at.isoformat(), "id": str(rows[-1].id)})
        if more and rows
        else None
    )
    return BroadcastPage(
        items=[_broadcast_out(session, r, counts, titles) for r in rows], next_cursor=next_cursor
    )


def _section_labels(session: Session) -> dict[uuid.UUID, str]:
    year = tenancy.get_current_academic_year(session)
    classes = {c.id: c for c in tenancy.list_classes(session)}
    labels: dict[uuid.UUID, str] = {}
    for section in tenancy.list_sections(session, academic_year_id=year.id if year else None):
        klass = classes.get(section.class_id)
        labels[section.id] = f"{klass.code if klass else '?'}-{section.name}"
    return labels


def broadcast_report(
    session: Session, ctx: UserContext, broadcast_id: uuid.UUID
) -> BroadcastReportOut:
    """One broadcast's delivery report: counts by status, by class and section, and failure
    and skip reasons, live (FR-MSG-017, US-2204 AC2; ``message.read``; a class teacher sees the
    students of their sections only). Counts only, no phone numbers or names."""
    messaging.ensure_available()
    row = repo.get_broadcast(session, broadcast_id)
    if row is None or row.status == "previewed":
        raise NotFound("Broadcast not found")
    reach = _reach(session, ctx)
    messages = [
        m
        for m in repo.broadcast_messages(session, row.id)
        if reach is None or m.student_id in reach
    ]
    placements = students.current_placements(
        session, [m.student_id for m in messages if m.student_id is not None]
    )
    labels = _section_labels(session)
    per: dict[uuid.UUID | None, Counter[str]] = defaultdict(Counter)
    reasons: Counter[str] = Counter()
    totals: Counter[str] = Counter()
    for m in messages:
        section = placements.get(m.student_id) if m.student_id is not None else None
        per[section][m.status] += 1
        totals[m.status] += 1
        if m.status == "failed":
            reasons[messaging.error_reason(m.error_code) or "provider_error"] += 1
        elif m.status == "skipped" and m.skip_reason:
            reasons[m.skip_reason] += 1
    sections = sorted(
        (
            SectionCountsOut(
                section_id=sid, label=labels.get(sid) if sid else None, counts=dict(counts)
            )
            for sid, counts in per.items()
        ),
        key=lambda s: (s.label is None, s.label or ""),
    )
    return BroadcastReportOut(
        broadcast=_broadcast_out(session, row, {row.id: dict(totals)}, {}),
        sections=sections,
        failure_reasons=dict(reasons),
    )


def revoke_link(session: Session, ctx: UserContext, message_id: uuid.UUID) -> RevokeLinkOut:
    """Stop the parent link a message carried from working (ADR-0046 decision 5, FR-PUB-007;
    ``message.send``). 404 for an unknown message or one without a link. Audited
    ``public.link.revoked`` (reason ``manual``) by the public module."""
    del ctx
    messaging.ensure_available()
    message = mrepo.get_message(session, message_id)
    if message is None or message.public_handle_id is None:
        raise NotFound("Message not found")
    revoked = public.revoke(session, message.public_handle_id, reason="manual")
    return RevokeLinkOut(message_id=message_id, revoked=revoked)


def automatic(session: Session, ctx: UserContext) -> AutomaticOut:
    """The automatic messages and their recent runs (US-2205, US-2207; ``message.read``):
    switches, times, no-message dates, how many messages wait, and per run the messages it
    queued by status now. Counts of the whole school only (no names or numbers)."""
    del ctx
    messaging.ensure_available()
    row = _settings(session)
    runs = []
    for run in repo.recent_runs(session, 14):
        purpose = "absence_alert" if run.job == "absence_alerts" else "fee_reminder"
        runs.append(
            SendRunOut(
                job=run.job,
                run_on=run.run_on,
                started_at=run.started_at,
                finished_at=run.finished_at,
                considered=run.considered,
                queued=run.queued,
                counts=repo.messages_of_run(session, purpose, run.run_on),
            )
        )
    return AutomaticOut(
        absence_alerts_enabled=bool(row and row.absence_alerts_enabled),
        absence_alert_time=row.absence_alert_time if row else dt.time(11, 0),
        fee_reminders_enabled=bool(row and row.fee_reminders_enabled),
        fee_reminder_time=load_config().automatic.fee_reminder_time,
        fee_receipts_enabled=bool(row and row.fee_receipts_enabled),
        no_message_dates=sorted(row.no_message_dates or []) if row else [],
        queued_now=repo.queued_count(session),
        runs=runs,
    )


# --- automatic messages (worker) ----------------------------------------------------------------


def _active(session: Session) -> bool:
    """D31: nothing is sent or generated for a school that is not active."""
    return tenancy.get_tenant(session).status == "active"


def _optins(session: Session, guardian_ids: Sequence[uuid.UUID], category: str) -> set[uuid.UUID]:
    current = mrepo.current_optins(session, guardian_ids)
    return {
        g for g in guardian_ids if (row := current.get((g, category))) and row.status == "opted_in"
    }


def _start_run(
    session: Session, job: str, now: dt.datetime, at: dt.time, purpose: str
) -> uuid.UUID | None:
    """The day's run id when ``job`` may run now (switch on, time reached, window open, not a
    no-message date, not run yet today)."""
    row = _settings(session)
    if row is None or not _switch_on(row, purpose) or not _active(session):
        return None
    today = rules.ist_today(now)
    if today in set(row.no_message_dates or []) or not rules.job_due(now, at):
        return None
    cfg = load_config().sending
    window = rules.next_open(
        now,
        purpose=purpose,
        quiet_start=row.quiet_start,
        quiet_end=row.quiet_end,
        early_close=cfg.early_close,
        early_purposes=cfg.early_close_purposes,
    )
    if window is not None and rules.ist_today(window) != today:
        return None  # too late in the day: tomorrow's run takes tomorrow's marks
    run_id = new_id()
    return run_id if repo.claim_run(session, job, today, run_id) else None


def _finish_run(
    session: Session, run_id: uuid.UUID, job: str, *, considered: int, queued: int
) -> None:
    repo.finish_run(session, run_id, considered=considered, queued=queued)
    messaging.record_audit(
        session,
        action="messaging.run.completed",
        resource_type="messaging_run",
        resource_id=run_id,
        summary={"job": job, "considered": considered, "queued": queued},
        system=True,
    )
    if queued:
        ops.enqueue_event(session, SEND_EVENT, {"run_id": str(run_id)})


def run_absence_alerts(tenant_id: uuid.UUID, now: dt.datetime) -> dict[str, int]:
    """Absence alerts for today's ``absent`` marks (FR-MSG-013, US-2207): one per student and
    opted-in guardian, with the child's first name and the date only. Runs once per IST day at
    the school's time (default 11:00, off by default, D25)."""
    with tenant_session(tenant_id) as session:
        row = _settings(session)
        at = row.absence_alert_time if row is not None else dt.time(11, 0)
        run_id = _start_run(session, "absence_alerts", now, at, "absence_alert")
        if run_id is None:
            return {"ran": 0}
        today = rules.ist_today(now)
        absent = academics.absent_students(session, today)
        briefs = students.guardians_of_students(session, absent)
        opted = _optins(session, sorted({b.guardian_id for b in briefs}), "attendance")
        names = students.first_names(session, absent)
        school = tenancy.school_display_name(session)
        queued = 0
        for brief in briefs:
            if brief.guardian_id not in opted or brief.student_id not in names:
                continue
            out = messaging.queue_message(
                session,
                purpose="absence_alert",
                guardian_id=brief.guardian_id,
                student_id=brief.student_id,
                source_type="student",
                source_id=brief.student_id,
                dedupe_key=today.isoformat(),
                variables={
                    "school_name": school,
                    "child_first_name": names[brief.student_id],
                    "date": rules.parent_date(today),
                },
            )
            queued += int(out is not None)
        _finish_run(session, run_id, "absence_alerts", considered=len(absent), queued=queued)
    return {"ran": 1, "queued": queued}


def run_fee_reminders(tenant_id: uuid.UUID, now: dt.datetime) -> dict[str, int]:
    """Fee reminders at 10:00 IST (FR-MSG-014, US-2205): for each instalment due on one of the
    school's offsets with an amount at least the minimum, one message per account, instalment
    and offset to each guardian opted in for fees, with a link to the dues page (its "Pay online"
    is the parent page's, R2-3H)."""
    with tenant_session(tenant_id) as session:
        at = load_config().automatic.fee_reminder_time
        run_id = _start_run(session, "fee_reminders", now, at, "fee_reminder")
        if run_id is None:
            return {"ran": 0}
        dues = fees.reminder_dues(session, rules.ist_today(now))
        student_ids = sorted({d.student_id for d in dues})
        briefs = students.guardians_of_students(session, student_ids)
        by_student: dict[uuid.UUID, list[uuid.UUID]] = defaultdict(list)
        for brief in briefs:
            by_student[brief.student_id].append(brief.guardian_id)
        opted = _optins(session, sorted({b.guardian_id for b in briefs}), "fees")
        names = students.first_names(session, student_ids)
        school = tenancy.school_display_name(session)
        queued = 0
        for due in dues:
            if due.student_id not in names:
                continue
            for guardian_id in by_student.get(due.student_id, []):
                if guardian_id not in opted:
                    continue
                out = messaging.queue_message(
                    session,
                    purpose="fee_reminder",
                    guardian_id=guardian_id,
                    student_id=due.student_id,
                    source_type="fee_account",
                    source_id=due.account_id,
                    dedupe_key=f"i{due.instalment_no}:o{due.offset_days}",
                    variables={
                        "school_name": school,
                        "amount": rules.parent_amount(due.amount),
                        "child_first_name": names[due.student_id],
                        "due_date": rules.parent_date(due.due_on),
                    },
                )
                queued += int(out is not None)
        _finish_run(session, run_id, "fee_reminders", considered=len(dues), queued=queued)
    return {"ran": 1, "queued": queued}


def _queue_receipt(
    session: Session,
    receipt: fees.ReceiptForMessage,
    guardian_ids: Sequence[uuid.UUID],
    *,
    created_by: uuid.UUID | None,
) -> int:
    """Queue the receipt message to ``guardian_ids`` (FR-MSG-015; dedupe key ``receipt``: once
    per receipt and guardian, whoever asks). Returns how many were new."""
    name = students.first_names(session, [receipt.student_id]).get(receipt.student_id)
    if name is None:
        return 0
    school = tenancy.school_display_name(session)
    queued = 0
    for guardian_id in guardian_ids:
        out = messaging.queue_message(
            session,
            purpose="fee_receipt",
            guardian_id=guardian_id,
            student_id=receipt.student_id,
            source_type="fee_receipt",
            source_id=receipt.receipt_id,
            dedupe_key="receipt",
            variables={
                "school_name": school,
                "amount": rules.parent_amount(receipt.amount),
                "child_first_name": name,
                "receipt_number": receipt.receipt_number,
                "date": rules.parent_date(receipt.receipt_date),
            },
            created_by=created_by,
        )
        queued += int(out is not None)
    return queued


def _receipt_guardians(session: Session, receipt: fees.ReceiptForMessage) -> list[uuid.UUID]:
    """The student's guardians opted in for fee messages."""
    briefs = students.guardians_of_students(session, [receipt.student_id])
    return sorted(_optins(session, sorted({b.guardian_id for b in briefs}), "fees"))


def queue_receipt_message(tenant_id: uuid.UUID, receipt_id: uuid.UUID) -> dict[str, int]:
    """The receipt message (FR-MSG-015, US-2206) to each guardian of the student opted in for
    fees: receipt number, amount, date and a link to the receipt page. Idempotent per receipt
    and guardian. Nothing while receipts are switched off or for a cancelled receipt."""
    with tenant_session(tenant_id) as session:
        if not messaging.available() or not _active(session):
            return {"queued": 0}
        if not _switch_on(_settings(session), "fee_receipt"):
            return {"queued": 0}
        receipt = fees.receipt_for_message(session, receipt_id)
        if receipt is None or receipt.cancelled:
            return {"queued": 0}
        queued = _queue_receipt(
            session, receipt, _receipt_guardians(session, receipt), created_by=None
        )
    if queued:
        send_due(tenant_id)
    return {"queued": queued}


# --- "Send on WhatsApp" for one receipt (R2-3J; FR-MSG-026) --------------------------------------


def _receipt_caller(ctx: UserContext) -> None:
    """The counter (``fee.collect``) or a sender (``message.send``), school-wide (fee
    permissions are never section-scoped, FR-FEE-024)."""
    for permission in (FEE_COLLECT, SEND):
        if ctx.has(permission) and ctx.scope_for(permission).school_wide:
            return
    raise Forbidden()


def _receipt(session: Session, receipt_id: uuid.UUID) -> fees.ReceiptForMessage:
    receipt = fees.receipt_for_message(session, receipt_id)
    if receipt is None:
        raise NotFound("Receipt not found")
    return receipt


@dataclass(frozen=True, slots=True)
class _ReceiptState:
    reason: str | None
    pending: list[uuid.UUID]
    opted: int
    messages: list[Message]


def _receipt_state(session: Session, receipt: fees.ReceiptForMessage) -> _ReceiptState:
    """Why the receipt cannot be sent now (or ``None``), the opted-in guardians who have no
    receipt message yet, and the receipt's messages."""
    messages = repo.source_messages(session, "fee_receipt", receipt.receipt_id)
    have = {m.guardian_id for m in messages}
    opted = _receipt_guardians(session, receipt)
    pending = [g for g in opted if g not in have]
    reason = messaging.account_block(session, "fee_receipt")
    if reason is None and receipt.cancelled:
        reason = "receipt_cancelled"
    if reason is None and not opted:
        reason = "no_opted_in_guardian"
    if reason is None and not pending:
        reason = "already_queued"
    return _ReceiptState(reason=reason, pending=pending, opted=len(opted), messages=messages)


def _receipt_out(receipt: fees.ReceiptForMessage, state: _ReceiptState) -> ReceiptWhatsAppOut:
    return ReceiptWhatsAppOut(
        receipt_id=receipt.receipt_id,
        can_send=state.reason is None,
        blocked_reason=state.reason,
        opted_in_guardians=state.opted,
        messages=[
            ReceiptMessageOut(
                id=m.id,
                status=m.status,
                skip_reason=m.skip_reason,
                failure_reason=(
                    (messaging.error_reason(m.error_code) or "provider_error")
                    if m.status == "failed"
                    else None
                ),
                language=m.language,
                queued_at=m.queued_at,
                status_at=m.status_at,
            )
            for m in state.messages
        ],
    )


def receipt_whatsapp(
    session: Session, ctx: UserContext, receipt_id: uuid.UUID
) -> ReceiptWhatsAppOut:
    """Whether the receipt can be sent on WhatsApp now and its messages' status (FR-MSG-026;
    ``fee.collect`` or ``message.send``, school-wide). 404 for an unknown receipt, another
    school's, or while fees are off."""
    _receipt_caller(ctx)
    messaging.ensure_available()
    receipt = _receipt(session, receipt_id)
    return _receipt_out(receipt, _receipt_state(session, receipt))


def send_receipt_whatsapp(
    session: Session, ctx: UserContext, receipt_id: uuid.UUID
) -> ReceiptWhatsAppOut:
    """Queue the receipt message to the guardians opted in for fee messages who have none yet
    (FR-MSG-026, US-2206; ``fee.collect`` or ``message.send``; ``Idempotency-Key``). The same
    path as the automatic receipt message (dedupe key ``receipt``: never twice to a guardian);
    sent outside quiet hours (until 20:00 IST). Errors: 404; 409 with the reason as its code
    (``messaging_not_connected``, ``messaging_paused``, ``template_not_approved``,
    ``template_paused``, ``messaging_currency_not_inr``, ``receipt_cancelled``,
    ``no_opted_in_guardian``, ``already_queued``). Audit ``messaging.receipt.sent``."""
    _receipt_caller(ctx)
    messaging.ensure_available()
    receipt = _receipt(session, receipt_id)
    state = _receipt_state(session, receipt)
    if state.reason is not None:
        raise SendingBlocked(state.reason)
    queued = _queue_receipt(session, receipt, state.pending, created_by=ctx.user_id)
    messaging.record_audit(
        session,
        action="messaging.receipt.sent",
        resource_type="fee_receipt",
        resource_id=receipt.receipt_id,
        summary={"queued": queued, "guardians": len(state.pending)},
    )
    if queued:
        ops.enqueue_event(session, SEND_EVENT, {"receipt_id": str(receipt.receipt_id)})
    return _receipt_out(receipt, _receipt_state(session, receipt))


# --- manual fee reminders from the dues list (R2-3J; FR-MSG-025) ---------------------------------


@dataclass
class _ReminderPlan:
    day: dt.date
    accounts: int = 0
    items: list[tuple[fees.ReminderAccount, uuid.UUID]] = field(default_factory=list)
    skipped_accounts: Counter[str] = field(default_factory=Counter)
    skipped_guardians: Counter[str] = field(default_factory=Counter)

    def token(self) -> str:
        """A digest of who gets a reminder, for how much, and who is left out (the preview the
        person saw); the confirmation recomputes it."""
        parts = [self.day.isoformat()]
        parts += sorted(f"{a.account_id}:{g}:{a.overdue}" for a, g in self.items)
        parts += [f"a.{k}={v}" for k, v in sorted(self.skipped_accounts.items())]
        parts += [f"g.{k}={v}" for k, v in sorted(self.skipped_guardians.items())]
        digest = hashlib.sha256("|".join(parts).encode()).hexdigest()
        return REMINDER_TOKEN_PREFIX + digest[:40]

    def counts(self) -> dict[str, Any]:
        return {
            "accounts": self.accounts,
            "recipients": len(self.items),
            "skipped_accounts": dict(self.skipped_accounts),
            "skipped_guardians": dict(self.skipped_guardians),
        }


def _reminder_dedupe(day: dt.date) -> str:
    return f"manual:{day.isoformat()}"


def _guardian_reason(
    session: Session, guardian_id: uuid.UUID, has_phone: bool, optin: Any
) -> str | None:
    if optin is None:
        return "no_optin"
    if optin.status != "opted_in":
        return "opted_out"
    if not has_phone:
        return "no_mobile_number"
    if not messaging.optin_for_current_number(session, guardian_id, optin):
        return "optin_for_other_number"
    return None


def _reminder_plan(
    session: Session, ctx: UserContext, data: FeeReminderSelectionIn
) -> _ReminderPlan:
    picked = fees.reminder_accounts(
        session,
        ctx,
        account_ids=data.account_ids,
        section_id=data.section_id,
        class_id=data.class_id,
    )
    if len(picked) > load_config().broadcasts.max_reminder_accounts:
        raise ValidationFailed([_error("account_ids", "too_many")])
    plan = _ReminderPlan(day=messaging.ist_today(session), accounts=len(picked))
    due: list[fees.ReminderAccount] = []
    for account in picked:
        if account.overdue <= 0 or account.oldest_due_on is None:
            plan.skipped_accounts["nothing_due"] += 1
        elif account.overdue < account.minimum:
            plan.skipped_accounts["below_minimum"] += 1
        else:
            due.append(account)
    student_ids = sorted({a.student_id for a in due})
    briefs = students.guardians_of_students(session, student_ids)
    by_student: dict[uuid.UUID, dict[uuid.UUID, bool]] = defaultdict(dict)
    for brief in briefs:
        by_student[brief.student_id][brief.guardian_id] = brief.has_phone
    names = students.first_names(session, student_ids)
    current = mrepo.current_optins(session, sorted({b.guardian_id for b in briefs}))
    already = repo.reminded(session, [a.account_id for a in due], _reminder_dedupe(plan.day))
    for account in due:
        guardians = by_student.get(account.student_id, {})
        if not guardians or account.student_id not in names:
            plan.skipped_accounts["no_guardian"] += 1
            continue
        for guardian_id, has_phone in sorted(guardians.items()):
            reason = (
                "already_sent"
                if (account.account_id, guardian_id) in already
                else _guardian_reason(
                    session, guardian_id, has_phone, current.get((guardian_id, "fees"))
                )
            )
            if reason is not None:
                plan.skipped_guardians[reason] += 1
                continue
            plan.items.append((account, guardian_id))
    return plan


def preview_fee_reminders(
    session: Session, ctx: UserContext, data: FeeReminderSelectionIn
) -> FeeReminderPreviewOut:
    """Who would get a fee reminder for the chosen dues-list rows (or all matching its filter),
    who is left out and why, and the estimated Meta cost (FR-MSG-025; ``message.send`` and
    ``finance.read``, school-wide). Nothing is sent or stored. Errors: 404 an account that is
    not one of this year's; 422 ``account_ids`` (``too_many``) or the selection."""
    messaging.ensure_available()
    plan = _reminder_plan(session, ctx, data)
    cfg = load_config().cost
    return FeeReminderPreviewOut(
        preview_token=plan.token(),
        accounts_reminded=len({a.account_id for a, _ in plan.items}),
        guardians=len({g for _, g in plan.items}),
        estimated_cost_inr=_cost(len(plan.items)),
        utility_rate_inr=str(cfg.utility_rate_inr),
        gst_percent=str(cfg.gst_percent),
        blocked_reason=messaging.account_block(session, "fee_reminder"),
        **plan.counts(),
    )


def confirm_fee_reminders(
    session: Session, ctx: UserContext, data: FeeRemindersConfirmIn
) -> FeeRemindersOut:
    """Queue the previewed fee reminders (FR-MSG-025; ``message.send`` and ``finance.read``;
    ``Idempotency-Key``): one per account and opted-in guardian, at most once a day each
    (dedupe key ``manual:<IST date>``), with the overdue amount, the oldest due date and a link
    to the dues page; sent outside quiet hours and within the daily cap. Errors: 409
    ``preview_stale`` (who gets one changed: preview again) and the blocking reasons
    (``messaging_not_connected``, ``messaging_paused``, ``template_not_approved``,
    ``template_paused``, ``messaging_currency_not_inr``). Audit ``messaging.fee_reminders.sent``
    (counts) and ``messaging.message.queued`` per message."""
    messaging.ensure_available()
    plan = _reminder_plan(session, ctx, data)  # checks finance.read first (403)
    blocked = messaging.account_block(session, "fee_reminder")
    if blocked is not None:
        raise SendingBlocked(blocked)
    if plan.token() != data.preview_token:
        raise PreviewStale("Who would get a reminder has changed. Preview again.")
    dedupe = _reminder_dedupe(plan.day)
    names = students.first_names(session, sorted({a.student_id for a, _ in plan.items}))
    school = tenancy.school_display_name(session)
    queued = 0
    for account, guardian_id in plan.items:
        due_on = account.oldest_due_on or plan.day  # never None here (nothing_due otherwise)
        out = messaging.queue_message(
            session,
            purpose="fee_reminder",
            guardian_id=guardian_id,
            student_id=account.student_id,
            source_type="fee_account",
            source_id=account.account_id,
            dedupe_key=dedupe,
            variables={
                "school_name": school,
                "amount": rules.parent_amount(account.overdue),
                "child_first_name": names[account.student_id],
                "due_date": rules.parent_date(due_on),
            },
            created_by=ctx.user_id,
        )
        queued += int(out is not None)
    counts = plan.counts()
    messaging.record_audit(
        session,
        action="messaging.fee_reminders.sent",
        resource_type="fee_reminders",
        resource_id=None,
        summary={
            **counts,
            "queued": queued,
            "all_matching": data.all_matching,
            "section_id": data.section_id,
            "class_id": data.class_id,
        },
    )
    if queued:
        ops.enqueue_event(session, SEND_EVENT, {"fee_reminders": queued})
    return FeeRemindersOut(queued=queued, **counts)


# --- notice withdrawal (R2-3J; FR-NOTICE-009, FR-PUB-007) ----------------------------------------


def cancel_notice_messages(tenant_id: uuid.UUID, notice_id: uuid.UUID) -> dict[str, int]:
    """Consumer of ``notice.withdrawn``: the notice's queued messages are skipped
    (``notice_withdrawn``) and its open parent links revoked (again: a link issued while the
    notice was being withdrawn). A message the sender is handing to Meta at this moment (under
    its claim lease) is left to the sender; its link is revoked here all the same. Audit
    ``messaging.notice_messages.cancelled`` (counts)."""
    with tenant_session(tenant_id) as session:
        now = mrepo.now(session)
        lease = now + dt.timedelta(seconds=load_config().sending.claim_seconds)
        cancelled = 0
        for message_id in repo.queued_source_message_ids(session, "school_notice", notice_id):
            message = repo.get_message(session, message_id, lock=True)
            if message is None or message.status != "queued":
                continue
            if message.next_attempt_at is not None and now < message.next_attempt_at <= lease:
                continue  # being sent now (the sender re-checks the notice before a new try)
            _skip(session, message, "notice_withdrawn")
            cancelled += 1
        revoked = public.revoke_for_target(
            session, "parent_notice", notice_id, reason="notice_withdrawn", by_user=False
        )
        _finish_broadcasts(session)
        if cancelled or revoked:
            messaging.record_audit(
                session,
                action="messaging.notice_messages.cancelled",
                resource_type="parent_notice",
                resource_id=notice_id,
                summary={"cancelled": cancelled, "links_revoked": revoked},
                system=True,
            )
    return {"cancelled": cancelled, "revoked": revoked}


# --- the sender (worker) ------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Claim:
    key: str | None
    handle_id: uuid.UUID | None


def _cap(session: Session, row: MessagingSettings | None) -> int | None:
    if row is not None and row.daily_cap is not None:
        return row.daily_cap
    account = mrepo.live_account(session)
    return load_config().default_daily_cap(account.messaging_limit if account else None)


def _revoke(session: Session, handle_id: uuid.UUID | None, reason: str) -> None:
    if handle_id is not None:
        public.revoke(session, handle_id, reason=reason, by_user=False)


def _skip(session: Session, message: Message, reason: str) -> str:
    repo.set_message(session, message.id, {"status": "skipped", "skip_reason": reason})
    _revoke(session, message.public_handle_id, "message_skipped")
    return "skipped"


def _notice_live(session: Session, notice_id: uuid.UUID) -> bool:
    notice = circulars.notice_for_sending(session, notice_id)
    return notice is not None and notice.approved


def _claim(session: Session, message_id: uuid.UUID, now: dt.datetime) -> _Claim | str:  # noqa: PLR0911 - one return per outcome
    """Check the rules for one queued message and claim it, or say why it waits / was
    skipped (``deferred``, ``skipped``, ``busy``)."""
    cfg = load_config().sending
    message = repo.get_message(session, message_id, lock=True)
    if message is None or message.status != "queued":
        return "busy"
    if message.next_attempt_at is not None and message.next_attempt_at > now:
        return "busy"
    if not _active(session):
        return "held"
    settings = _settings(session)
    manual = message.created_by is not None and message.purpose in MANUAL_ALLOWED
    if not manual and not _switch_on(settings, message.purpose):
        return _skip(session, message, "purpose_off")
    if message.purpose == "school_notice" and not _notice_live(session, message.source_id):
        return _skip(session, message, "notice_withdrawn")
    if rules.too_late(message.purpose, message.queued_at, now, max_queue_hours=cfg.max_queue_hours):
        return _skip(session, message, "too_late")
    quiet_start = settings.quiet_start if settings else dt.time(21, 0)
    quiet_end = settings.quiet_end if settings else dt.time(7, 0)
    opens = rules.next_open(
        now,
        purpose=message.purpose,
        quiet_start=quiet_start,
        quiet_end=quiet_end,
        early_close=cfg.early_close,
        early_purposes=cfg.early_close_purposes,
    )
    if opens is not None:
        repo.set_message(session, message.id, {"next_attempt_at": opens})
        return "deferred"
    if rules.cap_reached(
        repo.sent_since(session, rules.ist_midnight(now)), _cap(session, settings)
    ):
        repo.set_message(
            session, message.id, {"next_attempt_at": rules.next_morning(now, quiet_end)}
        )
        return "deferred"
    values: dict[str, Any] = {"next_attempt_at": now + dt.timedelta(seconds=cfg.claim_seconds)}
    key: str | None = None
    handle_id: uuid.UUID | None = None
    kind = LINKS.get(message.purpose)
    if kind is not None and message.guardian_id is not None:
        # One token per guardian per message (FR-PUB-005); the earlier try's token was never
        # delivered, so it is revoked and a new one issued.
        _revoke(session, message.public_handle_id, "send_retry")
        issued = public.issue_parent_link(
            session,
            kind=kind,
            target_id=message.source_id,
            guardian_id=message.guardian_id,
            message_id=message.id,
        )
        key, handle_id = issued.key, issued.handle_id
        values["public_handle_id"] = handle_id
    repo.set_message(session, message.id, values)
    return _Claim(key=key, handle_id=handle_id)


def _send_one(tenant_id: uuid.UUID, message_id: uuid.UUID, now: dt.datetime) -> str:
    with tenant_session(tenant_id) as session:
        claim = _claim(session, message_id, now)
    if not isinstance(claim, _Claim):
        return claim
    status = messaging.deliver(tenant_id, message_id, url_suffix=claim.key)
    if status == "sent":
        return status
    with tenant_session(tenant_id) as session:
        message = repo.get_message(session, message_id)
        if message is None:
            return status
        if status == "queued":  # transient error: try again after the backoff
            backoff = load_config().sending.backoff(message.attempts)
            repo.set_message(session, message_id, {"next_attempt_at": mrepo.now(session) + backoff})
            _revoke(session, claim.handle_id, "send_retry")
            return "retry"
        _revoke(session, claim.handle_id, "send_failed")
    return status


def _finish_broadcasts(session: Session) -> None:
    for row in repo.broadcasts_sending(session):
        counts = repo.status_counts(session, [row.id]).get(row.id, {})
        if not counts.get("queued"):
            repo.update_broadcast(
                session, row.id, {"status": "done", "completed_at": mrepo.now(session)}
            )


def send_due(tenant_id: uuid.UUID, *, now: dt.datetime | None = None) -> dict[str, int]:
    """Send the school's queued messages whose time has come, at most ``batch_per_run`` per run
    (FR-MSG-011, FR-MSG-016). Outcome counts only."""
    if not messaging.available():
        return {}
    cfg = load_config().sending
    with tenant_session(tenant_id) as session:
        if not _active(session):
            return {"held": 1}
        moment = now or mrepo.now(session)
        ids = repo.due_message_ids(session, moment, cfg.batch_per_run, SENDING_PURPOSES)
    totals: Counter[str] = Counter()
    for message_id in ids:
        totals[_send_one(tenant_id, message_id, moment)] += 1
    with tenant_session(tenant_id) as session:
        _finish_broadcasts(session)
    if ids:
        log.info(
            "messaging.sender.run",
            tenant_id=tenant_id,
            outcome=",".join(f"{k}:{v}" for k, v in sorted(totals.items()))[:200],
        )
    return dict(totals)


def purge_expired(tenant_id: uuid.UUID) -> dict[str, int]:
    """Daily retention: unconfirmed previews after a day, broadcasts whose messages are gone
    and run rows after 13 months (docs/05 §13)."""
    cfg = load_config()
    with tenant_session(tenant_id) as session:
        now = mrepo.now(session)
        previews = repo.purge_previews(
            session, now - dt.timedelta(hours=cfg.broadcasts.preview_retention_hours)
        )
        old = now - dt.timedelta(days=cfg.retention.messages_days)
        broadcasts = repo.purge_broadcasts(session, old)
        runs = repo.purge_runs(session, rules.ist_today(old))
    return {"previews": previews, "broadcasts": broadcasts, "runs": runs}


__all__ = [
    "LINKS",
    "NOTICE_WITHDRAWN_TASK",
    "READ",
    "RECEIPT_TASK",
    "SEND",
    "SEND_EVENT",
    "SEND_TASK",
    "automatic",
    "broadcast_report",
    "cancel_notice_messages",
    "confirm_broadcast",
    "confirm_fee_reminders",
    "list_broadcasts",
    "preview_broadcast",
    "preview_fee_reminders",
    "purge_expired",
    "queue_receipt_message",
    "receipt_whatsapp",
    "revoke_link",
    "run_absence_alerts",
    "run_fee_reminders",
    "send_due",
    "send_receipt_whatsapp",
]
