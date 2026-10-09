"""Celery tasks for certificates (FR-CERT-010; docs/04 §6). IDs only in arguments and results.

- ``certificates.render`` (queue ``pdf``, the workers with Chromium): prints an issued
  certificate to PDF and stores it as a document; consumer of the outbox event
  ``certificate.render_requested``. Idempotent (a stored PDF is left alone) and retried with
  backoff; after the last attempt the certificate shows the PDF failed (retry from the screen).

Register this module in ``sos_worker.celery_app.TASK_MODULES`` and route the task to ``pdf``.
"""

from __future__ import annotations

import uuid
from typing import Any

from celery import Task, shared_task

from app.certificates import service
from app.core.logging import get_logger
from app.ops.service import TenantTask

log = get_logger(__name__)

MAX_RETRIES = 3


def _uuid(value: object) -> uuid.UUID:
    return uuid.UUID(str(value))


@shared_task(
    name=service.RENDER_TASK,
    base=TenantTask,
    bind=True,
    queue="pdf",
    acks_late=True,
    max_retries=MAX_RETRIES,
)
def render(self: Task[Any, Any], tenant_id: str, event_id: str, payload: dict[str, Any]) -> str:
    tid = _uuid(tenant_id)
    certificate_id = _uuid(payload["certificate_id"])
    try:
        return service.render_pdf(tid, certificate_id, _uuid(payload["user_id"]))
    except Exception as exc:  # renderer, storage or database unavailable: retry later
        if self.request.retries >= MAX_RETRIES:
            service.mark_pdf_failed(tid, certificate_id, "render_failed")
            log.error(
                "certificates.render_failed",
                resource_type="certificate",
                resource_id=certificate_id,
                error_type=type(exc).__name__,
            )
            raise
        raise self.retry(exc=exc, countdown=min(15 * 2**self.request.retries, 300)) from exc
