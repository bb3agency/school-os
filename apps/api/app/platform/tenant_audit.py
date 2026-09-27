"""Guaranteed school-chain copies of platform actions (FR-AUD-001, CLAUDE.md invariant 7;
ADR-0020, docs/16 §16).

Some control-plane actions must also appear in the school's own audit chain (``tenant.*``
lifecycle events, ``actor_type = 'platform'``). One transaction cannot span the ``sos_platform``
and ``sos_app`` connections, so the copy goes through a transactional outbox:

1. :func:`enqueue` writes a row to ``platform.tenant_audit_outbox`` **in the caller's platform
   transaction**, next to the change and its ``platform.audit_events`` row: all three commit or
   none do. The summary is validated exactly as ``audit.record`` will validate it, so a bad
   summary fails the platform action instead of a later delivery.
2. :func:`deliver_pending` (Celery ``platform.deliver_tenant_audit``, queue ``maintenance``,
   every minute in both deployment modes; also called best-effort right after the action by
   :func:`deliver_now`) locks one undelivered row per school (``FOR UPDATE SKIP LOCKED``, the
   school's oldest first), writes ``audit.record`` in that school's ``tenant_session`` and marks
   the row delivered in the same platform transaction that holds the lock.
3. Exactly once: the row ID travels in the tenant event's summary as ``platform_event_id``.
   Before writing, the deliverer takes a transaction-level advisory lock on that ID in the
   tenant session and looks for an event carrying it; if one exists (a crash after the tenant
   commit but before the platform commit), it only marks the row delivered.

Payloads are IDs, codes and counts only (never names, emails or free text). The control plane
reads nothing from the school except whether that one event already exists.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Final

from pydantic import ValidationError
from sqlalchemy import RowMapping, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.audit import service as audit
from app.audit.schemas import AuditEventInput, SummaryError
from app.core.db import platform_session, tenant_session
from app.core.ids import new_id
from app.core.logging import get_logger
from app.platform import repository as repo
from app.platform.common import Actor, config

log = get_logger(__name__)


def _cfg(key: str) -> int:
    return int(config()["tenant_audit"][key])


EVENT_KEY: Final = "platform_event_id"
"""Summary key that carries the outbox row ID into the school's audit event (dedupe key)."""
MAX_BATCH: Final = 500
INLINE_BATCH: Final = 20
# The tenant event is written after the platform commit, so it is newer than the outbox row;
# the margin only absorbs clock differences between the app (occurred_at) and the database.
_LOOKBACK: Final = dt.timedelta(days=1)

_EXISTING_SQL = text(
    "SELECT 1 FROM audit.events AS e WHERE e.tenant_id = :t AND e.action = :a "
    "AND e.occurred_at >= :since AND e.summary ->> 'platform_event_id' = :k LIMIT 1"
)
_LOCK_SQL = text("SELECT pg_catalog.pg_advisory_xact_lock(:k)")


def enqueue(
    session: Session,
    tenant_id: uuid.UUID,
    actor: Actor,
    action: str,
    summary: Mapping[str, Any],
    *,
    resource_type: str = "tenant",
    resource_id: uuid.UUID | None = None,
) -> uuid.UUID:
    """Queue ``action`` for the school's own chain inside the platform transaction ``session``.

    ``resource_id`` defaults to the school. Returns the platform event ID (dedupe key).
    Raises ``pydantic.ValidationError`` for a summary ``audit.record`` would reject.
    """
    event_id = new_id()
    data = AuditEventInput(
        action=action,
        resource_type=resource_type,
        resource_id=resource_id or tenant_id,
        summary={**summary, EVENT_KEY: str(event_id)},
        actor_type="platform",
        actor_id=actor.operator_id,
        request_id=actor.request_id,
    )
    repo.insert_tenant_audit(
        session,
        {
            "id": event_id,
            "tenant_id": tenant_id,
            "action": data.action,
            "resource_type": data.resource_type,
            "resource_id": data.resource_id,
            "summary": data.summary,
            "actor_id": data.actor_id,
            "request_id": data.request_id,
            "attempts": 0,
        },
    )
    return event_id


@dataclass(frozen=True, slots=True)
class DeliveryResult:
    delivered: int = 0
    already_present: int = 0
    failed: int = 0


def _lock_key(event_id: uuid.UUID) -> int:
    return int.from_bytes(event_id.bytes[8:], "big", signed=True)


def _copy_into_school_chain(row: RowMapping) -> bool:
    """Write the event into the school's chain unless it is already there. True = written."""
    event_id: uuid.UUID = row["id"]
    with tenant_session(row["tenant_id"]) as ts:
        ts.execute(_LOCK_SQL, {"k": _lock_key(event_id)})
        found = ts.execute(
            _EXISTING_SQL,
            {
                "t": row["tenant_id"],
                "a": row["action"],
                "since": row["created_at"] - _LOOKBACK,
                "k": str(event_id),
            },
        ).first()
        if found is not None:
            return False
        audit.record(
            ts,
            action=row["action"],
            resource_type=row["resource_type"],
            resource_id=row["resource_id"],
            summary=dict(row["summary"]),
            actor_type="platform",
            actor_id=row["actor_id"],
            request_id=row["request_id"],
        )
    return True


def _error_code(exc: Exception) -> str:
    if isinstance(exc, SummaryError | ValidationError):
        return "summary_rejected"
    if isinstance(exc, audit.AuditContextError):
        return "no_tenant_context"
    if isinstance(exc, DBAPIError):
        return "db_error"
    return "delivery_failed"


def deliver_pending(
    *, tenant_id: uuid.UUID | None = None, limit: int = MAX_BATCH
) -> DeliveryResult:
    """Deliver up to ``limit`` undelivered copies (one school, or all), oldest first per school.

    A failing row stays undelivered (``attempts``/``last_error`` recorded) and blocks only its
    own school's later rows until it succeeds, so per-school order is never broken.
    """
    delivered = present = failed = 0
    skip: list[uuid.UUID] = []
    for _ in range(max(0, min(limit, MAX_BATCH))):
        with platform_session() as ps:
            row = repo.claim_tenant_audit(ps, tenant_id=tenant_id, skip=skip)
            if row is None:
                break
            try:
                written = _copy_into_school_chain(row)
            except Exception as exc:
                code = _error_code(exc)
                attempts = repo.mark_tenant_audit_failed(ps, row["id"], code)
                skip.append(row["id"])
                failed += 1
                log.error(
                    "platform.tenant_audit.failed"
                    if attempts < _cfg("stuck_after_attempts")
                    else "platform.tenant_audit.stuck",
                    tenant_id=str(row["tenant_id"]),
                    resource_id=str(row["id"]),
                    action=row["action"],
                    error_code=code,
                    error_type=type(exc).__name__,
                    attempt=attempts,
                )
                continue
            repo.mark_tenant_audit_delivered(ps, row["id"])
        if written:
            delivered += 1
        else:
            present += 1
            log.info(
                "platform.tenant_audit.already_present",
                tenant_id=str(row["tenant_id"]),
                resource_id=str(row["id"]),
                action=row["action"],
            )
    return DeliveryResult(delivered, present, failed)


def deliver_now(tenant_id: uuid.UUID) -> None:
    """Best-effort delivery right after the platform action committed (the worker guarantees it).

    Keeps the school's audit log current in the common case; any error is logged and left to
    the scheduled task.
    """
    try:
        deliver_pending(tenant_id=tenant_id, limit=INLINE_BATCH)
    except Exception as exc:
        log.warning(
            "platform.tenant_audit.inline_failed",
            tenant_id=str(tenant_id),
            error_type=type(exc).__name__,
        )


def check_backlog(*, now: dt.datetime | None = None) -> int:
    """Undelivered copies; logs ``platform.tenant_audit.backlog`` (alert, docs/16 §17) when the
    oldest has waited longer than ``tenant_audit.backlog_alert_minutes`` (billing.yaml)."""
    with platform_session() as ps:
        count, oldest = repo.pending_tenant_audit(ps)
    current = now or dt.datetime.now(dt.UTC)
    limit = dt.timedelta(minutes=_cfg("backlog_alert_minutes"))
    if count and oldest is not None and current - oldest > limit:
        log.error("platform.tenant_audit.backlog", count=count, outcome="stale")
    return count
