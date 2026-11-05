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

4. Retention (lead decision D51 for the control plane): :func:`purge_delivered` (Celery
   ``platform.purge_tenant_audit``, nightly, both modes) deletes copies delivered more than
   ``tenant_audit.delivered_retention_days`` ago. Only delivered rows go (the database refuses
   the others), and the deliverer only ever claims undelivered rows, so a purge can never cause
   a redelivery or a duplicate in a school chain. Lead decision D68: ``delivered_at`` is set
   once (trigger, migration 0079), and a delivered copy is deleted only after its
   ``platform_event_id`` is confirmed in that school's chain; the others are kept and counted.

5. Tenant-side reactions (lead decision D62): after writing a copy, the deliverer runs the hooks
   registered for its action (``audit.on_platform_event``) in the same tenant transaction, so
   they happen exactly once with the copy. ``tenant.owner_invited`` (the school owner's invite at
   provisioning) queues the owner's invitation email this way, and ``tenant.owner_invite_resent``
   (an operator's re-send) queues it again. Actions in :data:`HOOKED_ACTIONS`
   fail closed: a process without their hook leaves the copy undelivered (``hook_missing``) for
   one that has it (the worker), instead of delivering it without its reaction.

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
from app.platform.common import SYSTEM, Actor, audit_platform, config

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

OWNER_INVITED: Final = "tenant.owner_invited"
"""School-chain event for the owner's invite at provisioning (resource: the owner's user ID;
summary: ``membership_id``, ``owner_role_assigned``). Its hook queues the invitation email."""
OWNER_INVITE_RESENT: Final = "tenant.owner_invite_resent"
"""School-chain event for an operator's ``owner-invite:resend`` (resource: the school; no
summary). Its hook emails the school's invited owner again (staff invitation cool-down)."""
HOOKED_ACTIONS: Final = frozenset({OWNER_INVITED, OWNER_INVITE_RESENT})
"""Actions that must not be delivered by a process without their tenant-side hook."""


class HookMissing(RuntimeError):
    """This process has no hook for an action in :data:`HOOKED_ACTIONS`; another will deliver."""


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


def enqueue_owner_invited(
    session: Session,
    tenant_id: uuid.UUID,
    actor: Actor,
    *,
    user_id: uuid.UUID,
    membership_id: uuid.UUID,
    owner_role_assigned: bool,
) -> uuid.UUID:
    """Queue :data:`OWNER_INVITED` in the platform transaction that created the owner's invite
    (shared and dedicated provisioning). IDs and a flag only: the address is read tenant-side
    when the email is sent."""
    return enqueue(
        session,
        tenant_id,
        actor,
        OWNER_INVITED,
        {"membership_id": str(membership_id), "owner_role_assigned": owner_role_assigned},
        resource_type="user",
        resource_id=user_id,
    )


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
    hooks = audit.platform_event_hooks(row["action"])
    if row["action"] in HOOKED_ACTIONS and not hooks:
        raise HookMissing(row["action"])
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
        for hook in hooks:
            hook(ts, row["tenant_id"], row["resource_id"], dict(row["summary"]))
    return True


def _error_code(exc: Exception) -> str:
    if isinstance(exc, HookMissing):
        return "hook_missing"
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


def retention_days(raw: Mapping[str, Any] | None = None) -> int:
    """``tenant_audit.delivered_retention_days`` from ``billing.yaml`` (at least 1)."""
    section = (raw if raw is not None else config())["tenant_audit"]
    days = int(section["delivered_retention_days"])
    if days < 1:
        raise ValueError("tenant_audit.delivered_retention_days must be at least 1")
    return days


@dataclass(frozen=True, slots=True)
class PurgeResult:
    purged: int = 0
    kept_unconfirmed: int = 0


PURGE_BATCH: Final = 500

_CONFIRMED_SQL = text(
    "SELECT e.summary ->> 'platform_event_id' FROM audit.events AS e "
    "WHERE e.tenant_id = :t AND e.actor_type = 'platform' AND e.occurred_at >= :since "
    "AND e.summary ->> 'platform_event_id' = ANY(CAST(:ks AS text[]))"
)


def _confirmed_in_school_chain(tenant_id: uuid.UUID, rows: list[RowMapping]) -> set[uuid.UUID]:
    """The copies among ``rows`` whose ``platform_event_id`` is in the school's chain (D68).

    Read-only, in the school's own ``tenant_session`` (RLS applies). The chain is append-only,
    so a copy confirmed here stays confirmed until it is deleted.
    """
    since = min(r["created_at"] for r in rows) - _LOOKBACK
    with tenant_session(tenant_id) as ts:
        keys: set[str] = set(
            ts.execute(
                _CONFIRMED_SQL,
                {"t": tenant_id, "since": since, "ks": [str(r["id"]) for r in rows]},
            ).scalars()
        )
    return {r["id"] for r in rows if str(r["id"]) in keys}


def _confirm_school(tenant_id: uuid.UUID, days: int) -> tuple[list[uuid.UUID], int]:
    """(copies confirmed in the school's chain, copies kept unconfirmed), in batches by seq."""
    confirmed: list[uuid.UUID] = []
    kept = 0
    after = 0
    while True:
        with platform_session() as ps:
            rows = repo.purgeable_tenant_audit_batch(
                ps, tenant_id, older_than_days=days, after_seq=after, limit=PURGE_BATCH
            )
        if not rows:
            break
        after = int(rows[-1]["seq"])
        try:
            ok = _confirmed_in_school_chain(tenant_id, rows)
        except Exception as exc:  # the school could not be read: keep every copy, count them
            log.error(
                "platform.tenant_audit.confirm_failed",
                tenant_id=str(tenant_id),
                count=len(rows),
                error_type=type(exc).__name__,
            )
            ok = set()
        confirmed.extend(r["id"] for r in rows if r["id"] in ok)
        kept += len(rows) - len(ok)
        if len(rows) < PURGE_BATCH:
            break
    return confirmed, kept


def purge_delivered() -> PurgeResult:
    """Nightly (D51 for the control plane; D68): delete copies delivered more than
    ``tenant_audit.delivered_retention_days`` ago **whose ``platform_event_id`` is confirmed in
    that school's own audit chain**.

    Per school, in batches of :data:`PURGE_BATCH` by ``seq``: candidates are read in the
    platform session and looked up (read-only) in the school's ``tenant_session``. A copy that
    cannot be confirmed (marked delivered but missing from the chain, or the school could not be
    read) is kept and counted; the log line ``platform.tenant_audit.kept_unconfirmed`` carries
    the school id and the count only. Undelivered copies are never candidates. The confirmed
    copies are deleted in one platform transaction together with ``tenant_audit.copies_purged``
    (system; ``count``, ``kept_unconfirmed``, ``retention_days``).
    """
    days = retention_days()
    with platform_session() as ps:
        tenants = repo.tenants_with_purgeable_tenant_audit(ps, older_than_days=days)
    confirmed: list[uuid.UUID] = []
    kept = 0
    for tenant_id in tenants:
        ids, school_kept = _confirm_school(tenant_id, days)
        confirmed.extend(ids)
        kept += school_kept
        if school_kept:
            log.warning(
                "platform.tenant_audit.kept_unconfirmed",
                tenant_id=str(tenant_id),
                count=school_kept,
                outcome="kept",
            )
    count = 0
    with platform_session() as ps:
        for i in range(0, len(confirmed), PURGE_BATCH):
            count += repo.purge_delivered_tenant_audit(
                ps, confirmed[i : i + PURGE_BATCH], older_than_days=days
            )
        if count or kept:
            audit_platform(
                ps,
                SYSTEM,
                "tenant_audit.copies_purged",
                "tenant_audit_outbox",
                None,
                {"count": count, "kept_unconfirmed": kept, "retention_days": days},
            )
    if count or kept:
        log.info("platform.tenant_audit.purged", count=count, outcome="ok")
    return PurgeResult(count, kept)
