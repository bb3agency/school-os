"""Database access for the Tally connector (0036_tally; docs/05 §7.4).

Callers inside ``app.tally`` only. Every function runs in a ``core.db.tenant_session``: RLS limits
each statement to that school and ``tenant_id`` is taken from the session's context. Bound
parameters only.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from sqlalchemy import and_, delete, func, insert, select, text, update
from sqlalchemy.orm import Session

from app.core.ids import new_id
from app.core.record_tables import dump_table
from app.core.records import RecordTable
from app.tally.models import Device, EnrolmentCode, Group, Party, PartyLink, Sync


def current_tenant_id(session: Session) -> uuid.UUID:
    value: object = session.execute(text("SELECT core.current_tenant()")).scalar_one()
    if value is None:
        raise RuntimeError("tally repository used outside tenant_session")
    return uuid.UUID(str(value))


# --- enrolment codes ------------------------------------------------------------------------------


def insert_code(session: Session, values: Mapping[str, Any]) -> EnrolmentCode:
    return session.scalars(
        insert(EnrolmentCode)
        .values(tenant_id=current_tenant_id(session), **values)
        .returning(EnrolmentCode)
    ).one()


def lock_code(session: Session, code_hash: bytes) -> EnrolmentCode | None:
    return session.scalars(
        select(EnrolmentCode).where(EnrolmentCode.code_hash == code_hash).with_for_update(),
        execution_options={"populate_existing": True},
    ).one_or_none()


def mark_code_used(session: Session, code_id: uuid.UUID, at: dt.datetime) -> None:
    session.execute(update(EnrolmentCode).where(EnrolmentCode.id == code_id).values(used_at=at))


# --- devices --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DeviceKeys:
    """What the signature guard needs (the wrapped secrets never leave app.tally)."""

    id: uuid.UUID
    status: str
    key_id: str | None
    key_ciphertext: bytes | None
    next_key_id: str | None
    next_key_ciphertext: bytes | None
    rotation_started_at: dt.datetime | None


def device_keys(session: Session, device_id: uuid.UUID) -> DeviceKeys | None:
    row = session.execute(
        select(
            Device.id,
            Device.status,
            Device.key_id,
            Device.key_ciphertext,
            Device.next_key_id,
            Device.next_key_ciphertext,
            Device.rotation_started_at,
        ).where(Device.id == device_id)
    ).one_or_none()
    if row is None:
        return None
    return DeviceKeys(
        id=row.id,
        status=row.status,
        key_id=row.key_id,
        key_ciphertext=bytes(row.key_ciphertext) if row.key_ciphertext is not None else None,
        next_key_id=row.next_key_id,
        next_key_ciphertext=(
            bytes(row.next_key_ciphertext) if row.next_key_ciphertext is not None else None
        ),
        rotation_started_at=row.rotation_started_at,
    )


def insert_device(session: Session, values: Mapping[str, Any]) -> Device:
    return session.scalars(
        insert(Device).values(tenant_id=current_tenant_id(session), **values).returning(Device)
    ).one()


def get_device(session: Session, device_id: uuid.UUID, *, lock: bool = False) -> Device | None:
    stmt = select(Device).where(Device.id == device_id)
    if lock:
        stmt = stmt.with_for_update()
    return session.scalars(stmt, execution_options={"populate_existing": True}).one_or_none()


def list_devices(session: Session) -> list[Device]:
    return list(
        session.scalars(
            select(Device).order_by(Device.status, Device.enrolled_at.desc(), Device.id)
        )
    )


def count_active_devices(session: Session) -> int:
    return int(
        session.execute(
            select(func.count()).select_from(Device).where(Device.status == "active")
        ).scalar_one()
    )


def update_device(
    session: Session, device_id: uuid.UUID, values: Mapping[str, Any], *, bump: bool = True
) -> Device:
    extra: dict[str, Any] = {"version": Device.version + 1} if bump else {}
    return session.scalars(
        update(Device).where(Device.id == device_id).values(**values, **extra).returning(Device),
        execution_options={"populate_existing": True, "synchronize_session": False},
    ).one()


def silent_devices(session: Session, before: dt.datetime) -> list[Device]:
    """Active devices whose last call (or enrolment) is older than ``before`` and that were not
    reported since that call."""
    last = func.coalesce(Device.last_seen_at, Device.enrolled_at)
    return list(
        session.scalars(
            select(Device)
            .where(
                Device.status == "active",
                last < before,
                (Device.silent_notified_at.is_(None)) | (Device.silent_notified_at < last),
            )
            .with_for_update(skip_locked=True)
        )
    )


# --- groups ---------------------------------------------------------------------------------------


def list_groups(session: Session, company: str | None = None) -> list[Group]:
    stmt = select(Group)
    if company is not None:
        stmt = stmt.where(Group.company == company)
    return list(session.scalars(stmt.order_by(Group.company, Group.name, Group.id)))


def selected_groups(session: Session) -> list[Group]:
    return list(
        session.scalars(
            select(Group).where(Group.selected.is_(True)).order_by(Group.company, Group.name)
        )
    )


def latest_company(session: Session) -> str | None:
    """The company of the most recently reported catalog."""
    value: str | None = session.execute(
        select(Group.company).order_by(Group.last_seen_at.desc(), Group.company).limit(1)
    ).scalar_one_or_none()
    return value


def upsert_groups(
    session: Session, company: str, groups: Sequence[tuple[str, str | None]], at: dt.datetime
) -> None:
    """The catalog of ``company``: present groups upserted, the others marked not present."""
    existing = {g.name: g for g in list_groups(session, company)}
    tenant_id = current_tenant_id(session)
    seen: set[str] = set()
    new_rows: list[dict[str, Any]] = []
    for name, parent in groups:
        if name in seen:
            continue
        seen.add(name)
        row = existing.get(name)
        if row is None:
            new_rows.append(
                {
                    "id": new_id(),
                    "tenant_id": tenant_id,
                    "company": company,
                    "name": name,
                    "parent": parent,
                    "first_seen_at": at,
                    "last_seen_at": at,
                }
            )
        else:
            session.execute(
                update(Group)
                .where(Group.id == row.id)
                .values(parent=parent, present=True, last_seen_at=at)
            )
    if new_rows:
        session.execute(insert(Group), new_rows)
    gone = [g.id for name, g in existing.items() if name not in seen and g.present]
    if gone:
        session.execute(update(Group).where(Group.id.in_(gone)).values(present=False))


def set_selection(
    session: Session,
    company: str,
    group_ids: Collection[uuid.UUID],
    user_id: uuid.UUID,
    at: dt.datetime,
) -> None:
    """Exactly ``group_ids`` (of ``company``) selected; every other group unselected."""
    session.execute(
        update(Group)
        .where(
            Group.selected.is_(True), ~Group.id.in_(list(group_ids)) | (Group.company != company)
        )
        .values(selected=False, selected_by=None, selected_at=None, version=Group.version + 1)
    )
    if group_ids:
        session.execute(
            update(Group)
            .where(Group.id.in_(list(group_ids)), Group.selected.is_(False))
            .values(selected=True, selected_by=user_id, selected_at=at, version=Group.version + 1)
        )


# --- syncs and parties ----------------------------------------------------------------------------


def sync_for_batch(session: Session, device_id: uuid.UUID, batch_id: uuid.UUID) -> Sync | None:
    return session.scalars(
        select(Sync).where(Sync.device_id == device_id, Sync.batch_id == batch_id)
    ).one_or_none()


def insert_sync(session: Session, values: Mapping[str, Any]) -> Sync:
    return session.scalars(
        insert(Sync).values(tenant_id=current_tenant_id(session), **values).returning(Sync)
    ).one()


def latest_sync(session: Session) -> Sync | None:
    return session.scalars(
        select(Sync).order_by(Sync.received_at.desc(), Sync.id.desc()).limit(1)
    ).one_or_none()


def parties_of_company(session: Session, company: str) -> list[Party]:
    return list(session.scalars(select(Party).where(Party.company == company).with_for_update()))


def insert_parties(session: Session, rows: Sequence[Mapping[str, Any]]) -> None:
    if rows:
        tenant_id = current_tenant_id(session)
        session.execute(insert(Party), [{"tenant_id": tenant_id, **r} for r in rows])


def present_parties(session: Session) -> list[Party]:
    """Every party of the last snapshot (any company), locked for the snapshot being applied."""
    return list(session.scalars(select(Party).where(Party.present.is_(True)).with_for_update()))


def update_parties(session: Session, rows: Sequence[Mapping[str, Any]]) -> None:
    """Bulk update by primary key (each row has ``id`` and the changed columns)."""
    if rows:
        session.execute(update(Party), [dict(r) for r in rows])


def mark_parties_missing(
    session: Session, party_ids: Collection[uuid.UUID], sync_id: uuid.UUID
) -> None:
    if party_ids:
        session.execute(
            update(Party)
            .where(Party.id.in_(list(party_ids)))
            .values(present=False, last_sync_id=sync_id, version=Party.version + 1)
        )


def get_party(session: Session, party_id: uuid.UUID, *, lock: bool = False) -> Party | None:
    stmt = select(Party).where(Party.id == party_id)
    if lock:
        stmt = stmt.with_for_update()
    return session.scalars(stmt, execution_options={"populate_existing": True}).one_or_none()


@dataclass(frozen=True, slots=True)
class PartyFilter:
    linked: bool | None
    query: str | None
    after: tuple[str, uuid.UUID] | None
    limit: int


def list_parties(session: Session, spec: PartyFilter) -> list[Party]:
    """Present parties by ledger name, optionally only linked / unlinked or matching ``query``
    (case-insensitive substring of the ledger name); ``limit + 1`` rows for paging."""
    linked = select(PartyLink.party_id).where(PartyLink.party_id == Party.id).exists()
    stmt = select(Party).where(Party.present.is_(True))
    if spec.linked is True:
        stmt = stmt.where(linked)
    elif spec.linked is False:
        stmt = stmt.where(~linked)
    if spec.query:
        stmt = stmt.where(Party.ledger_name.icontains(spec.query, autoescape=True))
    if spec.after is not None:
        name, pid = spec.after
        stmt = stmt.where(
            (Party.ledger_name > name) | and_(Party.ledger_name == name, Party.id > pid)
        )
    return list(session.scalars(stmt.order_by(Party.ledger_name, Party.id).limit(spec.limit + 1)))


def links_of(
    session: Session, party_ids: Collection[uuid.UUID]
) -> dict[uuid.UUID, list[uuid.UUID]]:
    if not party_ids:
        return {}
    out: dict[uuid.UUID, list[uuid.UUID]] = {}
    for party_id, student_id in session.execute(
        select(PartyLink.party_id, PartyLink.student_id)
        .where(PartyLink.party_id.in_(list(party_ids)))
        .order_by(PartyLink.linked_at, PartyLink.id)
    ):
        out.setdefault(party_id, []).append(student_id)
    return out


def insert_link(session: Session, values: Mapping[str, Any]) -> bool:
    """False when the pair is already linked (idempotent)."""
    exists = session.execute(
        select(PartyLink.id).where(
            PartyLink.party_id == values["party_id"], PartyLink.student_id == values["student_id"]
        )
    ).scalar_one_or_none()
    if exists is not None:
        return False
    session.execute(insert(PartyLink).values(tenant_id=current_tenant_id(session), **values))
    return True


def delete_link(session: Session, party_id: uuid.UUID, student_id: uuid.UUID) -> bool:
    result = session.execute(
        delete(PartyLink)
        .where(PartyLink.party_id == party_id, PartyLink.student_id == student_id)
        .returning(PartyLink.id)
    )
    return result.first() is not None


@dataclass(frozen=True, slots=True)
class LinkedBalance:
    student_id: uuid.UUID
    party_id: uuid.UUID
    ledger_name: str
    group_name: str
    closing_balance: Decimal
    as_of: dt.date
    present: bool
    synced_at: dt.datetime


def linked_balances(
    session: Session, student_ids: Collection[uuid.UUID] | None
) -> list[LinkedBalance]:
    """Balances of the parties linked to ``student_ids`` (None = every linked student), the
    filter applied in SQL: an unlinked party is never read here (ADR-0032 §6)."""
    stmt = (
        select(
            PartyLink.student_id,
            Party.id,
            Party.ledger_name,
            Party.group_name,
            Party.closing_balance,
            Party.as_of,
            Party.present,
            Sync.received_at,
        )
        .join(Party, and_(Party.tenant_id == PartyLink.tenant_id, Party.id == PartyLink.party_id))
        .join(Sync, and_(Sync.tenant_id == Party.tenant_id, Sync.id == Party.last_sync_id))
    )
    if student_ids is not None:
        if not student_ids:
            return []
        stmt = stmt.where(PartyLink.student_id.in_(list(student_ids)))
    rows = session.execute(stmt.order_by(PartyLink.student_id, Party.ledger_name, Party.id))
    return [
        LinkedBalance(
            student_id=r[0],
            party_id=r[1],
            ledger_name=r[2],
            group_name=r[3],
            closing_balance=r[4],
            as_of=r[5],
            present=r[6],
            synced_at=r[7],
        )
        for r in rows
    ]


@dataclass(frozen=True, slots=True)
class PartyCounts:
    parties: int
    linked: int
    unlinked: int
    unlinked_due: Decimal
    as_of: dt.date | None


def party_counts(session: Session) -> PartyCounts:
    linked = select(PartyLink.party_id).where(PartyLink.party_id == Party.id).exists()
    row = session.execute(
        select(
            func.count(),
            func.count().filter(linked),
            func.coalesce(
                func.sum(Party.closing_balance).filter(~linked, Party.closing_balance > 0), 0
            ),
            func.max(Party.as_of),
        ).where(Party.present.is_(True))
    ).one()
    total, with_link = int(row[0]), int(row[1])
    return PartyCounts(
        parties=total,
        linked=with_link,
        unlinked=total - with_link,
        unlinked_due=Decimal(row[2]),
        as_of=row[3],
    )


def delete_old_syncs(session: Session, before: dt.datetime) -> int:
    """Sync records older than ``before`` that no party still points to."""
    referenced = select(Party.last_sync_id).where(Party.last_sync_id == Sync.id).exists()
    result = session.execute(
        delete(Sync).where(Sync.received_at < before, ~referenced).returning(Sync.id)
    )
    return len(result.all())


# --- full export ----------------------------------------------------------------------------------


def export_tables(session: Session) -> list[RecordTable]:
    """The connector's rows for the school's full data export (FR-ADM-001). Wrapped device keys
    and code hashes are never exported."""
    return [
        dump_table(
            session,
            Device.__table__,
            name="tally_devices",
            exclude=("key_ciphertext", "next_key_ciphertext"),
        ),
        dump_table(
            session, EnrolmentCode.__table__, name="tally_enrolment_codes", exclude=("code_hash",)
        ),
        dump_table(session, Group.__table__, name="tally_groups"),
        dump_table(session, Sync.__table__, name="tally_syncs"),
        dump_table(session, Party.__table__, name="tally_parties"),
        dump_table(session, PartyLink.__table__, name="tally_party_links"),
    ]


__all__ = [
    "DeviceKeys",
    "LinkedBalance",
    "PartyCounts",
    "PartyFilter",
    "count_active_devices",
    "current_tenant_id",
    "delete_link",
    "delete_old_syncs",
    "device_keys",
    "export_tables",
    "get_device",
    "get_party",
    "insert_code",
    "insert_device",
    "insert_link",
    "insert_parties",
    "insert_sync",
    "latest_company",
    "latest_sync",
    "linked_balances",
    "links_of",
    "list_devices",
    "list_groups",
    "list_parties",
    "lock_code",
    "mark_code_used",
    "mark_parties_missing",
    "parties_of_company",
    "party_counts",
    "present_parties",
    "selected_groups",
    "set_selection",
    "silent_devices",
    "sync_for_batch",
    "update_device",
    "update_parties",
    "upsert_groups",
]
