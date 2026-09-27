"""Celery task for register-photo extraction (US-402, FR-IMP-020..024). IDs only in arguments.

- ``extraction.process_batch`` (queue ``ocr``): consumer of the outbox event
  ``extraction.batch.created``. Transient provider failures retry with backoff; after the last
  attempt the batch is ``failed`` (``provider_unavailable``). A deployment without a provider
  fails the batch (``provider_not_configured``) and the task raises, so operators see it.

Registered in ``sos_worker.celery_app.TASK_MODULES``; ``extraction.*`` routes to queue ``ocr``.
"""

from __future__ import annotations

import uuid
from typing import Any

from celery import Task, shared_task

from app.core.logging import get_logger
from app.extraction import service
from app.extraction.providers import ExtractionUnavailable

log = get_logger(__name__)

MAX_RETRIES = 5


@shared_task(
    name=service.PROCESS_TASK,
    bind=True,
    queue="ocr",
    acks_late=True,
    max_retries=MAX_RETRIES,
    ignore_result=True,
)
def process_batch(
    self: Task[Any, Any], tenant_id: str, event_id: str, payload: dict[str, Any]
) -> str:
    tid = uuid.UUID(str(tenant_id))
    batch_id = uuid.UUID(str(payload["batch_id"]))
    try:
        return service.process_batch(tid, batch_id)
    except ExtractionUnavailable as exc:
        if self.request.retries >= MAX_RETRIES:
            service.fail_batch(tid, batch_id, exc.code)
            return "failed"
        log.warning(
            "extraction.provider.retry",
            error_code=exc.code,
            resource_type="extraction_batch",
            resource_id=batch_id,
        )
        raise self.retry(exc=exc, countdown=min(30 * 2**self.request.retries, 900)) from exc
