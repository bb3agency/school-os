"""Celery tasks for certificates (FR-CERT-010; docs/04 §6). IDs only in arguments and results.

- ``certificates.render`` (queue ``pdf``, the workers with Chromium): prints an issued
  certificate to PDF and stores it as a document; consumer of the outbox event
  ``certificate.render_requested``. Idempotent (a stored PDF is left alone) and retried with
  backoff; after the last attempt the certificate shows the PDF failed (retry from the screen).
- ``certificates.tc_overdue_reminders`` (beat, daily 07:15 IST, queue ``maintenance``): per
  active school, the overdue TC request count to the principal and the office (FR-CERT-045,
  ``tc_overdue``). Idempotent per recipient and day; one failing school does not stop the others.

Register this module in ``sos_worker.celery_app.TASK_MODULES``, route ``render`` to ``pdf`` and
merge :func:`beat_schedule`.
"""

from __future__ import annotations

import uuid
from typing import Any, Final

from celery import Task, shared_task
from celery.schedules import crontab

from app.certificates import service, tc_overdue
from app.core.db import context_free_session
from app.core.logging import get_logger
from app.ops.service import TenantTask
from app.tenancy import service as tenancy

log = get_logger(__name__)

MAX_RETRIES = 3
TC_OVERDUE_TASK: Final = "certificates.tc_overdue_reminders"
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


def tc_overdue_all() -> dict[str, int]:
    """Each active school's overdue TC request reminder in its own transaction (FR-CERT-045)."""
    with context_free_session() as session:
        tenant_ids = tenancy.list_tenant_ids(session, TENANT_STATUSES)
    totals = {"tenants": len(tenant_ids), "notified": 0, "failed": 0}
    for tenant_id in tenant_ids:
        try:
            totals["notified"] += tc_overdue.send_reminders(tenant_id)
        except Exception as exc:  # this school only; tomorrow's run tries again
            totals["failed"] += 1
            log.warning(
                "certificates.tc_overdue.failed", tenant_id=tenant_id, error_code=type(exc).__name__
            )
    log.info("certificates.tc_overdue", count=totals["notified"], failed=totals["failed"])
    return totals


@shared_task(name=TC_OVERDUE_TASK, acks_late=True, ignore_result=True)
def tc_overdue_reminders() -> dict[str, int]:
    return tc_overdue_all()


def beat_schedule() -> dict[str, dict[str, Any]]:
    """Beat entry for the overdue TC request reminder (both deployment modes)."""
    return {
        "certificates-tc-overdue-reminders": {
            "task": TC_OVERDUE_TASK,
            "schedule": crontab(minute=45, hour=1),  # 07:15 IST
        },
    }
