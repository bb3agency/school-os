"""Celery task of the tuition fee certificate (FM-06; FR-FEE-053). IDs only in arguments.

- ``fees.render_tax_certificate`` (queue ``pdf``, Chromium workers): consumer of the outbox
  event ``fee.tax_certificate.issued``; prints the frozen certificate and stores it as a
  generated fee document. Idempotent; retried with backoff; after the last attempt the
  certificate shows the PDF failed (``POST .../render`` queues it again).

Registered in ``sos_worker.celery_app.TASK_MODULES``.
"""

from __future__ import annotations

import uuid
from typing import Any, Final

from celery import Task, shared_task

from app.core.logging import get_logger
from app.fees import service
from app.ops.service import TenantTask

log = get_logger(__name__)

MAX_RETRIES: Final = 3


@shared_task(
    name=service.TAX_CERTIFICATE_RENDER_TASK,
    base=TenantTask,
    bind=True,
    queue="pdf",
    acks_late=True,
    max_retries=MAX_RETRIES,
)
def render_tax_certificate(
    self: Task[Any, Any], tenant_id: str, event_id: str, payload: dict[str, Any]
) -> str:
    tid = uuid.UUID(str(tenant_id))
    certificate_id = uuid.UUID(str(payload["certificate_id"]))
    try:
        return service.render_tax_certificate_pdf(
            tid, certificate_id, uuid.UUID(str(payload["user_id"]))
        )
    except Exception as exc:  # renderer, storage or database unavailable: retry later
        if self.request.retries >= MAX_RETRIES:
            service.mark_tax_certificate_pdf_failed(tid, certificate_id, "render_failed")
            log.error(
                "fees.tax_certificate_render_failed",
                resource_type="fee_tax_certificate",
                resource_id=certificate_id,
                error_type=type(exc).__name__,
            )
            raise
        raise self.retry(exc=exc, countdown=min(15 * 2**self.request.retries, 300)) from exc


__all__ = ["render_tax_certificate"]
