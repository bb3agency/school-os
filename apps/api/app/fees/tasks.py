"""Celery tasks for fees (FR-FEE-009, FR-FEE-011; docs/04 §6). IDs only in arguments.

- ``fees.render_receipt`` (queue ``pdf``, Chromium workers): consumer of the outbox event
  ``fee.receipt.issued``; prints the frozen receipt and stores it as a ``fee_receipt`` document.
  Idempotent; retried with backoff; after the last attempt the receipt shows the PDF failed.
- ``fees.post_late_fees`` (beat, 00:30 IST, queue ``maintenance``): per active school with fees
  on, posts each late fee once (keyed; FR-FEE-009). One tenant session per school; one failing
  school does not stop the others.

Register this module in ``sos_worker.celery_app.TASK_MODULES`` and merge :func:`beat_schedule`.
"""

from __future__ import annotations

import uuid
from typing import Any, Final

from celery import Task, shared_task
from celery.schedules import crontab
from sqlalchemy.exc import SQLAlchemyError

from app.core.db import context_free_session, tenant_session
from app.core.errors import DomainError
from app.core.logging import get_logger
from app.fees import service
from app.ops.service import TenantTask
from app.tenancy import service as tenancy

log = get_logger(__name__)

MAX_RETRIES: Final = 3
LATE_FEES_TASK: Final = "fees.post_late_fees"
TENANT_STATUSES: Final = ("active",)


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
def render_receipt(
    self: Task[Any, Any], tenant_id: str, event_id: str, payload: dict[str, Any]
) -> str:
    tid = _uuid(tenant_id)
    receipt_id = _uuid(payload["receipt_id"])
    try:
        return service.render_pdf(tid, receipt_id, _uuid(payload["user_id"]))
    except Exception as exc:  # renderer, storage or database unavailable: retry later
        if self.request.retries >= MAX_RETRIES:
            service.mark_pdf_failed(tid, receipt_id, "render_failed")
            log.error(
                "fees.render_failed",
                resource_type="fee_receipt",
                resource_id=receipt_id,
                error_type=type(exc).__name__,
            )
            raise
        raise self.retry(exc=exc, countdown=min(15 * 2**self.request.retries, 300)) from exc


def post_all() -> dict[str, int]:
    with context_free_session() as session:
        tenant_ids = tenancy.list_tenant_ids(session, TENANT_STATUSES)
    totals = {"tenants": len(tenant_ids), "posted": 0, "failed": 0}
    for tenant_id in tenant_ids:
        try:
            with tenant_session(tenant_id) as session:
                if not service.enabled(session, tenant_id):
                    continue
                totals["posted"] += service.post_late_fees(session)["posted"]
        except (SQLAlchemyError, DomainError) as exc:
            totals["failed"] += 1
            log.warning("fees.late_fees_failed", tenant_id=tenant_id, error_type=type(exc).__name__)
    log.info("fees.late_fees_posted", count=totals["posted"])
    return totals


@shared_task(name=LATE_FEES_TASK, queue="maintenance", acks_late=True, ignore_result=True)
def post_late_fees() -> dict[str, int]:
    return post_all()


def beat_schedule() -> dict[str, dict[str, Any]]:
    """Beat entries for fees (both deployment modes). The beat runs in UTC: 19:00 UTC = 00:30
    IST of the next day."""
    return {
        "fees-post-late-fees": {"task": LATE_FEES_TASK, "schedule": crontab(hour=19, minute=0)},
    }


__all__ = ["LATE_FEES_TASK", "beat_schedule", "post_all", "post_late_fees", "render_receipt"]
