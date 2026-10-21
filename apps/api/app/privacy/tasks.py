"""Celery tasks for privacy requests (R3-1A; FR-DPDP-006). IDs and counts only.

- ``privacy.notify_due`` (beat, daily 07:00 IST): per active school, one in-app notification to
  the holders of ``privacy.request.manage`` with the number of open requests due tomorrow and
  overdue (idempotent per school and day). One failing school does not stop the others.

Register this module in ``sos_worker.celery_app.TASK_MODULES`` and merge :func:`beat_schedule`.
"""

from __future__ import annotations

from typing import Any, Final

from celery import shared_task
from celery.schedules import crontab
from sqlalchemy.exc import SQLAlchemyError

from app.core.db import context_free_session, tenant_session
from app.core.errors import DomainError
from app.core.logging import get_logger
from app.privacy import service
from app.tenancy import service as tenancy

log = get_logger(__name__)
TENANT_STATUSES: Final = ("active",)


def notify_all() -> dict[str, int]:
    with context_free_session() as session:
        tenant_ids = tenancy.list_tenant_ids(session, TENANT_STATUSES)
    totals = {"tenants": len(tenant_ids), "notified": 0, "failed": 0}
    for tenant_id in tenant_ids:
        try:
            with tenant_session(tenant_id) as session:
                totals["notified"] += service.notify_due(session)
        except (SQLAlchemyError, DomainError) as exc:
            totals["failed"] += 1
            log.warning(
                "privacy.notify_due.failed", tenant_id=tenant_id, error_code=type(exc).__name__
            )
    log.info("privacy.notify_due", count=totals["notified"])
    return totals


@shared_task(name=service.DUE_TASK, acks_late=True)
def notify_due() -> dict[str, int]:
    return notify_all()


def beat_schedule() -> dict[str, dict[str, Any]]:
    """Beat entries for privacy requests (both deployment modes)."""
    return {
        "privacy-notify-due": {
            "task": service.DUE_TASK,
            "schedule": crontab(minute=30, hour=1),  # 07:00 IST
        },
    }
