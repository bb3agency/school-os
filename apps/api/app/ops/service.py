"""Tenant-side operations: transactional outbox, job runs, idempotency keys (FR-OPS-004).

- ``enqueue_event`` writes a domain event in the caller's ``tenant_session`` (same transaction
  as the change). Payloads carry IDs, codes and counts only (validated like audit summaries).
- ``dispatch_outbox`` is the only cross-tenant consumer: it calls the allowlisted definer
  ``ops.claim_outbox`` (``FOR UPDATE SKIP LOCKED``, marks rows dispatched) and sends each event
  to its Celery task by ``event_type``; workers then open ``tenant_session(tenant_id)``.
- Job runs are keyed by ``(tenant_id, idempotency_key)``: a rerun resumes, never duplicates.
- ``begin_idempotent`` / ``complete_idempotent`` implement docs/09 §2 on
  ``ops.idempotency_keys`` for tenant POSTs.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.audit.schemas import sanitize_summary
from app.core.db import context_free_session, tenant_session
from app.core.logging import get_logger
from app.ops import repository as repo
from app.ops.idempotency import IdempotencyRecord, check_key, resolve_existing
from app.tenancy import service as tenancy

log = get_logger(__name__)

MAX_ATTEMPTS = 5

# event_type -> Celery task name. Modules register their consumers at import time.
OUTBOX_ROUTES: dict[str, str] = {}


def register_outbox_route(event_type: str, task_name: str) -> None:
    OUTBOX_ROUTES[event_type] = task_name


def enqueue_event(session: Session, event_type: str, payload: Mapping[Any, Any]) -> uuid.UUID:
    """Add an event to the outbox inside the current tenant transaction (IDs only)."""
    clean = sanitize_summary(payload)
    return repo.insert_outbox(session, repo.current_tenant(session), event_type, clean)


@dataclass(frozen=True, slots=True)
class DispatchResult:
    claimed: int
    sent: int
    unrouted: int


SendTask = Callable[[str, dict[str, Any]], None]


def dispatch_outbox(send: SendTask, *, batch: int = 100) -> DispatchResult:
    with context_free_session() as s:
        rows = repo.claim_outbox(s, batch)
    sent = unrouted = 0
    for row in rows:
        task = OUTBOX_ROUTES.get(row["event_type"])
        if task is None:
            unrouted += 1
            log.error(
                "ops.outbox.unrouted",
                tenant_id=str(row["tenant_id"]),
                resource_id=str(row["id"]),
                action=row["event_type"],
            )
            continue
        send(
            task,
            {
                "tenant_id": str(row["tenant_id"]),
                "event_id": str(row["id"]),
                "payload": dict(row["payload"]),
            },
        )
        sent += 1
    return DispatchResult(len(rows), sent, unrouted)


# --- job runs ---------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class JobRun:
    id: uuid.UUID
    tenant_id: uuid.UUID
    task_name: str
    idempotency_key: str
    status: str
    attempts: int
    created: bool


def _job(row: Mapping[Any, Any], created: bool) -> JobRun:
    return JobRun(
        id=row["id"],
        tenant_id=row["tenant_id"],
        task_name=row["task_name"],
        idempotency_key=row["idempotency_key"],
        status=row["status"],
        attempts=int(row["attempts"]),
        created=created,
    )


def start_job(
    session: Session, *, task_name: str, idempotency_key: str, created_by: uuid.UUID | None = None
) -> JobRun:
    """Create the run, or return the existing one (bumping ``attempts`` when it is resumed)."""
    tenant_id = repo.current_tenant(session)
    row, created = repo.start_job(
        session,
        tenant_id=tenant_id,
        task_name=task_name,
        idempotency_key=idempotency_key,
        created_by=created_by,
    )
    if not created and row["status"] in ("pending", "running", "failed"):
        row = repo.update_job(
            session, row["id"], {"status": "running", "attempts": row["attempts"] + 1}
        )
    return _job(row, created)


def finish_job(
    session: Session, job_id: uuid.UUID, progress: Mapping[Any, Any] | None = None
) -> None:
    repo.update_job(
        session,
        job_id,
        {
            "status": "succeeded",
            "progress": sanitize_summary(progress or {}),
            "finished_at": datetime.now(UTC),
        },
    )


def fail_job(session: Session, job_id: uuid.UUID, error_code: str) -> str:
    row = repo.get_job(session, job_id)
    if row is None:
        raise LookupError("job not found")
    status = "dead" if int(row["attempts"]) >= MAX_ATTEMPTS else "failed"
    repo.update_job(
        session,
        job_id,
        {"status": status, "error": error_code[:200], "finished_at": datetime.now(UTC)},
    )
    return status


# --- idempotency (tenant API) -----------------------------------------------------------------


def begin_idempotent(
    session: Session,
    *,
    user_id: uuid.UUID,
    key: str,
    method: str,
    route: str,
    request_sha256: str,
) -> IdempotencyRecord | None:
    """``None`` = proceed (claimed); a record = replay it. Raises 422/409 per docs/09 §2."""
    check_key(key)
    tenant_id = repo.current_tenant(session)
    claimed = repo.idem_claim(
        session,
        {
            "tenant_id": tenant_id,
            "user_id": user_id,
            "key": key,
            "method": method,
            "route": route,
            "request_sha256": bytes.fromhex(request_sha256),
            "status": "in_progress",
        },
    )
    if claimed:
        return None
    row = repo.idem_get(session, tenant_id, user_id, key)
    if row is None:  # pragma: no cover - expired and purged in between
        return None
    existing = IdempotencyRecord(
        request_sha256=bytes(row["request_sha256"]).hex(),
        state=row["status"],
        status_code=row["response_status"],
        resource_type=row["resource_type"],
        resource_id=str(row["resource_id"]) if row["resource_id"] else None,
        location=row["location"],
    )
    return resolve_existing(existing, request_sha256)


def complete_idempotent(
    session: Session,
    *,
    user_id: uuid.UUID,
    key: str,
    status_code: int,
    resource_type: str | None = None,
    resource_id: uuid.UUID | None = None,
    location: str | None = None,
) -> None:
    repo.idem_update(
        session,
        repo.current_tenant(session),
        user_id,
        key,
        {
            "status": "completed",
            "response_status": status_code,
            "resource_type": resource_type,
            "resource_id": resource_id,
            "location": location,
        },
    )


def purge_idempotency_keys() -> int:
    """Daily: remove expired keys, one tenant session per school (RLS stays on)."""
    with context_free_session() as s:
        tenant_ids = tenancy.list_tenant_ids(s, None)
    total = 0
    for tenant_id in tenant_ids:
        with tenant_session(tenant_id) as ts:
            total += repo.idem_purge_expired(ts)
    return total


__all__ = [
    "OUTBOX_ROUTES",
    "DispatchResult",
    "JobRun",
    "begin_idempotent",
    "complete_idempotent",
    "dispatch_outbox",
    "enqueue_event",
    "fail_job",
    "finish_job",
    "purge_idempotency_keys",
    "register_outbox_route",
    "start_job",
]
