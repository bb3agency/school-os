"""Opt-in coverage and reach follow-up (FP-01; FR-MSG-039..046; docs/09 §4 "Messaging").

- **Coverage (FR-MSG-039, FR-MSG-040).** Per section of the current year and opt-in category:
  students, students *reached* (at least one guardian opted in for the category, with a mobile
  number, the opt-in given for that number), guardians, guardians opted in, guardians without a
  mobile number. Counts only. A class teacher (scoped ``message.read``) sees their sections.
- **Not reached (FR-MSG-041..043).** A section's students no message of the category reaches,
  with each guardian's relation and the reason code (``no_optin``, ``opted_out``,
  ``no_mobile_number``, ``optin_for_other_number``). Names only within the reader's scope
  (``students.summaries``: null without ``student.read_basic``); never a phone number. The web
  prints join slips for them with the existing ``POST /messaging/join-slips`` (``student_ids``).
- **Broadcast follow-up (FR-MSG-044, FR-MSG-045).** For a confirmed notice broadcast: the
  students of its sections whom the notice (any broadcast of it) did not reach: ``not_sent``,
  ``failed`` or ``not_opened`` (sent at least ``reach.not_opened_hours`` ago, not read on
  WhatsApp and the parent link never opened). A list to act on, never an automatic resend, and
  no score per teacher (risk noted in the research report).

Reads only; nothing is audited (counts and C2 names the reader may already see; FR-MSG-046
pins that no list carries a phone number and logs carry ids and counts only).
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Final

from sqlalchemy.orm import Session

from app.authz.context import UserContext
from app.core.errors import NotFound
from app.messaging import repository as mrepo
from app.messaging import service as messaging
from app.messaging.config import load_config
from app.messaging.models import Message
from app.messaging.sends import repository as repo
from app.messaging.sends import rules
from app.messaging.sends.schemas import (
    BroadcastFollowUpOut,
    CoverageOut,
    FollowUpStudentOut,
    GuardianReachOut,
    NotReachedOut,
    NotReachedStudentOut,
    OptinCategoryName,
    SectionCoverageOut,
)
from app.messaging.sends.service import READ, _guardian_reason, _section_labels
from app.public import service as public
from app.students import service as students
from app.tenancy import service as tenancy

FOLLOW_UP: Final = ("not_sent", "failed", "not_opened")


@dataclass(frozen=True, slots=True)
class _Audience:
    """Students per section (current year) within the reader's reach."""

    by_section: dict[uuid.UUID, list[uuid.UUID]]
    labels: dict[uuid.UUID, str]


def _audience(
    session: Session, ctx: UserContext, section_ids: Sequence[uuid.UUID] | None
) -> _Audience:
    year = tenancy.get_current_academic_year(session)
    labels = _section_labels(session)
    if year is None:
        return _Audience({}, labels)
    wanted = list(labels) if section_ids is None else [s for s in section_ids if s in labels]
    enrolled = students.enrolled_in_sections(session, academic_year_id=year.id, section_ids=wanted)
    reach: set[uuid.UUID] | None = None
    if not ctx.scope_for(READ).school_wide:
        reach = set(
            students.list_students_in_scope(session, ctx, section_ids=wanted, permissions=(READ,))
        )
    by_section: dict[uuid.UUID, list[uuid.UUID]] = defaultdict(list)
    for e in enrolled:
        if reach is None or e.student_id in reach:
            by_section[e.section_id].append(e.student_id)
    return _Audience(dict(by_section), labels)


def _guardian_reasons(
    session: Session, student_ids: Sequence[uuid.UUID], category: str
) -> tuple[dict[uuid.UUID, list[students.GuardianBrief]], dict[uuid.UUID, str | None]]:
    """Guardians per student and, per guardian, why no message of ``category`` reaches them
    (``None``: reached)."""
    briefs = students.guardians_of_students(session, student_ids)
    per_student: dict[uuid.UUID, list[students.GuardianBrief]] = defaultdict(list)
    for brief in briefs:
        per_student[brief.student_id].append(brief)
    guardian_ids = sorted({b.guardian_id for b in briefs})
    current = mrepo.current_optins(session, guardian_ids)
    has_phone = {b.guardian_id: b.has_phone for b in briefs}
    reasons = {
        gid: _guardian_reason(session, gid, has_phone[gid], current.get((gid, category)))
        for gid in guardian_ids
    }
    return dict(per_student), reasons


def coverage(session: Session, ctx: UserContext, category: OptinCategoryName) -> CoverageOut:
    """Opt-in coverage per section for ``category`` (FR-MSG-039, FR-MSG-040; ``message.read``;
    a class teacher: their sections). Counts only."""
    messaging.ensure_available()
    audience = _audience(session, ctx, None)
    all_students = [sid for ids in audience.by_section.values() for sid in ids]
    per_student, reasons = _guardian_reasons(session, all_students, category)
    sections: list[SectionCoverageOut] = []
    totals: Counter[str] = Counter()
    seen_guardians: set[uuid.UUID] = set()
    opted_guardians: set[uuid.UUID] = set()
    for section_id, student_ids in audience.by_section.items():
        guardians: set[uuid.UUID] = set()
        without_mobile: set[uuid.UUID] = set()
        reached = 0
        for sid in student_ids:
            briefs = per_student.get(sid, [])
            guardians.update(b.guardian_id for b in briefs)
            without_mobile.update(b.guardian_id for b in briefs if not b.has_phone)
            reached += int(any(reasons.get(b.guardian_id) is None for b in briefs))
        opted = {g for g in guardians if reasons.get(g) is None}
        sections.append(
            SectionCoverageOut(
                section_id=section_id,
                label=audience.labels.get(section_id, "?"),
                students=len(student_ids),
                students_reached=reached,
                guardians=len(guardians),
                guardians_opted_in=len(opted),
                guardians_without_mobile=len(without_mobile),
            )
        )
        totals["students"] += len(student_ids)
        totals["reached"] += reached
        seen_guardians |= guardians
        opted_guardians |= opted
    sections.sort(key=lambda s: s.label)
    return CoverageOut(
        category=category,
        students=totals["students"],
        students_reached=totals["reached"],
        guardians=len(seen_guardians),
        guardians_opted_in=len(opted_guardians),
        sections=sections,
    )


def _guardians_out(
    briefs: Sequence[students.GuardianBrief], reason: Mapping[uuid.UUID, str]
) -> list[GuardianReachOut]:
    return [
        GuardianReachOut(
            guardian_id=b.guardian_id,
            relationship=b.relationship,
            is_primary=b.is_primary,
            reason=reason.get(b.guardian_id, "not_sent"),
        )
        for b in sorted(briefs, key=lambda b: (not b.is_primary, b.relationship))
    ]


def not_reached(
    session: Session, ctx: UserContext, section_id: uuid.UUID, category: OptinCategoryName
) -> NotReachedOut:
    """A section's students whom no message of ``category`` reaches, with each guardian's
    reason (FR-MSG-041, FR-MSG-042; ``message.read``, scope: 404 outside it). Names within the
    reader's scope; no phone numbers."""
    messaging.ensure_available()
    try:
        tenancy.get_section(session, section_id)
    except NotFound:
        raise NotFound("Section not found") from None
    grant = ctx.scope_for(READ)
    audience = _audience(session, ctx, [section_id])
    student_ids = audience.by_section.get(section_id, [])
    if not student_ids and not grant.school_wide and section_id not in grant.section_ids:
        section = tenancy.get_section(session, section_id)
        if section.class_id not in grant.class_ids:
            raise NotFound("Section not found")
    per_student, reasons = _guardian_reasons(session, student_ids, category)
    missing = [
        sid
        for sid in student_ids
        if not any(reasons.get(b.guardian_id) is None for b in per_student.get(sid, []))
    ]
    limit = load_config().reach.max_list_rows
    names = students.summaries(session, ctx, missing[:limit])
    items = [
        NotReachedStudentOut(
            student_id=sid,
            name=names[sid].display_name if sid in names else None,
            class_section=names[sid].class_section if sid in names else None,
            guardians=_guardians_out(
                per_student.get(sid, []),
                {g: r for g, r in reasons.items() if r is not None},
            ),
        )
        for sid in missing[:limit]
    ]
    items.sort(key=lambda i: (i.name is None, i.name or "", str(i.student_id)))
    return NotReachedOut(
        section_id=section_id,
        label=audience.labels.get(section_id, "?"),
        category=category,
        students=len(student_ids),
        items=items,
        truncated=len(missing) > limit,
    )


def _message_reason(message: Message) -> str:
    if message.status == "failed":
        return messaging.error_reason(message.error_code) or "provider_error"
    if message.status == "skipped":
        return message.skip_reason or "skipped"
    return message.status


def _outcome(
    messages: Sequence[Message], viewed: set[uuid.UUID], now: dt.datetime, hours: int
) -> str:
    """One student's outcome from their guardians' messages of the notice."""
    if not messages:
        return "not_sent"
    opened = any(
        m.status == "read" or (m.public_handle_id is not None and m.public_handle_id in viewed)
        for m in messages
    )
    if opened:
        return "opened"
    live = [m for m in messages if m.status in ("queued", "sent", "delivered")]
    if not live:
        return "failed"
    if any(
        m.status == "queued" or not rules.not_opened(m.sent_at, now, hours=hours, opened=False)
        for m in live
    ):
        return "pending"
    return "not_opened"


def broadcast_follow_up(
    session: Session, ctx: UserContext, broadcast_id: uuid.UUID
) -> BroadcastFollowUpOut:
    """The students of a confirmed broadcast's sections whom the notice did not reach, with each
    guardian's status or reason (FR-MSG-044, FR-MSG-045; ``message.read``; a class teacher:
    their sections). Names within the reader's scope; no phone numbers; never a resend."""
    messaging.ensure_available()
    row = repo.get_broadcast(session, broadcast_id)
    if row is None or row.status == "previewed":
        raise NotFound("Broadcast not found")
    cfg = load_config().reach
    audience = _audience(session, ctx, list(row.section_ids))
    student_ids = [sid for ids in audience.by_section.values() for sid in ids]
    per_student, reasons = _guardian_reasons(session, student_ids, "notices")
    by_guardian: dict[uuid.UUID, list[Message]] = defaultdict(list)
    for message in repo.source_messages(session, "school_notice", row.notice_id):
        if message.guardian_id is not None:
            by_guardian[message.guardian_id].append(message)
    handles = {
        m.public_handle_id
        for ms in by_guardian.values()
        for m in ms
        if m.public_handle_id is not None
    }
    viewed = public.viewed_handles(session, handles)
    now = mrepo.now(session)
    outcomes: Counter[str] = Counter()
    follow: list[tuple[uuid.UUID, str, list[students.GuardianBrief], dict[uuid.UUID, str]]] = []
    for sid in student_ids:
        briefs = per_student.get(sid, [])
        messages = [m for b in briefs for m in by_guardian.get(b.guardian_id, [])]
        outcome = _outcome(messages, viewed, now, cfg.not_opened_hours)
        outcomes[outcome] += 1
        if outcome in FOLLOW_UP:
            why: dict[uuid.UUID, str] = {}
            for b in briefs:
                own = by_guardian.get(b.guardian_id, [])
                if own:
                    why[b.guardian_id] = _message_reason(own[-1])
                else:
                    why[b.guardian_id] = reasons.get(b.guardian_id) or "not_sent"
            follow.append((sid, outcome, briefs, why))
    limit = cfg.max_list_rows
    names = students.summaries(session, ctx, [f[0] for f in follow[:limit]])
    items = [
        FollowUpStudentOut(
            student_id=sid,
            name=names[sid].display_name if sid in names else None,
            class_section=names[sid].class_section if sid in names else None,
            outcome=outcome,
            guardians=_guardians_out(briefs, why),
        )
        for sid, outcome, briefs, why in follow[:limit]
    ]
    items.sort(key=lambda i: (FOLLOW_UP.index(i.outcome), i.class_section or "", i.name or ""))
    return BroadcastFollowUpOut(
        broadcast_id=row.id,
        notice_id=row.notice_id,
        not_opened_hours=cfg.not_opened_hours,
        outcomes=dict(outcomes),
        items=items,
        truncated=len(follow) > limit,
    )


__all__ = ["broadcast_follow_up", "coverage", "not_reached"]
