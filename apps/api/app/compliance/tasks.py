"""Compliance certificate jobs (FC-01; FR-COMP-011..FR-COMP-013).

- ``compliance.expiry_reminders`` (beat, daily 07:10 IST): per active school, in-app reminders
  of certificates that lapse soon or have lapsed. One failing school does not stop the others.
  A suspended school gets none (as the portal calendar, D31).

Register this module in ``sos_worker.celery_app.TASK_MODULES`` and merge :func:`beat_schedule`.
"""

from __future__ import annotations

from typing import Any, Final

from celery import shared_task
from celery.schedules import crontab

from app.compliance import checks, policies, service
from app.core.db import context_free_session
from app.core.logging import get_logger
from app.tenancy import service as tenancy

log = get_logger(__name__)
REMINDER_TASK: Final = "compliance.expiry_reminders"
TENANT_STATUSES: Final = ("active",)


def reminders_all() -> dict[str, int]:
    """Each school's certificate reminders in its own transaction."""
    with context_free_session() as session:
        tenant_ids = tenancy.list_tenant_ids(session, TENANT_STATUSES)
    totals = {"tenants": len(tenant_ids), "reminded": 0, "failed": 0}
    # FC-09 (FR-COMP-051) safety check and FC-10 (FR-COMP-061) policy review reminders run in
    # the same job, each sender in its own transaction.
    senders = (service.send_reminders, checks.send_reminders, policies.send_reminders)
    for tenant_id in tenant_ids:
        for send in senders:
            try:
                totals["reminded"] += send(tenant_id)
            except Exception as exc:  # this school and sender only; tomorrow's run retries
                totals["failed"] += 1
                log.warning(
                    "compliance.reminders.failed",
                    tenant_id=tenant_id,
                    error_code=type(exc).__name__,
                )
    log.info("compliance.reminders", count=totals["reminded"], failed=totals["failed"])
    return totals


@shared_task(name=REMINDER_TASK, acks_late=True, ignore_result=True)
def expiry_reminders() -> dict[str, int]:
    return reminders_all()


def beat_schedule() -> dict[str, dict[str, Any]]:
    """Beat entry for certificate reminders (both deployment modes)."""
    return {
        "compliance-expiry-reminders": {
            "task": REMINDER_TASK,
            "schedule": crontab(minute=40, hour=1),  # 07:10 IST
        },
    }
