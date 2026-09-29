"""Celery tasks for the admin module (FR-ADM-001; docs/04 §6). IDs only in arguments and results.

- ``admin.tenant_export`` (queue ``exports``): builds the school's full data export; consumer of
  the outbox event ``admin.tenant_export.requested``. Idempotent (the export status gates the
  work), retried with backoff; after the last attempt the export shows ``failed`` and the
  requester is notified. Longer time limits than the default (``app/admin/config.yaml``).
- ``admin.purge_tenant_exports`` (beat, hourly, queue ``maintenance``): archives are deleted
  24 hours after they were ready, one tenant session per school (suspended and offboarding
  schools included: retention purges keep running, docs/16 §5.5); a failing school does not
  stop the others (logged with ids only, counted in ``failed``, retried on the next run).

Register this module in ``sos_worker.celery_app.TASK_MODULES`` and merge :func:`beat_schedule`
into the beat configuration.
"""

from __future__ import annotations

import uuid
from typing import Any

from celery import Task, shared_task
from celery.schedules import crontab

from app.admin import service
from app.admin.config import load_config
from app.core.db import context_free_session
from app.core.logging import get_logger
from app.tenancy import service as tenancy

log = get_logger(__name__)

MAX_RETRIES = 3
TENANT_STATUSES = ("active", "suspended", "offboarding")
_LIMITS = load_config().tenant_export


def _uuid(value: object) -> uuid.UUID:
    return uuid.UUID(str(value))


@shared_task(
    name=service.EXPORT_TASK,
    bind=True,
    queue="exports",
    acks_late=True,
    max_retries=MAX_RETRIES,
    soft_time_limit=_LIMITS.task_soft_time_limit_s,
    time_limit=_LIMITS.task_time_limit_s,
)
def tenant_export(
    self: Task[Any, Any], tenant_id: str, event_id: str, payload: dict[str, Any]
) -> str:
    tid, export_id = _uuid(tenant_id), _uuid(payload["tenant_export_id"])
    try:
        return service.run_export(tid, export_id)
    except Exception as exc:  # storage or database unavailable: retry later
        if self.request.retries >= MAX_RETRIES:
            service.abandon(tid, export_id, "worker_error")
            raise
        raise self.retry(exc=exc, countdown=min(30 * 2**self.request.retries, 600)) from exc


def purge_all() -> dict[str, int]:
    """Purge every school's expired archives, each school on its own: a school that fails is
    logged (ids and error type only) and counted, the others still run, and its archives are
    due again on the next run."""
    with context_free_session() as session:
        tenant_ids = tenancy.list_tenant_ids(session, TENANT_STATUSES)
    purged = failed = 0
    for tenant_id in tenant_ids:
        try:
            purged += service.purge_expired(tenant_id)
        except Exception as exc:  # storage or database: this school only, retried next run
            failed += 1
            log.warning(
                "admin.export.purge_failed", tenant_id=tenant_id, error_type=type(exc).__name__
            )
    log.info("admin.export.purge_done", count=purged, failed=failed)
    return {"tenants": len(tenant_ids), "purged": purged, "failed": failed}


@shared_task(name=service.PURGE_TASK, queue="maintenance", acks_late=True)
def purge_tenant_exports() -> dict[str, int]:
    return purge_all()


def beat_schedule() -> dict[str, dict[str, Any]]:
    """Beat entries for the admin module (both deployment modes)."""
    return {
        # FR-ADM-001: the archive link is valid 24 hours; hourly keeps it within the hour.
        "admin-purge-tenant-exports": {
            "task": service.PURGE_TASK,
            "schedule": crontab(minute=35),
        },
    }
