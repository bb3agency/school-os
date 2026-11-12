"""Database access for the safety check log and the policy library (0099_drills_policies;
FC-09, FC-10).

Callers inside ``app.compliance`` only. Every function runs in a ``core.db.tenant_session``:
RLS limits each statement to that school. Check entries and policies are never deleted (only
the offboarding purge removes rows); acknowledgements are append-only.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Mapping, Sequence
from typing import Any, Final

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.compliance.safety_models import ComplianceCheck as Check
from app.compliance.safety_models import CompliancePolicy as Policy
from app.compliance.safety_models import CompliancePolicyAck as Ack
from app.core.record_tables import dump_table
from app.core.records import RecordTable

RECORDED: Final = "recorded"
ACTIVE: Final = "active"
LOG_LIMIT: Final = 200


def _insert[M: (Check, Policy)](session: Session, row: M) -> M:
    session.add(row)
    session.flush()
    session.refresh(row)
    return row


# --- checks -------------------------------------------------------------------------------------


def insert_check(session: Session, **values: Any) -> Check:
    return _insert(session, Check(**values))


def get_check(session: Session, check_id: uuid.UUID, *, lock: bool = False) -> Check | None:
    stmt = select(Check).where(Check.id == check_id)
    if lock:
        stmt = stmt.with_for_update()
    return session.scalars(stmt).one_or_none()


def latest_by_kind(session: Session) -> dict[str, Check]:
    """The latest recorded entry of each catalogue kind (``other`` left out)."""
    rank = (
        func.row_number()
        .over(
            partition_by=Check.kind,
            order_by=(Check.done_on.desc(), Check.created_at.desc(), Check.id),
        )
        .label("rank")
    )
    ranked = (
        select(Check.id, rank).where(Check.status == RECORDED, Check.kind != "other").subquery()
    )
    stmt = select(Check).join(ranked, ranked.c.id == Check.id).where(ranked.c.rank == 1)
    return {row.kind: row for row in session.scalars(stmt)}


def log(session: Session, *, kind: str | None = None, limit: int = LOG_LIMIT) -> list[Check]:
    """Recorded entries, newest first (removed ones left out)."""
    stmt = select(Check).where(Check.status == RECORDED)
    if kind is not None:
        stmt = stmt.where(Check.kind == kind)
    stmt = stmt.order_by(Check.done_on.desc(), Check.created_at.desc(), Check.id).limit(limit)
    return list(session.scalars(stmt))


def latest_for_reminder(session: Session) -> list[Check]:
    """The latest recorded entry of each catalogue kind, locked (the job decides which is due)."""
    latest = latest_by_kind(session)
    if not latest:
        return []
    ids = [r.id for r in latest.values()]
    stmt = select(Check).where(Check.id.in_(ids)).order_by(Check.id)
    return list(session.scalars(stmt.with_for_update(skip_locked=True)))


def update_check(
    session: Session, row: Check, values: Mapping[str, Any], *, expected_version: int
) -> Check | None:
    result = session.execute(
        update(Check)
        .where(Check.id == row.id, Check.version == expected_version)
        .values({**values, "version": Check.version + 1})
        .returning(Check.id),
        execution_options={"synchronize_session": False},
    )
    if result.scalar_one_or_none() is None:
        return None
    session.expire(row)
    return get_check(session, row.id)


def mark_check_reminded(session: Session, check_id: uuid.UUID, lead: int, day: dt.date) -> None:
    """Bookkeeping only: the version is not bumped."""
    session.execute(
        update(Check)
        .where(Check.id == check_id)
        .values(last_reminded_lead=lead, last_reminded_on=day),
        execution_options={"synchronize_session": False},
    )


# --- policies -----------------------------------------------------------------------------------


def insert_policy(session: Session, **values: Any) -> Policy:
    return _insert(session, Policy(**values))


def get_policy(session: Session, policy_id: uuid.UUID, *, lock: bool = False) -> Policy | None:
    stmt = select(Policy).where(Policy.id == policy_id)
    if lock:
        stmt = stmt.with_for_update()
    return session.scalars(stmt).one_or_none()


def policies(session: Session, *, active_only: bool = False) -> list[Policy]:
    stmt = select(Policy)
    if active_only:
        stmt = stmt.where(Policy.status == ACTIVE)
    return list(session.scalars(stmt.order_by(Policy.status, Policy.review_due, Policy.id)))


def policies_for_roles(session: Session, roles: Sequence[str]) -> list[Policy]:
    """Active policies that ask any of ``roles`` to acknowledge them."""
    if not roles:
        return []
    stmt = (
        select(Policy)
        .where(Policy.status == ACTIVE, Policy.ack_roles.overlap(list(roles)))
        .order_by(Policy.review_due, Policy.id)
    )
    return list(session.scalars(stmt))


def update_policy(
    session: Session, row: Policy, values: Mapping[str, Any], *, expected_version: int
) -> Policy | None:
    result = session.execute(
        update(Policy)
        .where(Policy.id == row.id, Policy.version == expected_version)
        .values({**values, "version": Policy.version + 1})
        .returning(Policy.id),
        execution_options={"synchronize_session": False},
    )
    if result.scalar_one_or_none() is None:
        return None
    session.expire(row)
    return get_policy(session, row.id)


def set_policy_values(session: Session, policy_id: uuid.UUID, values: Mapping[str, Any]) -> None:
    """Bookkeeping only (reminders, acknowledgement requests): the version is not bumped."""
    session.execute(
        update(Policy).where(Policy.id == policy_id).values(dict(values)),
        execution_options={"synchronize_session": False},
    )


def due_policies(session: Session) -> list[Policy]:
    stmt = (
        select(Policy)
        .where(Policy.status == ACTIVE)
        .order_by(Policy.review_due, Policy.id)
        .with_for_update(skip_locked=True)
    )
    return list(session.scalars(stmt))


# --- acknowledgements ---------------------------------------------------------------------------


def insert_ack(session: Session, **values: Any) -> bool:
    """Insert one acknowledgement; ``False`` when the member already acknowledged that version
    (unique key, no error)."""
    stmt = (
        pg_insert(Ack)
        .values(**values)
        .on_conflict_do_nothing(constraint="compliance_policy_acks_once")
        .returning(Ack.id)
    )
    return session.execute(stmt).scalar_one_or_none() is not None


def acks_for(
    session: Session, policy_ids: Sequence[uuid.UUID]
) -> dict[tuple[uuid.UUID, uuid.UUID, uuid.UUID], dt.datetime]:
    """(policy, version, member) -> when, for the given policies."""
    if not policy_ids:
        return {}
    stmt = select(Ack).where(Ack.policy_id.in_(list(policy_ids)))
    return {
        (a.policy_id, a.document_version_id, a.membership_id): a.acknowledged_at
        for a in session.scalars(stmt)
    }


def acks_of_member(session: Session, membership_id: uuid.UUID) -> list[Ack]:
    stmt = (
        select(Ack).where(Ack.membership_id == membership_id).order_by(Ack.acknowledged_at, Ack.id)
    )
    return list(session.scalars(stmt))


# --- export -------------------------------------------------------------------------------------


def export_tables(session: Session) -> list[RecordTable]:
    """The school's check log, policies and acknowledgements for its full data export."""
    return [
        dump_table(
            session, Check.__table__, name="compliance_checks", order_by=("created_at", "id")
        ),
        dump_table(
            session, Policy.__table__, name="compliance_policies", order_by=("created_at", "id")
        ),
        dump_table(
            session,
            Ack.__table__,
            name="compliance_policy_acks",
            order_by=("acknowledged_at", "id"),
        ),
    ]


__all__ = [
    "ACTIVE",
    "RECORDED",
    "acks_for",
    "acks_of_member",
    "due_policies",
    "export_tables",
    "get_check",
    "get_policy",
    "insert_ack",
    "insert_check",
    "insert_policy",
    "latest_by_kind",
    "latest_for_reminder",
    "log",
    "mark_check_reminded",
    "policies",
    "policies_for_roles",
    "set_policy_values",
    "update_check",
    "update_policy",
]
