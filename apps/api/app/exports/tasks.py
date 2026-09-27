"""Celery tasks for exports (FR-EXP-002..004; docs/04 §6). IDs only in arguments and results.

- ``exports.generate`` (queue ``exports``): XLSX/CSV exports; consumer of the outbox event
  ``export.requested``.
- ``exports.render`` (queue ``pdf``, the workers with Chromium): exports that include a PDF;
  consumer of ``export.render_requested``. The whole export runs there so every file of one
  export comes from the same snapshot.
- ``exports.purge_expired`` (beat, daily, queue ``maintenance``): files deleted 7 days after an
  export was ready, one tenant session per school (docs/05 §13).

Both builders are idempotent (the export status gates the work) and retry with backoff; after
the last attempt the export shows ``failed`` and the requester is notified.

Register this module in ``sos_worker.celery_app.TASK_MODULES`` and merge :func:`beat_schedule`
into the beat configuration.
"""

from __future__ import annotations

import uuid
from typing import Any

from celery import Task, shared_task
from celery.schedules import crontab

from app.core.db import context_free_session
from app.core.logging import get_logger
from app.exports import service
from app.tenancy import service as tenancy

log = get_logger(__name__)

MAX_RETRIES = 3
TENANT_STATUSES = ("active", "suspended", "offboarding")


def _uuid(value: object) -> uuid.UUID:
    return uuid.UUID(str(value))


def _run(task: Task[Any, Any], tenant_id: str, payload: dict[str, Any]) -> str:
    tid, export_id = _uuid(tenant_id), _uuid(payload["export_id"])
    try:
        return service.run_export(tid, export_id)
    except Exception as exc:  # storage, renderer or database unavailable: retry later
        if task.request.retries >= MAX_RETRIES:
            service.abandon(tid, export_id, "worker_error")
            raise
        raise task.retry(exc=exc, countdown=min(15 * 2**task.request.retries, 300)) from exc


@shared_task(
    name=service.GENERATE_TASK, bind=True, queue="exports", acks_late=True, max_retries=MAX_RETRIES
)
def generate(self: Task[Any, Any], tenant_id: str, event_id: str, payload: dict[str, Any]) -> str:
    return _run(self, tenant_id, payload)


@shared_task(
    name=service.RENDER_TASK, bind=True, queue="pdf", acks_late=True, max_retries=MAX_RETRIES
)
def render(self: Task[Any, Any], tenant_id: str, event_id: str, payload: dict[str, Any]) -> str:
    return _run(self, tenant_id, payload)


def purge_all() -> dict[str, int]:
    with context_free_session() as session:
        tenant_ids = tenancy.list_tenant_ids(session, TENANT_STATUSES)
    purged = 0
    for tenant_id in tenant_ids:
        purged += service.purge_expired(tenant_id)
    log.info("exports.purge_done", count=purged)
    return {"tenants": len(tenant_ids), "purged": purged}


@shared_task(name=service.PURGE_TASK, queue="maintenance", acks_late=True)
def purge_expired() -> dict[str, int]:
    return purge_all()


def beat_schedule() -> dict[str, dict[str, Any]]:
    """Beat entries for exports (both deployment modes)."""
    return {
        "exports-purge-expired": {
            "task": service.PURGE_TASK,
            "schedule": crontab(minute=50, hour=21),  # 03:20 IST
        },
    }
