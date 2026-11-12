"""Database access for sending (0061_messaging_sends; docs/05 §15.3). Callers inside
``app.messaging.sends`` only; every function runs in a ``core.db.tenant_session`` (RLS)."""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Collection, Mapping
from typing import Any

from sqlalchemy import and_, delete, exists, func, or_, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.core.record_tables import dump_table
from app.core.records import RecordTable
from app.messaging.models import Message
from app.messaging.repository import current_tenant_id
from app.messaging.sends.models import Broadcast, Closure, ClosureReopening, SendRun

# --- broadcasts -------------------------------------------------------------------------------


def insert_broadcast(session: Session, **values: Any) -> Broadcast:
    row = Broadcast(tenant_id=current_tenant_id(session), **values)
    session.add(row)
    session.flush()
    return row


def get_broadcast(
    session: Session, broadcast_id: uuid.UUID, *, lock: bool = False
) -> Broadcast | None:
    stmt = select(Broadcast).where(Broadcast.id == broadcast_id)
    if lock:
        stmt = stmt.with_for_update()
    return session.scalars(stmt).first()


def update_broadcast(session: Session, broadcast_id: uuid.UUID, values: Mapping[str, Any]) -> None:
    session.execute(
        update(Broadcast)
        .where(Broadcast.id == broadcast_id)
        .values(**values, version=Broadcast.version + 1)
        .execution_options(synchronize_session=False)
    )
    session.flush()
    session.expire_all()


def list_broadcasts(
    session: Session,
    *,
    notice_id: uuid.UUID | None,
    after: tuple[dt.datetime, uuid.UUID] | None,
    limit: int,
) -> list[Broadcast]:
    stmt = select(Broadcast).where(Broadcast.status != "previewed")
    if notice_id is not None:
        stmt = stmt.where(Broadcast.notice_id == notice_id)
    if after is not None:
        at, last = after
        stmt = stmt.where(
            or_(Broadcast.created_at < at, and_(Broadcast.created_at == at, Broadcast.id < last))
        )
    stmt = stmt.order_by(Broadcast.created_at.desc(), Broadcast.id.desc()).limit(limit)
    return list(session.scalars(stmt))


def broadcasts_sending(session: Session) -> list[Broadcast]:
    return list(session.scalars(select(Broadcast).where(Broadcast.status == "sending")))


def status_counts(
    session: Session,
    broadcast_ids: Collection[uuid.UUID],
    *,
    student_ids: Collection[uuid.UUID] | None = None,
) -> dict[uuid.UUID, dict[str, int]]:
    """Messages per broadcast and status (``student_ids``: only messages about these students,
    for a section-scoped reader)."""
    if not broadcast_ids or (student_ids is not None and not student_ids):
        return {}
    stmt = select(Message.broadcast_id, Message.status, func.count()).where(
        Message.broadcast_id.in_(list(broadcast_ids))
    )
    if student_ids is not None:
        stmt = stmt.where(Message.student_id.in_(list(student_ids)))
    stmt = stmt.group_by(Message.broadcast_id, Message.status)
    out: dict[uuid.UUID, dict[str, int]] = {}
    for bid, status, count in session.execute(stmt):
        if bid is not None:
            out.setdefault(bid, {})[str(status)] = int(count)
    return out


def broadcast_messages(session: Session, broadcast_id: uuid.UUID) -> list[Message]:
    return list(session.scalars(select(Message).where(Message.broadcast_id == broadcast_id)))


def guardians_sent_notice(
    session: Session, notice_id: uuid.UUID, guardian_ids: Collection[uuid.UUID]
) -> set[uuid.UUID]:
    """Guardians who already have a first-send message of this notice (US-2204 AC3)."""
    if not guardian_ids:
        return set()
    stmt = select(Message.guardian_id).where(
        Message.purpose == "school_notice",
        Message.source_id == notice_id,
        Message.dedupe_key == "notice",
        Message.guardian_id.in_(list(guardian_ids)),
    )
    return {g for g in session.scalars(stmt) if g is not None}


def reminded(
    session: Session, account_ids: Collection[uuid.UUID], dedupe_key: str
) -> set[tuple[uuid.UUID, uuid.UUID]]:
    """(account, guardian) pairs that already have a fee reminder with ``dedupe_key`` (a manual
    reminder of the day; R2-3J, FR-MSG-025)."""
    if not account_ids:
        return set()
    stmt = select(Message.source_id, Message.guardian_id).where(
        Message.purpose == "fee_reminder",
        Message.source_id.in_(list(account_ids)),
        Message.dedupe_key == dedupe_key,
    )
    return {(a, g) for a, g in session.execute(stmt) if g is not None}


def source_messages(session: Session, purpose: str, source_id: uuid.UUID) -> list[Message]:
    """The messages of one purpose about one source (a receipt), oldest first."""
    stmt = (
        select(Message)
        .where(Message.purpose == purpose, Message.source_id == source_id)
        .order_by(Message.queued_at, Message.id)
    )
    return list(session.scalars(stmt))


def queued_source_message_ids(
    session: Session, purpose: str, source_id: uuid.UUID
) -> list[uuid.UUID]:
    stmt = select(Message.id).where(
        Message.purpose == purpose, Message.source_id == source_id, Message.status == "queued"
    )
    return list(session.scalars(stmt.order_by(Message.id)))


# --- the sender -------------------------------------------------------------------------------


def due_message_ids(
    session: Session, now: dt.datetime, limit: int, purposes: Collection[str]
) -> list[uuid.UUID]:
    """Queued template messages of ``purposes`` whose next attempt time has come (oldest
    first)."""
    stmt = (
        select(Message.id)
        .where(
            Message.status == "queued",
            Message.kind == "template",
            Message.purpose.in_(list(purposes)),
            or_(Message.next_attempt_at.is_(None), Message.next_attempt_at <= now),
        )
        .order_by(Message.queued_at, Message.id)
        .limit(limit)
    )
    return list(session.scalars(stmt))


def get_message(session: Session, message_id: uuid.UUID, *, lock: bool = False) -> Message | None:
    stmt = select(Message).where(Message.id == message_id)
    if lock:
        stmt = stmt.with_for_update(skip_locked=True)
    return session.scalars(stmt).first()


def set_message(session: Session, message_id: uuid.UUID, values: Mapping[str, Any]) -> None:
    session.execute(
        update(Message)
        .where(Message.id == message_id)
        .values(**values, status_at=func.now())
        .execution_options(synchronize_session=False)
    )
    session.flush()


def sent_since(session: Session, since: dt.datetime) -> int:
    """Template messages handed to Meta since ``since`` (the daily cap). Free-form replies
    inside a window the parent opened do not count against Meta's messaging limit; since 1 Oct
    2026 they are charged above Meta's free tier (FR-MSG-029), which the spend view shows."""
    stmt = select(func.count()).where(
        Message.kind == "template", Message.sent_at.is_not(None), Message.sent_at >= since
    )
    return int(session.execute(stmt).scalar_one())


def lock_cap(session: Session) -> None:
    """The school's daily-cap lock (BL-05): a transaction advisory lock on the stable key
    ``hashtextextended('messaging.cap:' || <tenant id>, 0)``, held until the claiming
    transaction ends, so cap checks and claims of one school never interleave."""
    session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended('messaging.cap:' || :t, 0))"),
        {"t": str(current_tenant_id(session))},
    )


def claimed_count(session: Session, now: dt.datetime) -> int:
    """Template messages claimed by a sender and not recorded yet (live ``claimed_until``):
    counted in the daily cap with the sent ones (BL-05)."""
    stmt = select(func.count()).where(
        Message.kind == "template",
        Message.status == "queued",
        Message.claimed_until.is_not(None),
        Message.claimed_until > now,
    )
    return int(session.execute(stmt).scalar_one())


def queued_count(session: Session) -> int:
    return int(session.execute(select(func.count()).where(Message.status == "queued")).scalar_one())


# --- automatic runs -----------------------------------------------------------------------------


def claim_run(session: Session, job: str, run_on: dt.date, run_id: uuid.UUID) -> bool:
    """Start the day's run of ``job``; ``False`` when it already ran (or is running)."""
    stmt = (
        insert(SendRun)
        .values(id=run_id, tenant_id=current_tenant_id(session), job=job, run_on=run_on)
        .on_conflict_do_nothing(constraint="send_runs_once")
        .returning(SendRun.id)
    )
    return session.execute(stmt).scalar_one_or_none() is not None


def finish_run(session: Session, run_id: uuid.UUID, *, considered: int, queued: int) -> None:
    session.execute(
        update(SendRun)
        .where(SendRun.id == run_id)
        .values(finished_at=func.now(), considered=considered, queued=queued)
        .execution_options(synchronize_session=False)
    )


def recent_runs(session: Session, limit: int) -> list[SendRun]:
    stmt = select(SendRun).order_by(SendRun.run_on.desc(), SendRun.job).limit(limit)
    return list(session.scalars(stmt))


def messages_of_run(session: Session, purpose: str, day: dt.date) -> dict[str, int]:
    """Status counts of an automatic run's messages (absence alerts carry the date as their
    dedupe key; reminders are counted by the IST day they were queued)."""
    start = dt.datetime.combine(day, dt.time(0), tzinfo=dt.UTC)
    stmt = select(Message.status, func.count()).where(Message.purpose == purpose)
    if purpose == "absence_alert":
        stmt = stmt.where(Message.dedupe_key == day.isoformat())
    else:
        stmt = stmt.where(
            Message.queued_at >= start - dt.timedelta(hours=5, minutes=30),
            Message.queued_at < start + dt.timedelta(hours=18, minutes=30),
        )
    return {str(s): int(c) for s, c in session.execute(stmt.group_by(Message.status))}


# --- FP-02 closures (0093_msg_reach_closure) ----------------------------------------------------


def insert_closure(session: Session, **values: Any) -> Closure:
    row = Closure(tenant_id=current_tenant_id(session), **values)
    session.add(row)
    session.flush()
    return row


def get_closure(session: Session, closure_id: uuid.UUID) -> Closure | None:
    return session.scalars(select(Closure).where(Closure.id == closure_id)).first()


def list_closures(session: Session, *, since: dt.datetime, limit: int) -> list[Closure]:
    stmt = (
        select(Closure)
        .where(Closure.sent_at >= since)
        .order_by(Closure.sent_at.desc(), Closure.id.desc())
        .limit(limit)
    )
    return list(session.scalars(stmt))


def count_closures(session: Session, *, since: dt.datetime) -> int:
    return int(
        session.scalar(select(func.count()).select_from(Closure).where(Closure.sent_at >= since))
        or 0
    )


# --- FP-02 follow-up: reopening messages (0100_closure_reopen) ----------------------------------


def insert_reopening(session: Session, **values: Any) -> ClosureReopening:
    row = ClosureReopening(tenant_id=current_tenant_id(session), **values)
    session.add(row)
    session.flush()
    return row


def get_reopening(session: Session, reopening_id: uuid.UUID) -> ClosureReopening | None:
    return session.scalars(
        select(ClosureReopening).where(ClosureReopening.id == reopening_id)
    ).first()


def reopenings_of(
    session: Session, closure_ids: Collection[uuid.UUID]
) -> dict[uuid.UUID, ClosureReopening]:
    """The reopening message of each closure that has one."""
    if not closure_ids:
        return {}
    stmt = select(ClosureReopening).where(ClosureReopening.closure_id.in_(list(closure_ids)))
    return {row.closure_id: row for row in session.scalars(stmt)}


def source_status_counts(
    session: Session, purpose: str, source_ids: Collection[uuid.UUID]
) -> dict[uuid.UUID, dict[str, int]]:
    """Messages per source (a closure) and status."""
    if not source_ids:
        return {}
    stmt = (
        select(Message.source_id, Message.status, func.count())
        .where(Message.purpose == purpose, Message.source_id.in_(list(source_ids)))
        .group_by(Message.source_id, Message.status)
    )
    out: dict[uuid.UUID, dict[str, int]] = {}
    for sid, status, count in session.execute(stmt):
        out.setdefault(sid, {})[str(status)] = int(count)
    return out


# --- retention ----------------------------------------------------------------------------------


def purge_previews(session: Session, before: dt.datetime) -> int:
    result = session.execute(
        delete(Broadcast).where(Broadcast.status == "previewed", Broadcast.created_at < before)
    )
    return int(getattr(result, "rowcount", 0) or 0)


def purge_broadcasts(session: Session, before: dt.datetime) -> int:
    """Broadcasts older than ``before`` whose messages are gone (13 months, docs/05 §13)."""
    has_messages = exists().where(Message.broadcast_id == Broadcast.id)
    result = session.execute(delete(Broadcast).where(Broadcast.created_at < before, ~has_messages))
    return int(getattr(result, "rowcount", 0) or 0)


def purge_reopenings(session: Session, before: dt.datetime) -> int:
    """Reopening messages older than ``before`` whose messages are gone (13 months)."""
    has_messages = exists().where(
        Message.purpose == "school_reopening", Message.source_id == ClosureReopening.id
    )
    result = session.execute(
        delete(ClosureReopening).where(ClosureReopening.sent_at < before, ~has_messages)
    )
    return int(getattr(result, "rowcount", 0) or 0)


def purge_closures(session: Session, before: dt.datetime) -> int:
    """Closures older than ``before`` whose messages and reopening are gone (13 months,
    docs/05 §13)."""
    has_messages = exists().where(
        Message.purpose == "school_closure", Message.source_id == Closure.id
    )
    has_reopening = exists().where(ClosureReopening.closure_id == Closure.id)
    result = session.execute(
        delete(Closure).where(Closure.sent_at < before, ~has_messages, ~has_reopening)
    )
    return int(getattr(result, "rowcount", 0) or 0)


def purge_runs(session: Session, before: dt.date) -> int:
    result = session.execute(delete(SendRun).where(SendRun.run_on < before))
    return int(getattr(result, "rowcount", 0) or 0)


def export_tables(session: Session) -> list[RecordTable]:
    return [
        dump_table(session, Broadcast.__table__, name="messaging_broadcasts"),
        dump_table(session, SendRun.__table__, name="messaging_send_runs"),
        dump_table(session, Closure.__table__, name="messaging_closures"),
        dump_table(session, ClosureReopening.__table__, name="messaging_closure_reopenings"),
    ]


__all__ = [
    "broadcast_messages",
    "broadcasts_sending",
    "claim_run",
    "count_closures",
    "due_message_ids",
    "export_tables",
    "finish_run",
    "get_broadcast",
    "get_closure",
    "get_message",
    "get_reopening",
    "guardians_sent_notice",
    "insert_broadcast",
    "insert_closure",
    "insert_reopening",
    "list_broadcasts",
    "list_closures",
    "messages_of_run",
    "purge_broadcasts",
    "purge_closures",
    "purge_previews",
    "purge_reopenings",
    "purge_runs",
    "queued_count",
    "recent_runs",
    "reopenings_of",
    "sent_since",
    "set_message",
    "source_status_counts",
    "status_counts",
    "update_broadcast",
]
