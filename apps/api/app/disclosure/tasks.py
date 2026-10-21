"""Celery tasks for the disclosure (ADR-0051; FR-DISC-011; docs/04 §6). IDs only in arguments.

- ``disclosure.render_files`` (queue ``pdf``, Chromium workers): consumer of the outbox event
  ``disclosure.render_requested``; the HTML file for the school's website and the A4 PDF of one
  snapshot. Idempotent (only ``queued`` files are made); retried with backoff; after the last
  attempt the snapshot shows the files failed.

Registered in ``sos_worker.celery_app.TASK_MODULES``.
"""

from __future__ import annotations

import uuid
from typing import Any, Final

from celery import Task, shared_task

from app.disclosure import service
from app.ops.service import TenantTask

MAX_RETRIES: Final = 3


@shared_task(
    name=service.RENDER_TASK,
    base=TenantTask,
    bind=True,
    queue="pdf",
    acks_late=True,
    max_retries=MAX_RETRIES,
    ignore_result=True,
)
def render_files(
    self: Task[Any, Any], tenant_id: str, event_id: str, payload: dict[str, Any]
) -> str:
    tid, snapshot_id = uuid.UUID(tenant_id), uuid.UUID(str(payload["snapshot_id"]))
    try:
        return service.render_files(tid, snapshot_id)
    except Exception as exc:
        if self.request.retries >= MAX_RETRIES:
            service.abandon_render(tid, snapshot_id, "worker_error")
            raise
        raise self.retry(exc=exc, countdown=min(30 * 2**self.request.retries, 600)) from exc


__all__ = ["render_files"]
