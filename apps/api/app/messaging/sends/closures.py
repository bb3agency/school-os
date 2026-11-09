"""Urgent closure message (FP-02; FR-MSG-047..055; ADR-0043 amendment of 2026-11-09, lead
decision D85, PO to confirm; docs/09 §4 "Messaging").

District Collectors close schools by evening orders (cyclones, heavy rain); a notice approved
at 22:00 would reach parents at 07:00, when buses may already be running. This is the one
urgent path:

- **Who.** Holders of ``message.closure.send`` only (the principal; critical, step-up MFA for
  the send). The preview needs the permission without step-up. No second approver: the
  principal already holds ``notice.approve``; the send is audited ``messaging.closure.sent``.
- **What.** Only the approved UTILITY template ``school_closure`` ("... the school will be closed
  on <dates> because of <reason> ..."): dates and a reason chosen from ``config.yaml``
  ``closures.reasons`` (no free text reaches parents). Guardians opted in for ``notices`` only.
- **When.** Between ``closures.window_start`` and ``window_end`` IST (06:00-21:30 by default),
  also inside the school's quiet hours; never outside that window: a later message waits for
  the next ``window_start`` (``rules.closure_next_open``). A closure message is skipped
  (``too_late``) once its last closure date has passed.
- **Limits that still apply.** The daily cap, the account pause, an unapproved or paused template,
  INR billing, the notices switch (``notices_off``) and D31 (nothing while suspended). The
  closure may start today or within ``max_days_ahead`` days and last at most ``max_days``.
- **Misuse.** The list endpoint shows how many closure messages were sent in the last
  ``uses_window_days`` days on the principal's screen (FR-MSG-054).
"""

from __future__ import annotations

import datetime as dt
import hashlib
import uuid
from collections import Counter
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Final

from sqlalchemy.orm import Session

from app.authz.context import UserContext
from app.core.errors import ValidationFailed
from app.core.ids import new_id
from app.core.languages import Language, parent_language
from app.messaging import catalogue as cat
from app.messaging import repository as mrepo
from app.messaging import service as messaging
from app.messaging.config import load_config
from app.messaging.sends import repository as repo
from app.messaging.sends import rules
from app.messaging.sends.models import Closure
from app.messaging.sends.schemas import (
    ClosureConfirmIn,
    ClosureIn,
    ClosureOut,
    ClosurePage,
    ClosurePreviewOut,
)
from app.messaging.sends.service import (
    SEND_EVENT,
    PreviewStale,
    SendingBlocked,
    _error,
    _guardian_reason,
    _sections,
    _settings,
    _switch_on,
)
from app.ops import service as ops
from app.students import service as students
from app.tenancy import service as tenancy

PURPOSE: Final = "school_closure"
SEND: Final = "message.closure.send"
TOKEN_PREFIX: Final = "cl_"  # noqa: S105 - a preview digest prefix, not a secret


@dataclass
class _Plan:
    day: dt.date
    closed_from: dt.date
    closed_to: dt.date
    reason: str
    section_ids: list[uuid.UUID]
    whole_school: bool
    guardians: int = 0
    recipients: list[tuple[uuid.UUID, uuid.UUID]] = field(default_factory=list)
    skipped: Counter[str] = field(default_factory=Counter)
    languages: Counter[str] = field(default_factory=Counter)
    fallbacks: int = 0

    def token(self) -> str:
        parts = [
            self.day.isoformat(),
            self.closed_from.isoformat(),
            self.closed_to.isoformat(),
            self.reason,
            "school" if self.whole_school else ",".join(sorted(s.hex for s in self.section_ids)),
            *sorted(f"{g.hex}:{s.hex}" for g, s in self.recipients),
            *[f"{k}={v}" for k, v in sorted(self.skipped.items())],
        ]
        return TOKEN_PREFIX + hashlib.sha256("|".join(parts).encode()).hexdigest()[:40]


def _dates(session: Session, data: ClosureIn) -> tuple[dt.date, dt.date, dt.date]:
    cfg = load_config().closures
    today = messaging.ist_today(session)
    closed_to = data.closed_to or data.closed_from
    if data.closed_from < today:
        raise ValidationFailed([_error("closed_from", "in_past")])
    if data.closed_from > today + dt.timedelta(days=cfg.max_days_ahead):
        raise ValidationFailed([_error("closed_from", "too_far_ahead")])
    if closed_to < data.closed_from:
        raise ValidationFailed([_error("closed_to", "before_closed_from")])
    if (closed_to - data.closed_from).days + 1 > cfg.max_days:
        raise ValidationFailed([_error("closed_to", "too_long")])
    return today, data.closed_from, closed_to


def _plan(session: Session, data: ClosureIn) -> _Plan:
    today, closed_from, closed_to = _dates(session, data)
    if data.section_ids is not None:
        year_id, wanted = _sections(session, data.section_ids)
    else:
        year = tenancy.get_current_academic_year(session)
        if year is None:
            raise ValidationFailed([_error("section_ids", "no_current_year")])
        year_id = year.id
        wanted = [s.id for s in tenancy.list_sections(session, academic_year_id=year.id)]
    plan = _Plan(
        day=today,
        closed_from=closed_from,
        closed_to=closed_to,
        reason=data.reason,
        section_ids=[] if data.whole_school else wanted,
        whole_school=data.whole_school,
    )
    enrolled = students.enrolled_in_sections(session, academic_year_id=year_id, section_ids=wanted)
    student_ids = [e.student_id for e in enrolled]
    order = {sid: i for i, sid in enumerate(student_ids)}
    first_child: dict[uuid.UUID, Any] = {}
    for brief in sorted(
        students.guardians_of_students(session, student_ids),
        key=lambda b: (order.get(b.student_id, 0), not b.is_primary),
    ):
        first_child.setdefault(brief.guardian_id, brief)
    plan.guardians = len(first_child)
    current = mrepo.current_optins(session, list(first_child))
    school_language = tenancy.parent_language(session)
    te_ok = messaging.telugu_usable(session, PURPOSE)
    for guardian_id, brief in first_child.items():
        reason = _guardian_reason(
            session, guardian_id, brief.has_phone, current.get((guardian_id, "notices"))
        )
        if reason is not None:
            plan.skipped[reason] += 1
            continue
        language: Language = parent_language(school_language, brief.preferred_language)
        if language == "te" and not te_ok:
            language = "en"
            plan.fallbacks += 1
        plan.languages[language] += 1
        plan.recipients.append((guardian_id, brief.student_id))
    return plan


def _blocked(session: Session) -> str | None:
    reason = messaging.account_block(session, PURPOSE)
    if reason is not None:
        return reason
    if not _switch_on(_settings(session), PURPOSE):
        return "notices_off"
    return None


def _dates_text(closed_from: dt.date, closed_to: dt.date) -> str:
    if closed_from == closed_to:
        return rules.parent_date(closed_from)
    return f"{rules.parent_date(closed_from)} to {rules.parent_date(closed_to)}"


def _variables(session: Session, plan: _Plan) -> dict[str, dict[str, str]]:
    school = tenancy.school_display_name(session)
    reason = load_config().closures.reasons[plan.reason]  # type: ignore[index]
    dates = _dates_text(plan.closed_from, plan.closed_to)
    te_dates = (
        dates
        if plan.closed_from == plan.closed_to
        else f"{rules.parent_date(plan.closed_from)} - {rules.parent_date(plan.closed_to)}"
    )
    return {
        "en": {"school_name": school, "dates": dates, "reason": reason.en},
        "te": {"school_name": school, "dates": te_dates, "reason": reason.te},
    }


def _message_en(variables: dict[str, str]) -> str:
    item = cat.template(PURPOSE)
    body = item.en.body
    for index, var in enumerate(item.variables, start=1):
        body = body.replace("{{" + str(index) + "}}", variables[var.name])
    return body


def _sends_at(session: Session) -> dt.datetime | None:
    cfg = load_config().closures
    return rules.closure_next_open(
        mrepo.now(session), window_start=cfg.window_start, window_end=cfg.window_end
    )


def _recent(session: Session) -> int:
    since = mrepo.now(session) - dt.timedelta(days=load_config().closures.uses_window_days)
    return repo.count_closures(session, since=since)


def _cost(count: int) -> str:
    cfg = load_config().cost
    return str(rules.estimate_cost(count, cfg.utility_rate_inr, cfg.gst_percent))


def preview_closure(session: Session, ctx: UserContext, data: ClosureIn) -> ClosurePreviewOut:
    """Who would get the closure message, who is left out and why, the English text, when
    sending starts and the estimated Meta cost (FR-MSG-048; ``message.closure.send``). Nothing
    is sent or stored. Errors: 422 ``closed_from`` (``in_past``, ``too_far_ahead``),
    ``closed_to`` (``before_closed_from``, ``too_long``), ``section_ids``."""
    messaging.ensure_available()
    plan = _plan(session, data)
    cfg = load_config()
    return ClosurePreviewOut(
        preview_token=plan.token(),
        guardians=plan.guardians,
        recipients=len(plan.recipients),
        skipped=dict(plan.skipped),
        languages=dict(plan.languages),
        language_fallbacks=plan.fallbacks,
        estimated_cost_inr=_cost(len(plan.recipients)),
        utility_rate_inr=str(cfg.cost.utility_rate_inr),
        gst_percent=str(cfg.cost.gst_percent),
        message_en=_message_en(_variables(session, plan)["en"]),
        sends_at=_sends_at(session),
        window_start=cfg.closures.window_start,
        window_end=cfg.closures.window_end,
        blocked_reason=_blocked(session),
        recent_closures=_recent(session),
    )


def _closure_out(row: Closure, counts: dict[str, int]) -> ClosureOut:
    return ClosureOut(
        id=row.id,
        whole_school=row.whole_school,
        section_ids=list(row.section_ids or []),
        closed_from=row.closed_from,
        closed_to=row.closed_to,
        reason=row.reason_code,
        recipients=row.recipients,
        queued=row.queued,
        skipped={str(k): int(v) for k, v in (row.skipped or {}).items()},
        languages={str(k): int(v) for k, v in (row.languages or {}).items()},
        estimated_cost_inr=f"{Decimal(str(row.estimated_cost_inr)):.2f}",
        counts=counts,
        sent_by=row.sent_by,
        sent_at=row.sent_at,
    )


def send_closure(session: Session, ctx: UserContext, data: ClosureConfirmIn) -> ClosureOut:
    """Queue the previewed closure message (FR-MSG-049..052; ``message.closure.send`` with
    step-up; ``Idempotency-Key``): one message per opted-in guardian, sent inside the closure
    window (06:00-21:30 IST) within the daily cap. Errors: 409 ``preview_stale`` and the
    blocking reasons (``messaging_not_connected``, ``messaging_paused``,
    ``template_not_approved``, ``template_paused``, ``messaging_currency_not_inr``,
    ``notices_off``); 422 as the preview. Audit ``messaging.closure.sent`` (dates, reason code,
    counts) and ``messaging.message.queued`` per message, in the same transaction."""
    messaging.ensure_available()
    plan = _plan(session, data)
    blocked = _blocked(session)
    if blocked is not None:
        raise SendingBlocked(blocked)
    if plan.token() != data.preview_token:
        raise PreviewStale("Who would get this message has changed. Preview again.")
    closure_id = new_id()
    by_language = _variables(session, plan)
    queued = 0
    for guardian_id, student_id in plan.recipients:
        out = messaging.queue_message(
            session,
            purpose=PURPOSE,
            guardian_id=guardian_id,
            student_id=student_id,
            source_type=PURPOSE,
            source_id=closure_id,
            dedupe_key="closure",
            variables_by_language=by_language,
            created_by=ctx.user_id,
        )
        queued += int(out is not None)
    # The row is written once, with the number queued (insert-only for the app role).
    row = repo.insert_closure(
        session,
        id=closure_id,
        whole_school=plan.whole_school,
        section_ids=plan.section_ids,
        closed_from=plan.closed_from,
        closed_to=plan.closed_to,
        reason_code=plan.reason,
        recipients=len(plan.recipients),
        skipped=dict(plan.skipped),
        languages=dict(plan.languages),
        estimated_cost_inr=_cost(len(plan.recipients)),
        queued=queued,
        sent_by=ctx.user_id,
    )
    messaging.record_audit(
        session,
        action="messaging.closure.sent",
        resource_type="school_closure",
        resource_id=row.id,
        summary={
            "closed_from": plan.closed_from.isoformat(),
            "closed_to": plan.closed_to.isoformat(),
            "reason": plan.reason,
            "whole_school": plan.whole_school,
            "sections": len(plan.section_ids),
            "recipients": len(plan.recipients),
            "queued": queued,
            "skipped": dict(plan.skipped),
            "sends_at": (_sends_at(session) or mrepo.now(session)).isoformat(),
        },
    )
    if queued:
        ops.enqueue_event(session, SEND_EVENT, {"closure_id": str(row.id)})
    return _closure_out(row, {"queued": queued} if queued else {})


def list_closures(session: Session, ctx: UserContext, *, limit: int) -> ClosurePage:
    """Closure messages sent in the last ``uses_window_days`` days, newest first, with live
    counts, and how many were sent (FR-MSG-054; ``message.read`` school-wide)."""
    messaging.ensure_available()
    cfg = load_config().closures
    since = mrepo.now(session) - dt.timedelta(days=cfg.uses_window_days)
    rows = repo.list_closures(session, since=since, limit=limit)
    counts = repo.source_status_counts(session, PURPOSE, [r.id for r in rows])
    return ClosurePage(
        items=[_closure_out(r, counts.get(r.id, {})) for r in rows],
        uses=repo.count_closures(session, since=since),
        uses_window_days=cfg.uses_window_days,
        window_start=cfg.window_start,
        window_end=cfg.window_end,
        reasons=list(cfg.reasons),
    )


def closure_ended(session: Session, closure_id: uuid.UUID, today: dt.date) -> bool:
    """Whether a closure's last date is before ``today`` (its messages are then too late)."""
    row = repo.get_closure(session, closure_id)
    return row is None or row.closed_to < today


__all__ = ["PURPOSE", "SEND", "closure_ended", "list_closures", "preview_closure", "send_closure"]
