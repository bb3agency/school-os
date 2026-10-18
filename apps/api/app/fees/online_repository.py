"""Database access for online payments (R2-2D). Only ``app.fees`` modules call this module.

Every function runs in the caller's ``tenant_session``: RLS limits each statement to the current
school. Fee permissions are school-wide (FR-FEE-024), so there is no per-object scope filter
here; the service decides who may call what.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Collection, Mapping
from typing import Any

from sqlalchemy import Table, delete, func, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.core.errors import Conflict, DomainError
from app.core.record_tables import dump_table
from app.core.records import RecordTable
from app.fees.models import Receipt, Refund
from app.fees.online_models import (
    PaymentConnection,
    PaymentIntent,
    ProviderEvent,
    ProviderPayment,
    ReconciliationItem,
    ReconciliationRun,
)

_CONFLICTS: dict[str, tuple[str, str]] = {
    "payment_connections_one_live": (
        "payments_already_connected",
        "Online payments are already connected. Disconnect them first.",
    ),
    "payment_intents_frozen": ("intent_closed", "This payment link was already closed."),
    "receipts_payment_intent_unique": (
        "intent_paid",
        "This payment link already has a receipt.",
    ),
    "reconciliation_items_frozen": (
        "item_closed",
        "This item was already explained or resolved. Reload to see it.",
    ),
}


def translate_db_error(exc: DBAPIError) -> DomainError | None:
    constraint = getattr(getattr(exc.orig, "diag", None), "constraint_name", None) or ""
    if constraint in _CONFLICTS:
        code, message = _CONFLICTS[constraint]
        return Conflict(message, code=code)
    return None


def now(session: Session) -> dt.datetime:
    value: dt.datetime = session.execute(select(func.now())).scalar_one()
    return value


def lock_account_intents(session: Session, account_id: uuid.UUID) -> None:
    """Serialise link creation for one account (FR-PAY-006: reuse, never two open links for the
    same instalments)."""
    session.execute(
        text(
            "SELECT pg_advisory_xact_lock(hashtextextended('fees.intent:' || "
            "core.current_tenant() || ':' || :a, 0))"
        ),
        {"a": str(account_id)},
    )


def _bump(model: Any, values: Mapping[str, Any]) -> dict[str, Any]:
    out = dict(values)
    if hasattr(model, "version"):
        out["version"] = model.version + 1
    return out


def _update(session: Session, model: Any, row_id: uuid.UUID, values: Mapping[str, Any]) -> Any:
    stmt = (
        update(model)
        .where(model.id == row_id)
        .values(**_bump(model, values))
        .returning(model)
        .execution_options(synchronize_session=False)
    )
    return session.scalars(stmt, execution_options={"populate_existing": True}).one()


def _get(session: Session, model: Any, row_id: uuid.UUID, *, lock: bool = False) -> Any:
    stmt = select(model).where(model.id == row_id)
    if lock:
        stmt = stmt.with_for_update()
    return session.scalars(stmt, execution_options={"populate_existing": True}).one_or_none()


# --- connections ----------------------------------------------------------------------------------


def live_connection(session: Session, *, lock: bool = False) -> PaymentConnection | None:
    """The school's connection that is not disconnected (active or revoked), if any."""
    stmt = select(PaymentConnection).where(PaymentConnection.status != "disconnected")
    if lock:
        stmt = stmt.with_for_update()
    return session.scalars(stmt, execution_options={"populate_existing": True}).one_or_none()


def get_connection(
    session: Session, connection_id: uuid.UUID, *, lock: bool = False
) -> PaymentConnection | None:
    row: PaymentConnection | None = _get(session, PaymentConnection, connection_id, lock=lock)
    return row


def insert_connection(session: Session, **values: Any) -> PaymentConnection:
    return session.scalars(
        insert(PaymentConnection).values(**values).returning(PaymentConnection)
    ).one()


def update_connection(
    session: Session, connection_id: uuid.UUID, values: Mapping[str, Any]
) -> PaymentConnection:
    row: PaymentConnection = _update(session, PaymentConnection, connection_id, values)
    return row


def connections_with_tokens_expiring(
    session: Session, before: dt.datetime
) -> list[PaymentConnection]:
    return list(
        session.scalars(
            select(PaymentConnection).where(
                PaymentConnection.status == "active",
                PaymentConnection.mode == "oauth",
                PaymentConnection.access_expires_at < before,
            )
        )
    )


# --- intents --------------------------------------------------------------------------------------


def insert_intent(session: Session, **values: Any) -> PaymentIntent:
    return session.scalars(insert(PaymentIntent).values(**values).returning(PaymentIntent)).one()


def get_intent(
    session: Session, intent_id: uuid.UUID, *, lock: bool = False
) -> PaymentIntent | None:
    """``lock``: ``FOR NO KEY UPDATE`` (receipts and provider payments that only reference the
    intent are not blocked by it)."""
    stmt = select(PaymentIntent).where(PaymentIntent.id == intent_id)
    if lock:
        stmt = stmt.with_for_update(key_share=True)
    return session.scalars(stmt, execution_options={"populate_existing": True}).one_or_none()


def update_intent(
    session: Session, intent_id: uuid.UUID, values: Mapping[str, Any]
) -> PaymentIntent:
    row: PaymentIntent = _update(session, PaymentIntent, intent_id, values)
    return row


def intents_of_account(session: Session, account_id: uuid.UUID, limit: int) -> list[PaymentIntent]:
    return list(
        session.scalars(
            select(PaymentIntent)
            .where(PaymentIntent.account_id == account_id)
            .order_by(PaymentIntent.created_at.desc(), PaymentIntent.id.desc())
            .limit(limit)
        )
    )


def latest_intent_of_handle(session: Session, handle_id: uuid.UUID) -> PaymentIntent | None:
    """The newest intent a parent dues link made (R2-3H return page)."""
    return session.scalars(
        select(PaymentIntent)
        .where(PaymentIntent.public_handle_id == handle_id)
        .order_by(PaymentIntent.created_at.desc(), PaymentIntent.id.desc())
        .limit(1)
    ).first()


def open_intents(
    session: Session, account_id: uuid.UUID, *, lock: bool = False
) -> list[PaymentIntent]:
    stmt = (
        select(PaymentIntent)
        .where(PaymentIntent.account_id == account_id, PaymentIntent.status == "created")
        .order_by(PaymentIntent.created_at)
    )
    if lock:
        stmt = stmt.with_for_update(key_share=True)
    return list(session.scalars(stmt, execution_options={"populate_existing": True}))


def expired_open_intents(session: Session, at: dt.datetime, limit: int) -> list[PaymentIntent]:
    return list(
        session.scalars(
            select(PaymentIntent)
            .where(PaymentIntent.status == "created", PaymentIntent.expires_at < at)
            .order_by(PaymentIntent.expires_at)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
    )


def open_intents_of_connection(
    session: Session, connection_id: uuid.UUID, limit: int
) -> list[PaymentIntent]:
    return list(
        session.scalars(
            select(PaymentIntent)
            .where(PaymentIntent.connection_id == connection_id, PaymentIntent.status == "created")
            .order_by(PaymentIntent.created_at)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
    )


def receipt_for_intent(session: Session, intent_id: uuid.UUID) -> Receipt | None:
    return session.scalars(
        select(Receipt).where(Receipt.payment_intent_id == intent_id, Receipt.status == "issued")
    ).first()


def receipt_for_payment(session: Session, provider_payment_id: str) -> Receipt | None:
    return session.scalars(
        select(Receipt).where(Receipt.provider_payment_id == provider_payment_id)
    ).one_or_none()


def online_receipts_between(session: Session, start: dt.date, end: dt.date) -> list[Receipt]:
    return list(
        session.scalars(
            select(Receipt)
            .where(
                Receipt.mode == "razorpay",
                Receipt.receipt_date >= start,
                Receipt.receipt_date <= end,
            )
            .order_by(Receipt.receipt_date, Receipt.receipt_no)
        )
    )


def refund_by_provider_id(session: Session, provider_refund_id: str) -> Refund | None:
    return session.scalars(
        select(Refund).where(Refund.provider_refund_id == provider_refund_id).with_for_update()
    ).one_or_none()


def refunds_in_flight(session: Session, limit: int) -> list[Refund]:
    return list(
        session.scalars(
            select(Refund)
            .where(Refund.mode == "original_method", Refund.status.in_(("approved", "processing")))
            .order_by(Refund.decided_at)
            .limit(limit)
        )
    )


# --- webhook inbox --------------------------------------------------------------------------------


def insert_event(session: Session, **values: Any) -> uuid.UUID | None:
    """Store one event; ``None`` when its id was stored before (FR-PAY-007: replays are no-ops)."""
    stmt = (
        insert(ProviderEvent)
        .values(**values)
        .on_conflict_do_nothing(constraint="provider_events_event_unique")
        .returning(ProviderEvent.id)
    )
    value: uuid.UUID | None = session.execute(stmt).scalar_one_or_none()
    return value


def next_events(session: Session, at: dt.datetime, limit: int) -> list[ProviderEvent]:
    """Events due for processing, oldest first, locked (``SKIP LOCKED``: two workers never
    process the same event)."""
    return list(
        session.scalars(
            select(ProviderEvent)
            .where(ProviderEvent.status == "received", ProviderEvent.next_attempt_at <= at)
            .order_by(ProviderEvent.received_at, ProviderEvent.id)
            .limit(limit)
            .with_for_update(skip_locked=True),
            execution_options={"populate_existing": True},
        )
    )


def finish_event(session: Session, event_id: uuid.UUID, values: Mapping[str, Any]) -> None:
    session.execute(
        update(ProviderEvent)
        .where(ProviderEvent.id == event_id)
        .values(**values)
        .execution_options(synchronize_session=False)
    )


def count_events(session: Session, status: str) -> int:
    value: int = (
        session.scalar(
            select(func.count()).select_from(ProviderEvent).where(ProviderEvent.status == status)
        )
        or 0
    )
    return value


def stale_event_payloads(session: Session, version: int, limit: int) -> list[ProviderEvent]:
    """Inbox payloads under another DEK version (SEC-012 rotation), locked."""
    if limit <= 0:
        return []
    key_version = func.get_byte(ProviderEvent.payload_ciphertext, 1) * 256 + func.get_byte(
        ProviderEvent.payload_ciphertext, 2
    )
    return list(
        session.scalars(
            select(ProviderEvent)
            .where(key_version != version)
            .order_by(ProviderEvent.id)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
    )


def purge_events(session: Session, before: dt.datetime) -> int:
    result = session.execute(
        delete(ProviderEvent).where(
            ProviderEvent.status != "received", ProviderEvent.processed_at < before
        )
    )
    return int(getattr(result, "rowcount", 0) or 0)


# --- provider payments ----------------------------------------------------------------------------


def get_payment(
    session: Session, provider_payment_id: str, *, lock: bool = False
) -> ProviderPayment | None:
    stmt = select(ProviderPayment).where(ProviderPayment.provider_payment_id == provider_payment_id)
    if lock:
        stmt = stmt.with_for_update()
    return session.scalars(stmt, execution_options={"populate_existing": True}).one_or_none()


def upsert_payment(session: Session, values: Mapping[str, Any]) -> ProviderPayment:
    base = insert(ProviderPayment).values(**values)
    fixed = (
        "id",
        "tenant_id",
        "provider",
        "connection_id",
        "provider_payment_id",
        "first_seen_at",
        "receipt_id",
    )
    changing: dict[str, Any] = {k: base.excluded[k] for k in values if k not in fixed}
    changing["updated_at"] = func.now()
    stmt = base.on_conflict_do_update(
        constraint="provider_payments_payment_unique", set_=changing
    ).returning(ProviderPayment)
    row: ProviderPayment = session.scalars(
        stmt, execution_options={"populate_existing": True}
    ).one()
    return row


def link_payment_receipt(session: Session, provider_payment_id: str, receipt_id: uuid.UUID) -> None:
    session.execute(
        update(ProviderPayment)
        .where(ProviderPayment.provider_payment_id == provider_payment_id)
        .values(receipt_id=receipt_id, updated_at=func.now())
        .execution_options(synchronize_session=False)
    )


def payments_by_ids(session: Session, ids: Collection[str]) -> dict[str, ProviderPayment]:
    if not ids:
        return {}
    rows = session.scalars(
        select(ProviderPayment).where(ProviderPayment.provider_payment_id.in_(list(ids)))
    )
    return {r.provider_payment_id: r for r in rows}


def unsettled_payments(session: Session, before: dt.datetime) -> list[ProviderPayment]:
    return list(
        session.scalars(
            select(ProviderPayment).where(
                ProviderPayment.status.in_(("captured", "refunded")),
                ProviderPayment.receipt_id.is_not(None),
                ProviderPayment.settled_at.is_(None),
                ProviderPayment.paid_at < before,
            )
        )
    )


def set_settlement(
    session: Session, provider_payment_id: str, settlement_id: str | None, settled_at: dt.datetime
) -> bool:
    result = session.execute(
        update(ProviderPayment)
        .where(
            ProviderPayment.provider_payment_id == provider_payment_id,
            ProviderPayment.settled_at.is_(None),
        )
        .values(settlement_id=settlement_id, settled_at=settled_at, updated_at=func.now())
        .execution_options(synchronize_session=False)
    )
    return bool(getattr(result, "rowcount", 0))


# --- reconciliation -------------------------------------------------------------------------------


def get_run(session: Session, run_date: dt.date) -> ReconciliationRun | None:
    return session.scalars(
        select(ReconciliationRun).where(ReconciliationRun.run_date == run_date)
    ).one_or_none()


def insert_run(session: Session, **values: Any) -> ReconciliationRun:
    return session.scalars(
        insert(ReconciliationRun).values(**values).returning(ReconciliationRun)
    ).one()


def update_run(session: Session, run_id: uuid.UUID, values: Mapping[str, Any]) -> None:
    session.execute(
        update(ReconciliationRun)
        .where(ReconciliationRun.id == run_id)
        .values(**values)
        .execution_options(synchronize_session=False)
    )


def latest_runs(session: Session, limit: int) -> list[ReconciliationRun]:
    return list(
        session.scalars(
            select(ReconciliationRun).order_by(ReconciliationRun.run_date.desc()).limit(limit)
        )
    )


def insert_item(session: Session, **values: Any) -> uuid.UUID | None:
    """Open an exception once (``dedupe_key``); ``None`` when it was already recorded."""
    stmt = (
        insert(ReconciliationItem)
        .values(**values)
        .on_conflict_do_nothing(constraint="reconciliation_items_dedupe")
        .returning(ReconciliationItem.id)
    )
    value: uuid.UUID | None = session.execute(stmt).scalar_one_or_none()
    return value


def get_item(
    session: Session, item_id: uuid.UUID, *, lock: bool = False
) -> ReconciliationItem | None:
    row: ReconciliationItem | None = _get(session, ReconciliationItem, item_id, lock=lock)
    return row


def update_item(
    session: Session, item_id: uuid.UUID, values: Mapping[str, Any]
) -> ReconciliationItem:
    row: ReconciliationItem = _update(session, ReconciliationItem, item_id, values)
    return row


def list_items(
    session: Session,
    *,
    status: str | None,
    kind: str | None,
    limit: int,
    after: tuple[dt.datetime, uuid.UUID] | None,
) -> list[ReconciliationItem]:
    stmt = select(ReconciliationItem)
    if status is not None:
        stmt = stmt.where(ReconciliationItem.status == status)
    if kind is not None:
        stmt = stmt.where(ReconciliationItem.kind == kind)
    if after is not None:
        stmt = stmt.where(
            (ReconciliationItem.created_at, ReconciliationItem.id) < after  # type: ignore[operator]
        )
    stmt = stmt.order_by(ReconciliationItem.created_at.desc(), ReconciliationItem.id.desc())
    return list(session.scalars(stmt.limit(limit)))


def open_items_for_payment(session: Session, provider_payment_id: str) -> list[ReconciliationItem]:
    return list(
        session.scalars(
            select(ReconciliationItem)
            .where(
                ReconciliationItem.provider_payment_id == provider_payment_id,
                ReconciliationItem.status == "open",
            )
            .with_for_update()
        )
    )


def count_open_items(session: Session) -> int:
    value: int = (
        session.scalar(
            select(func.count())
            .select_from(ReconciliationItem)
            .where(ReconciliationItem.status == "open")
        )
        or 0
    )
    return value


# --- export ---------------------------------------------------------------------------------------

# Never exported: secrets, wrapped tokens, the encrypted inbox (FR-FEE-027; docs/05 §15.6).
_SECRET_COLUMNS = frozenset(
    {
        "key_secret_ciphertext",
        "access_token_ciphertext",
        "refresh_token_ciphertext",
        "webhook_secret_ciphertext",
        "previous_webhook_secret_ciphertext",
        "route_ciphertext",
    }
)


def export_tables(session: Session) -> list[RecordTable]:
    """Online payment tables of the current school for its full data export (FR-FEE-027): the
    connection without its secrets, intents, provider payments, reconciliation runs and items.
    The webhook inbox (encrypted provider payloads) is left out."""
    out: list[RecordTable] = []
    for model in (
        PaymentConnection,
        PaymentIntent,
        ProviderPayment,
        ReconciliationRun,
        ReconciliationItem,
    ):
        table = model.__table__
        if not isinstance(table, Table):  # pragma: no cover - declarative tables
            raise TypeError(model.__name__)
        secret = [c.name for c in table.columns if c.name in _SECRET_COLUMNS]
        order: tuple[str, ...] | None
        if "created_at" in table.c:
            order = ("created_at", "id")
        elif model is ProviderPayment:
            order = ("first_seen_at", "id")
        else:
            order = ("run_date",)
        out.append(
            dump_table(session, table, name=f"fees_{table.name}", order_by=order, exclude=secret)
        )
    return out


__all__ = [
    "count_events",
    "count_open_items",
    "export_tables",
    "finish_event",
    "get_connection",
    "get_intent",
    "get_item",
    "get_payment",
    "insert_connection",
    "insert_event",
    "insert_intent",
    "insert_item",
    "insert_run",
    "live_connection",
    "next_events",
    "translate_db_error",
    "update_connection",
    "update_intent",
    "update_item",
]
