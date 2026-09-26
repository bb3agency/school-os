"""Celery tasks for notifications (FR-NOT-001). IDs and counts only in arguments and results.

- ``notifications.purge_read`` (daily): per school, delete notifications read more than
  ``retention.read_days`` (90) days ago, each school in its own ``tenant_session``.
"""

from __future__ import annotations

from typing import Any

from celery import shared_task
from celery.schedules import crontab

from app.core.db import context_free_session, tenant_session
from app.core.logging import get_logger
from app.notifications import service
from app.tenancy import service as tenancy

log = get_logger(__name__)
TENANT_STATUSES = ("active", "suspended", "offboarding")


def purge_all() -> dict[str, int]:
    with context_free_session() as session:
        tenant_ids = tenancy.list_tenant_ids(session, TENANT_STATUSES)
    purged = 0
    for tenant_id in tenant_ids:
        with tenant_session(tenant_id) as session:
            purged += service.purge_read(session)
    log.info("notifications.purged", count=purged)
    return {"tenants": len(tenant_ids), "purged": purged}


@shared_task(name="notifications.purge_read", acks_late=True)
def purge_read() -> dict[str, int]:
    return purge_all()


def beat_schedule() -> dict[str, dict[str, Any]]:
    """Beat entries for notifications (both deployment modes)."""
    return {
        "notifications-purge-read": {
            "task": "notifications.purge_read",
            "schedule": crontab(minute=20, hour=21),  # 02:50 IST
        },
    }
