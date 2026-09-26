"""Public audit API (SEC-007, FR-AUD-001..003, Invariant 7).

``record`` appends one event to the current tenant's hash chain inside the caller's
transaction: the event commits with the audited change or not at all. Writes for one tenant are
serialised by ``SELECT ... FOR UPDATE`` on ``audit.chain_heads``; different tenants never block
each other.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterable, Mapping
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.audit import repository
from app.audit.hashing import chain_hash, platform_event_dict, tenant_event_dict
from app.audit.schemas import (
    ZERO_HASH,
    ActorType,
    AuditEvent,
    AuditEventInput,
    JsonValue,
    PlatformActorType,
    PlatformAuditEvent,
    PlatformAuditEventInput,
    VerifyResult,
)
from app.core.ids import new_id

__all__ = [
    "AuditContextError",
    "AuditEvent",
    "PlatformAuditEvent",
    "VerifyResult",
    "record",
    "record_platform",
    "verify_chain",
    "verify_events",
    "verify_platform_chain",
]


class AuditContextError(RuntimeError):
    """``record``/``verify_chain`` was called outside a matching ``tenant_session``."""


def _now() -> datetime:
    # Microsecond precision matches timestamptz, so the hashed value is exactly what is stored.
    return datetime.now(UTC)


def record(
    session: Session,
    *,
    action: str,
    resource_type: str,
    resource_id: uuid.UUID | None = None,
    summary: Mapping[str, Any],
    actor_type: ActorType = "user",
    actor_id: uuid.UUID | None = None,
    request_id: str | None = None,
    ip_hash: bytes | None = None,
) -> AuditEvent:
    """Append an audit event to the current tenant's chain, in the caller's transaction.

    Must run inside ``core.db.tenant_session`` (else ``AuditContextError``). ``summary`` holds
    IDs, field names, counts and codes only; personal keys/values, long strings and long digit
    runs are rejected with ``pydantic.ValidationError`` (a ``ValueError``) before anything is
    written. For ``actor_type="user"`` the actor defaults to the session's ``app.user_id``.
    """
    data = AuditEventInput(
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        summary=dict(summary),
        actor_type=actor_type,
        actor_id=actor_id,
        request_id=request_id,
        ip_hash=ip_hash,
    )
    tenant_id = repository.current_tenant(session)
    if tenant_id is None:
        raise AuditContextError("audit.record must run inside tenant_session (no tenant context)")
    actor = data.actor_id
    if actor is None and data.actor_type == "user":
        actor = repository.current_user_id(session)

    last_seq, prev_hash = repository.lock_head(session, tenant_id)
    row: dict[str, Any] = {
        "id": new_id(),
        "tenant_id": tenant_id,
        "seq": last_seq + 1,
        "occurred_at": _now(),
        "actor_type": data.actor_type,
        "actor_id": actor,
        "action": data.action,
        "resource_type": data.resource_type,
        "resource_id": data.resource_id,
        "summary": data.summary,
        "request_id": data.request_id,
        "ip_hash": data.ip_hash,
        "prev_hash": prev_hash,
    }
    row["hash"] = chain_hash(prev_hash, tenant_event_dict(row))
    repository.insert_event(session, row)
    repository.advance_head(session, tenant_id, row["seq"], row["hash"])
    return AuditEvent.model_validate(row)


def record_platform(
    session: Session,
    *,
    action: str,
    resource_type: str,
    resource_id: uuid.UUID | None = None,
    summary: Mapping[str, Any],
    actor_type: PlatformActorType = "operator",
    actor_id: uuid.UUID | None = None,
    subject_tenant_id: uuid.UUID | None = None,
    request_id: str | None = None,
    ip_hash: bytes | None = None,
) -> PlatformAuditEvent:
    """Append an event to the control-plane chain (``platform_session``; one global head)."""
    data = PlatformAuditEventInput(
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        summary=dict(summary),
        actor_type=actor_type,
        actor_id=actor_id,
        subject_tenant_id=subject_tenant_id,
        request_id=request_id,
        ip_hash=ip_hash,
    )
    last_seq, prev_hash = repository.lock_platform_head(session)
    row: dict[str, Any] = {
        "id": new_id(),
        "seq": last_seq + 1,
        "occurred_at": _now(),
        "actor_type": data.actor_type,
        "actor_id": data.actor_id,
        "action": data.action,
        "resource_type": data.resource_type,
        "resource_id": data.resource_id,
        "subject_tenant_id": data.subject_tenant_id,
        "summary": data.summary,
        "request_id": data.request_id,
        "ip_hash": data.ip_hash,
        "prev_hash": prev_hash,
    }
    row["hash"] = chain_hash(prev_hash, platform_event_dict(row))
    repository.insert_platform_event(session, row)
    repository.advance_platform_head(session, row["seq"], row["hash"])
    return PlatformAuditEvent.model_validate(row)


def _check_row(
    row: Mapping[Any, Any],
    expected_seq: int,
    prev_hash: bytes,
    to_dict: Callable[[Mapping[Any, Any]], Mapping[str, JsonValue]],
) -> tuple[int, str] | None:
    """Return ``(first_bad_seq, reason)`` if ``row`` breaks the chain, else ``None``."""
    seq = int(row["seq"])
    if seq < expected_seq:
        return seq, "duplicate_seq"
    if seq > expected_seq:
        return expected_seq, "seq_gap"
    if bytes(row["prev_hash"]) != prev_hash:
        return seq, "prev_hash_mismatch"
    if chain_hash(prev_hash, to_dict(row)) != bytes(row["hash"]):
        return seq, "hash_mismatch"
    return None


def _check_head(
    head: tuple[int, bytes] | None, last_seq: int, last_hash: bytes
) -> tuple[int, str] | None:
    if head is None:
        return (last_seq, "head_missing") if last_seq else None
    head_seq, head_hash = head
    if head_seq != last_seq:
        return min(head_seq, last_seq) + 1, "head_mismatch"
    if head_seq and head_hash != last_hash:
        return last_seq, "head_mismatch"
    return None


def verify_events(
    rows: Iterable[Mapping[Any, Any]],
    head: tuple[int, bytes] | None,
    to_dict: Callable[[Mapping[Any, Any]], Mapping[str, JsonValue]],
    *,
    check_head: bool = True,
    start_seq: int = 1,
    start_prev_hash: bytes = ZERO_HASH,
) -> VerifyResult:
    """Walk events in ``seq`` order and check contiguity, linkage, hashes and the head.

    With ``check_head=False`` the head is not compared (partial ranges, e.g. one archived day).
    """
    expected = start_seq
    prev = start_prev_hash
    checked = 0
    for row in rows:
        problem = _check_row(row, expected, prev, to_dict)
        if problem is not None:
            return VerifyResult(False, checked, *problem)
        prev = bytes(row["hash"])
        expected += 1
        checked += 1
    if check_head:
        problem = _check_head(head, expected - 1, prev)
        if problem is not None:
            return VerifyResult(False, checked, *problem)
    return VerifyResult(True, checked)


def verify_chain(session: Session, tenant_id: uuid.UUID) -> VerifyResult:
    """Verify one tenant's chain (run in ``tenant_session(tenant_id)``; SEC-007)."""
    if repository.current_tenant(session) != tenant_id:
        raise AuditContextError("verify_chain must run inside tenant_session(tenant_id)")
    head = repository.get_head(session, tenant_id)
    rows = repository.iter_events(session, tenant_id)
    return verify_events(rows, head, tenant_event_dict)


def verify_platform_chain(session: Session) -> VerifyResult:
    """Verify the control-plane chain (run in ``platform_session``)."""
    head = repository.get_platform_head(session)
    rows = repository.iter_platform_events(session)
    return verify_events(rows, head, platform_event_dict)
