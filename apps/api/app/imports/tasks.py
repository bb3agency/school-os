"""Celery tasks for imports (FR-IMP-001..007). IDs, codes and counts only in arguments/results.

- ``imports.parse`` / ``imports.validate`` / ``imports.commit`` (queue ``ingest``): consumers of
  the outbox events ``import.parse_requested`` / ``import.validate_requested`` /
  ``import.commit_requested``; each is idempotent (the batch status gates the work).
- ``imports.purge_raw_files`` (beat, daily, queue ``maintenance``): raw files 90 days after
  commit, one tenant session per school (FR-IMP-007).

Register this module in ``sos_worker.celery_app.TASK_MODULES`` and merge
:func:`beat_schedule` into the beat configuration.
"""

from __future__ import annotations

import uuid
from typing import Any

from celery import Task, shared_task
from celery.schedules import crontab

from app.core.db import context_free_session
from app.core.logging import get_logger
from app.imports import service
from app.tenancy import service as tenancy

log = get_logger(__name__)

MAX_RETRIES = 3
TENANT_STATUSES = ("active", "suspended", "offboarding")


def _uuid(value: object) -> uuid.UUID:
    return uuid.UUID(str(value))


def _optional(value: object) -> uuid.UUID | None:
    return _uuid(value) if value else None


def _args(tenant_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "tenant_id": _uuid(tenant_id),
        "batch_id": _uuid(payload["batch_id"]),
        "job_id": _optional(payload.get("job_id")),
        "user_id": _uuid(payload["user_id"]),
        "membership_id": _uuid(payload["membership_id"]),
    }


def _retry(task: Task[Any, Any], exc: Exception, a: dict[str, Any], *, status: str) -> Exception:
    """Retry with backoff; after the last attempt the batch shows ``worker_error``."""
    if task.request.retries >= MAX_RETRIES:
        service.abandon(
            a["tenant_id"], a["batch_id"], a["job_id"], a["user_id"], "worker_error", status=status
        )
        return exc
    return task.retry(exc=exc, countdown=min(15 * 2**task.request.retries, 300))


@shared_task(
    name=service.PARSE_TASK, bind=True, queue="ingest", acks_late=True, max_retries=MAX_RETRIES
)
def parse(self: Task[Any, Any], tenant_id: str, event_id: str, payload: dict[str, Any]) -> str:
    a = _args(tenant_id, payload)
    try:
        return service.run_parse(
            a["tenant_id"],
            a["batch_id"],
            job_id=a["job_id"],
            user_id=a["user_id"],
            membership_id=a["membership_id"],
        )
    except Exception as exc:  # storage or database unavailable: retry later
        raise _retry(self, exc, a, status="failed") from exc


@shared_task(
    name=service.VALIDATE_TASK, bind=True, queue="ingest", acks_late=True, max_retries=MAX_RETRIES
)
def validate(self: Task[Any, Any], tenant_id: str, event_id: str, payload: dict[str, Any]) -> str:
    a = _args(tenant_id, payload)
    try:
        return service.run_validate(
            a["tenant_id"],
            a["batch_id"],
            job_id=a["job_id"],
            user_id=a["user_id"],
            membership_id=a["membership_id"],
        )
    except Exception as exc:
        raise _retry(self, exc, a, status="parsed") from exc


@shared_task(name=service.COMMIT_TASK, queue="ingest", acks_late=True)
def commit(tenant_id: str, event_id: str, payload: dict[str, Any]) -> str:
    """Not retried automatically: a failed commit rolls back and returns the batch to
    ``validated`` with the reason; the office can try again."""
    a = _args(tenant_id, payload)
    return service.run_commit(
        a["tenant_id"],
        a["batch_id"],
        job_id=a["job_id"],
        user_id=a["user_id"],
        membership_id=a["membership_id"],
        skip_error_rows=bool(payload.get("skip_error_rows", False)),
    )


def purge_all() -> dict[str, int]:
    with context_free_session() as session:
        tenant_ids = tenancy.list_tenant_ids(session, TENANT_STATUSES)
    purged = 0
    for tenant_id in tenant_ids:
        purged += service.purge_raw_files(tenant_id)
    log.info("imports.raw_files.purge_done", count=purged)
    return {"tenants": len(tenant_ids), "purged": purged}


@shared_task(name=service.PURGE_TASK, queue="maintenance", acks_late=True)
def purge_raw_files() -> dict[str, int]:
    return purge_all()


def beat_schedule() -> dict[str, dict[str, Any]]:
    """Beat entries for imports (both deployment modes)."""
    return {
        "imports-purge-raw-files": {
            "task": service.PURGE_TASK,
            "schedule": crontab(minute=35, hour=21),  # 03:05 IST
        },
    }
