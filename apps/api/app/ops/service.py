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
from app.authz.kv import KVUnavailable, kv_store
from app.core import purge as purging
from app.core.db import context_free_session, tenant_session
from app.core.errors import NotFound
from app.core.logging import get_logger
from app.ops import repository as repo
from app.ops.config import load_config
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
    """Claim a batch of events and relay each to its consumer **inside the claiming
    transaction**: when the broker refuses one (``send`` raises), the claim rolls back and the
    whole batch is relayed again on the next run, so no event is lost (audit 2026-10-17 L-05).
    Delivery is at least once, as with ``acks_late`` redelivery; consumers are idempotent."""
    sent = unrouted = 0
    with context_free_session() as s:
        rows = repo.claim_outbox(s, batch)
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
            try:
                send(
                    task,
                    {
                        "tenant_id": str(row["tenant_id"]),
                        "event_id": str(row["id"]),
                        "payload": dict(row["payload"]),
                    },
                )
            except Exception as exc:
                log.error(
                    "ops.outbox.send_failed",
                    resource_id=str(row["id"]),
                    action=row["event_type"],
                    error_type=type(exc).__name__,
                    count=len(rows),
                )
                raise
            sent += 1
    return DispatchResult(len(rows), sent, unrouted)


# --- suspended schools: hold queued work (audit 2026-10-05 A-12) -------------------------------

# How long a held task waits before it checks the school again. Below the broker's visibility
# timeout (2 h, ``sos_worker.celery_app.BROKER_VISIBILITY_TIMEOUT_S``), so a held message is never
# delivered twice.
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
        # Bookkeeping for an AI call already made and billed (audit W3-10); no provider call.
        "knowledge.settle_spend",
    }
)

# Outbox consumers with outside effects (an email, a stored PDF, OCR or LLM calls, a scan, a
# full export). Delivery is at least once (the dispatcher relays inside its claim, L-05, and
# tasks are ``acks_late``), so each of these runs once per ``event_id``: a delivery of an
# event already done or running is skipped (audit 2026-10-17 L-14). A failed run frees the
# event for its retry. Without Valkey the check fails open (the work runs, as before).
DEDUPE_BY_EVENT: Final[frozenset[str]] = frozenset(
    {
        "notifications.send_email",
        "certificates.render",
        "exports.generate",
        "exports.render",
        "circulars.read_version",
        "circulars.draft_notice",
        "circulars.render_notice",
        "extraction.process_batch",
        "documents.scan",
        "knowledge.ingest_version",
        "knowledge.summarise_conversation",
        "admin.tenant_export",
    }
)
EVENT_LEASE_SECONDS: Final = 3 * 3600  # longer than the broker visibility timeout (2 h, YA-02)
EVENT_DONE_SECONDS: Final = 7 * 24 * 3600
_RUNNING: Final = b"running"
_DONE: Final = b"done"


def _event_key(task_name: str, event_id: str) -> str:
    return f"sos:outbox-done:{task_name}:{event_id}"


def claim_event(task_name: str, event_id: str) -> bool:
    """Start the one run of ``task_name`` for ``event_id``; False when it is done or running.
    Fails open (True) when Valkey is unavailable."""
    try:
        return kv_store().set(
            _event_key(task_name, event_id), _RUNNING, ttl_s=EVENT_LEASE_SECONDS, nx=True
        )
    except KVUnavailable:
        log.warning("ops.task.dedupe_unavailable", action=task_name)
        return True


def _event_running(task_name: str, event_id: str) -> bool:
    """Whether a run of ``task_name`` for ``event_id`` holds the lease (not yet done)."""
    try:
        return kv_store().get(_event_key(task_name, event_id)) == _RUNNING
    except KVUnavailable:
        log.warning("ops.task.dedupe_unavailable", action=task_name)
        return False


def _settle_event(task_name: str, event_id: str, *, done: bool) -> None:
    key = _event_key(task_name, event_id)
    try:
        if done:
            kv_store().set(key, _DONE, ttl_s=EVENT_DONE_SECONDS)
        else:
            kv_store().delete(key)
    except KVUnavailable:
        log.warning("ops.task.dedupe_unavailable", action=task_name)


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
            if self.name in DEDUPE_BY_EVENT:
                return self._run_once(args, kwargs)
        return super().__call__(*args, **kwargs)

    def _run_once(self, args: tuple[Any, ...], kwargs: dict[str, Any]) -> Any:
        event_id = str(kwargs["event_id"])
        if not claim_event(self.name, event_id):
            if _event_running(self.name, event_id):
                # Another delivery holds the lease: it is still running, or its worker died
                # (``task_reject_on_worker_lost`` re-queues the message at once, a lost worker's
                # message comes back after the visibility timeout) and the lease outlives it.
                # Look again later instead of dropping the event: it is skipped once done, and
                # runs once a dead run's lease has ended.
                self.apply_async(args=args, kwargs=kwargs, countdown=HOLD_COUNTDOWN_SECONDS)
                log.info("ops.task.deferred", action=self.name, outcome="lease_held")
                return None
            log.info("ops.task.duplicate", action=self.name, outcome="skipped")
            return None
        try:
            result = super().__call__(*args, **kwargs)
        except BaseException:
            _settle_event(self.name, event_id, done=False)  # the retry may run it
            raise
        _settle_event(self.name, event_id, done=True)
        return result


# --- at-most-once consumers (audit 2026-11-09 YM-06) ----------------------------------------

ConsumerClaim = Literal["claimed", "consumed", "missing"]


def claim_event_consumer(tenant_id: uuid.UUID, event_id: uuid.UUID) -> ConsumerClaim:
    """Record, in its own committed transaction, that a consumer is about to act on this outbox
    event outside the database (an e-mail). ``claimed``: go ahead; ``consumed``: an earlier run
    already claimed it, do not act again (at most once; the Valkey lease of
    :class:`TenantTask` ends and fails open, this does not); ``missing``: the event row is gone
    (purged long after delivery) or was never written, so nothing durable can be recorded."""
    with tenant_session(tenant_id) as session:
        return repo.claim_consumer(session, event_id)


def release_event_consumer(tenant_id: uuid.UUID, event_id: uuid.UUID) -> None:
    """Undo :func:`claim_event_consumer` when the outside action certainly did not happen (the
    provider answered with a refusal to retry), so the retry may act."""
    with tenant_session(tenant_id) as session:
        repo.release_consumer(session, event_id)


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
    """Daily: remove expired keys, one tenant session per school (RLS stays on). A school that
    fails is rolled back and logged (ids and error type only); the others still run, and its
    keys are due again on the next run."""
    with context_free_session() as s:
        tenant_ids = tenancy.list_tenant_ids(s, None)
    total = 0
    for tenant_id in tenant_ids:
        try:
            with tenant_session(tenant_id) as ts:
                total += repo.idem_purge_expired(ts)
        except Exception as exc:  # database: this school only, retried next run
            log.warning(
                "ops.idempotency.purge_failed",
                tenant_id=str(tenant_id),
                error_type=type(exc).__name__,
            )
    return total


# Outbox retention runs for these schools: retention is a duty, also while a school is suspended
# or offboarding (as the other retention purges, docs/16 §5.5).
OUTBOX_RETENTION_STATUSES: Final = ("active", "suspended", "offboarding")


@dataclass(frozen=True, slots=True)
class OutboxPurgeResult:
    tenants: int
    purged: int
    failed: int


def purge_delivered_outbox() -> OutboxPurgeResult:
    """Nightly (lead decision D51): delete delivered outbox events older than
    ``outbox.delivered_retention_days`` (``app/ops/config.yaml``), one tenant session per school
    (RLS stays on). Undelivered events are kept. A school that fails is rolled back, logged (ids
    and error type only) and counted; the others still run, and its events are due again on the
    next run."""
    days = load_config().outbox_delivered_retention_days
    with context_free_session() as s:
        tenant_ids = tenancy.list_tenant_ids(s, OUTBOX_RETENTION_STATUSES)
    purged = failed = 0
    for tenant_id in tenant_ids:
        try:
            with tenant_session(tenant_id) as ts:
                purged += repo.purge_delivered_outbox(ts, older_than_days=days)
        except Exception as exc:  # any error: this school only, retried next run
            failed += 1
            log.warning(
                "ops.outbox.purge_failed",
                tenant_id=str(tenant_id),
                error_type=type(exc).__name__,
            )
    if purged or failed:
        log.info("ops.outbox.purged", count=purged, outcome="ok" if not failed else "partial")
    return OutboxPurgeResult(len(tenant_ids), purged, failed)


__all__ = [
    "HOLD_COUNTDOWN_SECONDS",
    "OUTBOX_RETENTION_STATUSES",
    "OUTBOX_ROUTES",
    "RUN_WHILE_SUSPENDED",
    "ConsumerClaim",
    "DispatchResult",
    "JobRun",
    "OutboxPurgeResult",
    "TenantTask",
    "begin_idempotent",
    "claim_event_consumer",
    "complete_idempotent",
    "dispatch_outbox",
    "enqueue_event",
    "fail_job",
    "finish_job",
    "purge_delivered_outbox",
    "purge_idempotency_keys",
    "purge_tenant_data",
    "register_outbox_route",
    "release_event_consumer",
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
