"""Data-principal access report and erasure for WhatsApp messaging (R3-1A; FR-DPDP-009,
FR-DPDP-012..014, ADR-0048 decisions 6 and 8, PRV-031, PRV-032; ADR-0043).

**Report.** For a guardian: their opt-in decisions (category, decision, date, how it was given),
the messages sent to them (template, date and time, status) and how many replies of each kind
(join, STOP, other) came from their current number. For a student: the messages sent about
them. Never the message variables, phone blind indexes, provider ids, link handles or who
recorded a decision.

**Erasure.** ``message_optins`` (guardian): ``msg.optins`` is append-only evidence for the app
role (no DELETE grant; the rows are deleted with the guardian), so every category the guardian
is opted in to gets an opt-out decision recorded by the signed-in staff member
(``staff_recorded_stop``), audited ``messaging.optin.recorded`` like any opt-out, and their open
parent links are revoked (FR-MSG-010). ``messages`` (guardian or student): the encrypted
variables of their messages are erased now, as the 180-day retention job would; the message
rows (template, status, dates) stay as evidence until their 13-month retention. Counts cover
only rows that change.
"""

from __future__ import annotations

import uuid
from collections.abc import Collection, Mapping
from typing import Any, Final

from sqlalchemy.orm import Session

from app.audit import service as audit
from app.core.ids import new_id
from app.core.logging import get_context
from app.core.report_format import IST, cap_rows, fmt_count, fmt_date, fmt_datetime, humanise
from app.core.subjects import MAX_SECTION_ROWS, ErasureOutcome, ReportSection, Subject
from app.identity import service as identity
from app.messaging import repository as repo
from app.messaging.config import OPTIN_CATEGORIES, load_config
from app.messaging.models import Message
from app.public import service as public
from app.students import crypto
from app.students import service as students

MESSAGES_CLASS: Final = "messages"
OPTINS_CLASS: Final = "message_optins"
INBOUND_CLASS: Final = "message_inbound"
REASON: Final = "privacy_request"
OPTOUT_METHOD: Final = "staff_recorded_stop"
MESSAGE_COLUMNS: Final = ("Message", "Queued on", "Status")
OPTIN_COLUMNS: Final = ("Category", "Decision", "Decided on", "How")
INBOUND_COLUMNS: Final = ("Kind of message", "Count")
_METHODS: Final = {
    "whatsapp_join": "Join message on WhatsApp",
    "signed_form": "Signed form",
    "in_person": "In person at the school",
    "whatsapp_stop": "STOP sent on WhatsApp",
    "staff_recorded_stop": "Opt-out recorded by the school",
}
_INBOUND: Final = {"join": "Join message", "stop": "STOP", "other": "Other reply"}
_NO_ACTOR_STEP: Final = (
    "Record an opt-out for each WhatsApp category on the guardian's messaging page "
    "(no signed-in staff member was available to record it)."
)


def _request_id() -> str | None:
    value = get_context().get("request_id")
    return value if isinstance(value, str) else None


def _audit(
    session: Session,
    action: str,
    resource_type: str,
    resource_id: uuid.UUID,
    summary: Mapping[str, Any],
) -> None:
    audit.record(
        session,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        summary=summary,
        request_id=_request_id(),
    )


# --- report ---------------------------------------------------------------------------------------


def _message_label(row: Message) -> str:
    return humanise(row.template_key or row.purpose)


def _messages(
    session: Session,
    *,
    guardian_id: uuid.UUID | None = None,
    student_id: uuid.UUID | None = None,
) -> ReportSection | None:
    found, total = repo.messages_about(
        session, guardian_id=guardian_id, student_id=student_id, limit=MAX_SECTION_ROWS
    )
    if total == 0:
        return None
    rows = [(_message_label(m), fmt_datetime(m.queued_at), humanise(m.status)) for m in found]
    kept, note = cap_rows(rows, total)
    return ReportSection(
        class_key=MESSAGES_CLASS,
        title="WhatsApp messages sent to you" if guardian_id else "WhatsApp messages about you",
        columns=MESSAGE_COLUMNS,
        rows=tuple(kept),
        note=note,
    )


def _optins(session: Session, guardian_id: uuid.UUID) -> ReportSection | None:
    history = repo.optin_history(session, guardian_id)  # newest first
    if not history:
        return None
    rows = [
        (
            humanise(r.category),
            humanise(r.status),
            fmt_date(r.decided_on),
            _METHODS.get(r.method, humanise(r.method)),
        )
        for r in history
    ]
    kept, note = cap_rows(rows)
    return ReportSection(
        class_key=OPTINS_CLASS,
        title="WhatsApp opt-ins",
        columns=OPTIN_COLUMNS,
        rows=tuple(kept),
        note=note,
    )


def _inbound(session: Session, guardian_id: uuid.UUID) -> ReportSection | None:
    try:
        phone = students.guardian_phone_index(session, guardian_id)
    except crypto.KeyMaterialMissing:
        return None
    if phone is None:
        return None
    counts = repo.inbound_kind_counts(session, phone.digest)
    if not counts:
        return None
    rows = tuple(
        (_INBOUND.get(kind, humanise(kind)), fmt_count(n)) for kind, n in sorted(counts.items())
    )
    return ReportSection(
        class_key=INBOUND_CLASS,
        title="WhatsApp messages received from your number",
        columns=INBOUND_COLUMNS,
        rows=rows,
        note="Only the kind and time of a message are kept, never its text.",
    )


def subject_report(session: Session, subject: Subject) -> list[ReportSection]:
    """Opt-ins, messages and reply kinds of a guardian; messages about a student; [] for staff
    members or when nothing is held."""
    if subject.kind == "guardian":
        sections = [
            _optins(session, subject.id),
            _messages(session, guardian_id=subject.id),
            _inbound(session, subject.id),
        ]
    elif subject.kind == "student":
        sections = [_messages(session, student_id=subject.id)]
    else:
        return []
    return [s for s in sections if s is not None]


# --- erasure --------------------------------------------------------------------------------------


def _actor(session: Session) -> tuple[uuid.UUID, uuid.UUID] | None:
    """(user, membership) of the signed-in staff member, else ``None``."""
    user_id = repo.current_user_id(session)
    if user_id is None:
        return None
    found = identity.members_for_users(session, [user_id]).get(user_id)
    return None if found is None else (user_id, found[0])


def _opt_out(session: Session, guardian_id: uuid.UUID, *, dry_run: bool) -> ErasureOutcome:
    current = repo.current_optins(session, [guardian_id])
    opted_in = [
        current[(guardian_id, c)]
        for c in OPTIN_CATEGORIES
        if (guardian_id, c) in current and current[(guardian_id, c)].status == "opted_in"
    ]
    if dry_run or not opted_in:
        return ErasureOutcome(class_key=OPTINS_CLASS, count=len(opted_in))
    actor = _actor(session)
    if actor is None:
        return ErasureOutcome(class_key=OPTINS_CLASS, count=0, manual_steps=(_NO_ACTOR_STEP,))
    user_id, membership_id = actor
    today = repo.now(session).astimezone(IST).date()
    wording = load_config().optin.wording_version
    for previous in opted_in:
        row = repo.insert_optin(
            session,
            id=new_id(),
            guardian_id=guardian_id,
            category=previous.category,
            seq=previous.seq + 1,
            status="opted_out",
            method=OPTOUT_METHOD,
            wording_version=wording,
            decided_on=today,
            evidence_document_id=None,
            phone_bidx=None,
            phone_key_version=None,
            recorded_by=user_id,
            recorded_by_membership=membership_id,
        )
        _audit(
            session,
            "messaging.optin.recorded",
            "guardian",
            guardian_id,
            {
                "optin_id": row.id,
                "category": previous.category,
                "status": "opted_out",
                "method": OPTOUT_METHOD,
                "previous_status": previous.status,
                "has_evidence": False,
                "evidence_document_id": None,
                "reason": REASON,
            },
        )
    # FR-MSG-010 / FR-PUB-007: links sent to this guardian stop working with the opt-out.
    public.revoke_for_guardian(session, guardian_id, reason="opted_out")
    return ErasureOutcome(class_key=OPTINS_CLASS, count=len(opted_in))


def _erase_vars(session: Session, subject: Subject, *, dry_run: bool) -> ErasureOutcome:
    about: dict[str, uuid.UUID] = (
        {"guardian_id": subject.id} if subject.kind == "guardian" else {"student_id": subject.id}
    )
    if dry_run:
        return ErasureOutcome(
            class_key=MESSAGES_CLASS, count=repo.count_vars_about(session, **about)
        )
    erased = repo.erase_vars_about(session, **about)
    if erased:
        _audit(
            session,
            "messaging.message_vars.erased",
            subject.kind,
            subject.id,
            {"messages": erased, "reason": REASON},
        )
    return ErasureOutcome(class_key=MESSAGES_CLASS, count=erased)


def erase_for_subject(
    session: Session, subject: Subject, class_keys: Collection[str], *, dry_run: bool
) -> list[ErasureOutcome]:
    """Guardian: opt out of every category (``message_optins``) and erase the variables of the
    messages sent to them (``messages``). Student: erase the variables of the messages about
    them. With ``dry_run`` only count. Runs in the caller's transaction."""
    if subject.kind not in ("guardian", "student"):
        return []
    keys = set(class_keys)
    out: list[ErasureOutcome] = []
    if subject.kind == "guardian" and OPTINS_CLASS in keys:
        out.append(_opt_out(session, subject.id, dry_run=dry_run))
    if MESSAGES_CLASS in keys:
        out.append(_erase_vars(session, subject, dry_run=dry_run))
    return out


__all__ = ["erase_for_subject", "subject_report"]
