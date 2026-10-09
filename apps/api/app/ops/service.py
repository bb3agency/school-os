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
from typing import Any, Final, Literal

from celery import Task
from sqlalchemy.orm import Session

from app.audit.schemas import sanitize_summary
from app.core import purge as purging
from app.core.db import context_free_session, tenant_session
from app.core.errors import NotFound
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


# --- suspended schools: hold queued work (audit 2026-10-05 A-12) -------------------------------

# How long a held task waits before it checks the school again. Below the broker's visibility
# timeout (1 h on Valkey), so a held message is never delivered twice.
HOLD_COUNTDOWN_SECONDS: Final = 600

# Outbox consumers that still run while a school is suspended or offboarding. Each one either
# serves a route on the BR-08 suspended-school allowlist (the owner's full data export), is a
# security response (key rotation re-encryption, the on-demand audit-chain check) or only
# removes data or narrows access (file deletion, index removal, ACL refresh). None calls an
# OCR, LLM or email provider. Everything else is held until the school is active again.
RUN_WHILE_SUSPENDED: Final[frozenset[str]] = frozenset(
    {
        "admin.tenant_export",
        "maintenance.reencrypt_tenant",
        "audit.verify_chain",
        "documents.purge_objects",
        "documents.discard_object",
        "documents.discard_unused_object",
        "knowledge.refresh_acl",
        "knowledge.remove_document",
    }
)

WorkDecision = Literal["run", "hold", "drop"]
_HELD_STATUSES: Final = frozenset({"suspended", "offboarding"})


def school_work_decision(tenant_id: uuid.UUID, task_name: str) -> WorkDecision:
    """Whether queued work of this school may run now (audit 2026-10-05 A-12).

    ``run`` for an active (or provisioning) school and for :data:`RUN_WHILE_SUSPENDED` tasks;
    ``hold`` while the school is suspended or offboarding (the HTTP side answers 403
    ``tenant_suspended`` for the same states, BR-08); ``drop`` once it is deleted. An unknown
    school runs as before (the task fails on its own if the data is missing)."""
    if task_name in RUN_WHILE_SUSPENDED:
        return "run"
    try:
        with tenant_session(tenant_id) as session:
            status = tenancy.get_tenant(session).status
    except NotFound:
        return "run"
    if status in _HELD_STATUSES:
        return "hold"
    if status == "deleted":
        return "drop"
    return "run"


class TenantTask(Task):  # type: ignore[type-arg]
    """Base class of every outbox consumer (``tenant_id``, ``event_id``, ``payload``).

    Before the task body runs (first delivery and every retry), the school's status is checked
    (:func:`school_work_decision`). Work of a suspended or offboarding school is held, not
    dropped: the same message is sent again after :data:`HOLD_COUNTDOWN_SECONDS`, and runs once
    the school is reactivated. Work of a deleted school is dropped. IDs only in logs."""

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        tenant_id = kwargs.get("tenant_id")
        if tenant_id is not None and "event_id" in kwargs:
            decision = school_work_decision(uuid.UUID(str(tenant_id)), self.name)
            if decision != "run":
                if decision == "hold":
                    self.apply_async(args=args, kwargs=kwargs, countdown=HOLD_COUNTDOWN_SECONDS)
                log.info(
                    "ops.task.held" if decision == "hold" else "ops.task.dropped",
                    tenant_id=str(tenant_id),
                    action=self.name,
                    outcome="school_" + decision,
                )
                return None
        return super().__call__(*args, **kwargs)


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


def record_job_progress(session: Session, job_id: uuid.UUID, progress: Mapping[Any, Any]) -> None:
    """Store a running job's progress (IDs, codes and counts only, validated like audit)."""
    repo.update_job(session, job_id, {"progress": sanitize_summary(progress)})


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
    "HOLD_COUNTDOWN_SECONDS",
    "OUTBOX_ROUTES",
    "RUN_WHILE_SUSPENDED",
    "DispatchResult",
    "JobRun",
    "TenantTask",
    "begin_idempotent",
    "complete_idempotent",
    "dispatch_outbox",
    "enqueue_event",
    "fail_job",
    "finish_job",
    "purge_idempotency_keys",
    "purge_tenant_data",
    "register_outbox_route",
    "school_work_decision",
    "start_job",
    "tenant_data_counts",
]


# --- offboarding purge (FR-PLT-005, ADR-0029) ------------------------------------------------
# Job runs, idempotency keys and the outbox.
# Registered with app.tenancy at import; the offboarding job counts them as sos_app and deletes
# them as sos_purger (children before parents) inside the school's tenant_session.
_PURGE = purging.PurgeTables(
    deleted=("ops.job_runs", "ops.idempotency_keys", "ops.outbox"),
)


def tenant_data_counts(session: Session) -> dict[str, int]:
    """Rows of the current school in this module's tables (offboarding inventory)."""
    return _PURGE.count(session)


def purge_tenant_data(session: Session) -> dict[str, int]:
    """Delete the current school's rows of this module (offboarding only: the database allows it
    only as ``sos_purger`` for a school in ``offboarding``)."""
    return _PURGE.delete(session)


tenancy.register_data_owner(
    tenancy.TenantDataOwner(name="ops", count=tenant_data_counts, purge=purge_tenant_data)
)
