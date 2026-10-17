"""Database access for messaging (0058_messaging_core; docs/05 §15.3; ADR-0043).

Callers inside ``app.messaging`` only. Every function runs in a ``core.db.tenant_session``: RLS
limits each statement to that school. ``msg.optins`` is append-only for the app role (INSERT and
SELECT only); the other tables are updated only in the columns the migration grants.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Collection, Mapping, Sequence
from typing import Any

from sqlalchemy import and_, delete, func, or_, select, text, update
from sqlalchemy.dialects.postgresql import distinct_on, insert
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.core.errors import DomainError, PreconditionFailed, ValidationFailed
from app.core.record_tables import dump_table
from app.core.records import RecordTable
from app.messaging.models import (
    Account,
    InboundEvent,
    JoinCode,
    Message,
    MessageTemplate,
    MessagingSettings,
    Optin,
    ProviderEvent,
)


def now(session: Session) -> dt.datetime:
    value: dt.datetime = session.execute(select(func.now())).scalar_one()
    return value


def current_tenant_id(session: Session) -> uuid.UUID:
    value: object = session.execute(text("SELECT core.current_tenant()")).scalar_one()
    if value is None:
        raise RuntimeError("messaging repository used outside tenant_session")
    return uuid.UUID(str(value))


def translate_db_error(exc: DBAPIError) -> DomainError | None:
    """Constraint names -> API errors (the service validates first; these close races)."""
    message = str(exc.orig) if exc.orig is not None else str(exc)
    if "optins_seq_key" in message:
        return PreconditionFailed(
            "Someone else recorded a decision for this guardian just now. Reload and try again."
        )
    if "optins_evidence_fk" in message:
        return ValidationFailed(
            [
                {
                    "field": "evidence_document_id",
                    "code": "not_found",
                    "message_key": "errors.not_found",
                }
            ]
        )
    if "accounts_one_live" in message:
        return PreconditionFailed("WhatsApp was connected by someone else just now. Reload.")
    return None


# --- accounts ---------------------------------------------------------------------------------


def live_account(session: Session, *, lock: bool = False) -> Account | None:
    """The school's account that is not disconnected (at most one)."""
    stmt = select(Account).where(Account.status != "disconnected")
    if lock:
        stmt = stmt.with_for_update()
    return session.scalars(stmt).first()


def get_account(session: Session, account_id: uuid.UUID, *, lock: bool = False) -> Account | None:
    stmt = select(Account).where(Account.id == account_id)
    if lock:
        stmt = stmt.with_for_update()
    return session.scalars(stmt).first()


def insert_account(session: Session, **values: Any) -> Account:
    row = Account(tenant_id=current_tenant_id(session), **values)
    session.add(row)
    session.flush()
    return row


def update_account(session: Session, account_id: uuid.UUID, values: Mapping[str, Any]) -> None:
    session.execute(
        update(Account)
        .where(Account.id == account_id)
        .values(**values, version=Account.version + 1)
        .execution_options(synchronize_session=False)
    )
    session.flush()


def set_account_secrets(
    session: Session, account_id: uuid.UUID, *, token: bytes | None, pin: bytes | None
) -> None:
    session.execute(
        update(Account)
        .where(Account.id == account_id)
        .values(token_ciphertext=token, pin_ciphertext=pin)
        .execution_options(synchronize_session=False)
    )


# --- templates --------------------------------------------------------------------------------


def templates_of(session: Session, account_id: uuid.UUID) -> list[MessageTemplate]:
    return list(
        session.scalars(
            select(MessageTemplate)
            .where(MessageTemplate.account_id == account_id)
            .order_by(
                MessageTemplate.template_key,
                MessageTemplate.template_version,
                MessageTemplate.language,
            )
        )
    )


def ensure_template_rows(
    session: Session, account_id: uuid.UUID, rows: Sequence[Mapping[str, Any]]
) -> None:
    """Insert the catalogue's (key, version, language) rows that are missing (``not_created``)."""
    if not rows:
        return
    tenant_id = current_tenant_id(session)
    stmt = insert(MessageTemplate).values(
        [{"tenant_id": tenant_id, "account_id": account_id, **r} for r in rows]
    )
    session.execute(stmt.on_conflict_do_nothing(constraint="templates_key"))


def update_template(session: Session, template_id: uuid.UUID, values: Mapping[str, Any]) -> None:
    session.execute(
        update(MessageTemplate)
        .where(MessageTemplate.id == template_id)
        .values(**values)
        .execution_options(synchronize_session=False)
    )


def template_for(
    session: Session, account_id: uuid.UUID, key: str, version: int, language: str
) -> MessageTemplate | None:
    return session.scalars(
        select(MessageTemplate).where(
            MessageTemplate.account_id == account_id,
            MessageTemplate.template_key == key,
            MessageTemplate.template_version == version,
            MessageTemplate.language == language,
        )
    ).first()


# --- settings ---------------------------------------------------------------------------------


def get_settings(session: Session, *, lock: bool = False) -> MessagingSettings | None:
    stmt = select(MessagingSettings)
    if lock:
        stmt = stmt.with_for_update()
    return session.scalars(stmt).first()


def insert_settings(session: Session, **values: Any) -> MessagingSettings:
    row = MessagingSettings(tenant_id=current_tenant_id(session), **values)
    session.add(row)
    session.flush()
    return row


def update_settings(
    session: Session, settings_id: uuid.UUID, values: Mapping[str, Any]
) -> MessagingSettings:
    session.execute(
        update(MessagingSettings)
        .where(MessagingSettings.id == settings_id)
        .values(**values, version=MessagingSettings.version + 1)
        .execution_options(synchronize_session=False)
    )
    session.flush()
    row = session.get(MessagingSettings, settings_id, populate_existing=True)
    if row is None:  # pragma: no cover - the row was just updated in this transaction
        raise RuntimeError("messaging settings row vanished")
    return row


# --- opt-ins ----------------------------------------------------------------------------------


def optin_history(session: Session, guardian_id: uuid.UUID) -> list[Optin]:
    """Every decision for the guardian, newest first (per category by seq)."""
    return list(
        session.scalars(
            select(Optin)
            .where(Optin.guardian_id == guardian_id)
            .order_by(Optin.recorded_at.desc(), Optin.category, Optin.seq.desc())
        )
    )


def current_optins(
    session: Session, guardian_ids: Collection[uuid.UUID]
) -> dict[tuple[uuid.UUID, str], Optin]:
    """The latest decision per (guardian, category) that has one."""
    if not guardian_ids:
        return {}
    stmt = (
        select(Optin)
        .where(Optin.guardian_id.in_(list(guardian_ids)))
        .ext(distinct_on(Optin.guardian_id, Optin.category))
        .order_by(Optin.guardian_id, Optin.category, Optin.seq.desc())
    )
    return {(r.guardian_id, r.category): r for r in session.scalars(stmt)}


def insert_optin(session: Session, **values: Any) -> Optin:
    row = Optin(tenant_id=current_tenant_id(session), **values)
    session.add(row)
    session.flush()
    return row


# --- join codes -------------------------------------------------------------------------------


def insert_join_code(session: Session, **values: Any) -> JoinCode:
    row = JoinCode(tenant_id=current_tenant_id(session), **values)
    session.add(row)
    session.flush()
    return row


def join_code_by_hash(session: Session, code_hash: bytes) -> JoinCode | None:
    return session.scalars(
        select(JoinCode).where(JoinCode.code_hash == code_hash).with_for_update()
    ).first()


def mark_join_code_used(session: Session, code_id: uuid.UUID, at: dt.datetime) -> None:
    session.execute(
        update(JoinCode)
        .where(JoinCode.id == code_id, JoinCode.used_at.is_(None))
        .values(used_at=at)
        .execution_options(synchronize_session=False)
    )


def purge_join_codes(session: Session, before: dt.datetime, batch: int) -> int:
    ids = select(JoinCode.id).where(JoinCode.expires_at < before).limit(batch)
    result = session.execute(
        delete(JoinCode).where(JoinCode.id.in_(ids)).execution_options(synchronize_session=False)
    )
    return int(result.rowcount or 0)  # type: ignore[attr-defined]


# --- messages ---------------------------------------------------------------------------------


def insert_message(session: Session, **values: Any) -> Message | None:
    """Insert one message row; ``None`` when the same (purpose, source, guardian, dedupe key)
    already exists (idempotent)."""
    tenant_id = current_tenant_id(session)
    stmt = (
        insert(Message)
        .values(tenant_id=tenant_id, **values)
        .on_conflict_do_nothing(constraint="messages_once")
        .returning(Message.id)
    )
    new_id = session.execute(stmt).scalar_one_or_none()
    if new_id is None:
        return None
    return session.get(Message, new_id)


def get_message(session: Session, message_id: uuid.UUID, *, lock: bool = False) -> Message | None:
    stmt = select(Message).where(Message.id == message_id)
    if lock:
        stmt = stmt.with_for_update()
    return session.scalars(stmt).first()


def message_by_provider_id(session: Session, provider_message_id: str) -> Message | None:
    return session.scalars(
        select(Message).where(Message.provider_message_id == provider_message_id).with_for_update()
    ).first()


def update_message(session: Session, message_id: uuid.UUID, values: Mapping[str, Any]) -> None:
    session.execute(
        update(Message)
        .where(Message.id == message_id)
        .values(**values, status_at=func.now())
        .execution_options(synchronize_session=False)
    )
    session.flush()


def open_handle_ids(session: Session) -> list[uuid.UUID]:
    """Parent link handles carried by messages (revoked on disconnect)."""
    return [
        r
        for r in session.scalars(
            select(Message.public_handle_id).where(Message.public_handle_id.is_not(None))
        )
        if r is not None
    ]


def search_messages(
    session: Session,
    *,
    guardian_ids: Collection[uuid.UUID] | None,
    student_ids: Collection[uuid.UUID] | None,
    purpose: str | None,
    status: str | None,
    since: dt.datetime | None,
    until: dt.datetime | None,
    after: tuple[dt.datetime, uuid.UUID] | None,
    limit: int,
) -> list[Message]:
    stmt = select(Message)
    reach = []
    if guardian_ids is not None:
        reach.append(Message.guardian_id.in_(list(guardian_ids)))
    if student_ids is not None:
        reach.append(Message.student_id.in_(list(student_ids)))
    if reach:
        stmt = stmt.where(or_(*reach))
    if purpose is not None:
        stmt = stmt.where(Message.purpose == purpose)
    if status is not None:
        stmt = stmt.where(Message.status == status)
    if since is not None:
        stmt = stmt.where(Message.queued_at >= since)
    if until is not None:
        stmt = stmt.where(Message.queued_at < until)
    if after is not None:
        at, last_id = after
        stmt = stmt.where(
            or_(Message.queued_at < at, and_(Message.queued_at == at, Message.id < last_id))
        )
    stmt = stmt.order_by(Message.queued_at.desc(), Message.id.desc()).limit(limit)
    return list(session.scalars(stmt))


def erase_message_vars(session: Session, before: dt.datetime, batch: int) -> int:
    ids = (
        select(Message.id)
        .where(Message.queued_at < before, Message.vars_ciphertext.is_not(None))
        .limit(batch)
    )
    result = session.execute(
        update(Message)
        .where(Message.id.in_(ids))
        .values(vars_ciphertext=None)
        .execution_options(synchronize_session=False)
    )
    return int(result.rowcount or 0)  # type: ignore[attr-defined]


def purge_messages(session: Session, before: dt.datetime, batch: int) -> int:
    ids = select(Message.id).where(Message.queued_at < before).limit(batch)
    result = session.execute(
        delete(Message).where(Message.id.in_(ids)).execution_options(synchronize_session=False)
    )
    return int(result.rowcount or 0)  # type: ignore[attr-defined]


# --- inbound events ---------------------------------------------------------------------------


def insert_inbound(session: Session, **values: Any) -> InboundEvent:
    row = InboundEvent(tenant_id=current_tenant_id(session), **values)
    session.add(row)
    session.flush()
    return row


def replies_since(session: Session, phone_bidx: bytes, since: dt.datetime) -> int:
    value: int = session.execute(
        select(func.count())
        .select_from(InboundEvent)
        .where(
            InboundEvent.phone_bidx == phone_bidx,
            InboundEvent.replied.is_(True),
            InboundEvent.received_at >= since,
        )
    ).scalar_one()
    return value


def mark_inbound(session: Session, event_id: uuid.UUID, *, replied: bool, outcome: str) -> None:
    session.execute(
        update(InboundEvent)
        .where(InboundEvent.id == event_id)
        .values(replied=replied, outcome=outcome)
        .execution_options(synchronize_session=False)
    )


def purge_inbound(session: Session, before: dt.datetime, batch: int) -> int:
    ids = select(InboundEvent.id).where(InboundEvent.received_at < before).limit(batch)
    result = session.execute(
        delete(InboundEvent)
        .where(InboundEvent.id.in_(ids))
        .execution_options(synchronize_session=False)
    )
    return int(result.rowcount or 0)  # type: ignore[attr-defined]


# --- provider events (webhook inbox) ----------------------------------------------------------


def insert_provider_event(session: Session, **values: Any) -> uuid.UUID | None:
    """Store one event; ``None`` when its key was stored before (Meta retries for days)."""
    tenant_id = current_tenant_id(session)
    stmt = (
        insert(ProviderEvent)
        .values(tenant_id=tenant_id, **values)
        .on_conflict_do_nothing(constraint="provider_events_key")
        .returning(ProviderEvent.id)
    )
    value: uuid.UUID | None = session.execute(stmt).scalar_one_or_none()
    return value


def set_event_payload(session: Session, event_id: uuid.UUID, payload: bytes) -> None:
    session.execute(
        update(ProviderEvent)
        .where(ProviderEvent.id == event_id)
        .values(payload_ciphertext=payload)
        .execution_options(synchronize_session=False)
    )


def pending_events(session: Session, limit: int) -> list[ProviderEvent]:
    """Events waiting to be applied, oldest first, locked (``SKIP LOCKED``: two workers never
    apply the same event)."""
    return list(
        session.scalars(
            select(ProviderEvent)
            .where(ProviderEvent.status == "received")
            .order_by(ProviderEvent.event_at.asc().nulls_first(), ProviderEvent.received_at)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
    )


def finish_event(
    session: Session,
    event_id: uuid.UUID,
    *,
    status: str,
    attempts: int,
    error_code: str | None,
) -> None:
    values: dict[str, Any] = {"status": status, "attempts": attempts, "error_code": error_code}
    values["processed_at"] = None if status == "received" else func.now()
    session.execute(
        update(ProviderEvent)
        .where(ProviderEvent.id == event_id)
        .values(**values)
        .execution_options(synchronize_session=False)
    )


def purge_provider_events(session: Session, before: dt.datetime, batch: int) -> int:
    ids = (
        select(ProviderEvent.id)
        .where(ProviderEvent.processed_at.is_not(None), ProviderEvent.processed_at < before)
        .limit(batch)
    )
    result = session.execute(
        delete(ProviderEvent)
        .where(ProviderEvent.id.in_(ids))
        .execution_options(synchronize_session=False)
    )
    return int(result.rowcount or 0)  # type: ignore[attr-defined]


# --- full data export (FR-ADM-001) ------------------------------------------------------------


def export_tables(session: Session) -> list[RecordTable]:
    """The school's messaging records without secrets, hashes, blind indexes or payloads."""
    return [
        dump_table(
            session,
            Account.__table__,
            name="messaging_accounts",
            exclude=("token_ciphertext", "pin_ciphertext", "waba_handle_id"),
        ),
        dump_table(session, MessageTemplate.__table__, name="messaging_templates"),
        dump_table(session, MessagingSettings.__table__, name="messaging_settings"),
        dump_table(
            session,
            Optin.__table__,
            name="messaging_optins",
            exclude=("phone_bidx", "phone_key_version"),
            order_by=("guardian_id", "category", "seq"),
        ),
        dump_table(
            session,
            Message.__table__,
            name="messaging_messages",
            exclude=("phone_bidx", "phone_key_version", "vars_ciphertext", "public_handle_id"),
        ),
    ]


__all__ = [
    "current_optins",
    "current_tenant_id",
    "ensure_template_rows",
    "erase_message_vars",
    "export_tables",
    "finish_event",
    "get_account",
    "get_message",
    "get_settings",
    "insert_account",
    "insert_inbound",
    "insert_join_code",
    "insert_message",
    "insert_optin",
    "insert_provider_event",
    "insert_settings",
    "join_code_by_hash",
    "live_account",
    "mark_inbound",
    "mark_join_code_used",
    "message_by_provider_id",
    "now",
    "open_handle_ids",
    "optin_history",
    "pending_events",
    "purge_inbound",
    "purge_join_codes",
    "purge_messages",
    "purge_provider_events",
    "replies_since",
    "search_messages",
    "set_account_secrets",
    "set_event_payload",
    "template_for",
    "templates_of",
    "translate_db_error",
    "update_account",
    "update_message",
    "update_settings",
    "update_template",
]
