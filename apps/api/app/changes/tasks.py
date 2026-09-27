"""Celery tasks for change requests (FR-CR-004). IDs and counts only.

- ``changes.expire_requests`` (daily): per school, pending requests past ``expires_at`` become
  ``expired`` (audited, requester notified). Each school runs in its own ``tenant_session``;
  one failing school does not stop the others.
"""

from __future__ import annotations

from typing import Any

from celery import shared_task
from celery.schedules import crontab
from sqlalchemy.exc import SQLAlchemyError

from app.changes import service
from app.core.db import context_free_session, tenant_session
from app.core.errors import DomainError
from app.core.logging import get_logger
from app.tenancy import service as tenancy

log = get_logger(__name__)
TENANT_STATUSES = ("active", "suspended")


def expire_all() -> dict[str, int]:
    with context_free_session() as session:
        tenant_ids = tenancy.list_tenant_ids(session, TENANT_STATUSES)
    totals = {"tenants": len(tenant_ids), "expired": 0, "failed": 0}
    for tenant_id in tenant_ids:
        try:
            with tenant_session(tenant_id) as session:
                totals["expired"] += service.expire_due(session)
        except (SQLAlchemyError, DomainError) as exc:
            totals["failed"] += 1
            log.warning("changes.expire_failed", tenant_id=tenant_id, error_type=type(exc).__name__)
    log.info("changes.expired", count=totals["expired"])
    return totals


@shared_task(name="changes.expire_requests", acks_late=True, ignore_result=True)
def expire_requests() -> dict[str, int]:
    return expire_all()


def beat_schedule() -> dict[str, dict[str, Any]]:
    """Beat entries for change requests (both deployment modes)."""
    return {
        "changes-expire-requests": {
            "task": "changes.expire_requests",
            "schedule": crontab(minute=10, hour=21),  # 02:40 IST
        },
    }
