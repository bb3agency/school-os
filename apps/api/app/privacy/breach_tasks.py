"""Celery task of the breach register (FR-DPDP-032, FR-DPDP-035). IDs and counts only.

``privacy.breach_sweep`` (every ``sweep_interval_seconds``): for each school, pull SchoolOS's
confirmed incident notices (shared deployment) and send the 24 h / 60 h reminders that are due.
Each school runs in its own transactions; one failing school does not stop the others.
"""

from __future__ import annotations

from typing import Any

from celery import shared_task
from sqlalchemy.exc import SQLAlchemyError

from app.core.db import context_free_session
from app.core.errors import DomainError
from app.core.logging import get_logger
from app.privacy import breach_service as service
from app.privacy import breach_sync
from app.tenancy import service as tenancy

log = get_logger(__name__)
TENANT_STATUSES = ("active", "suspended")


def sweep_all() -> dict[str, int]:
    with context_free_session() as session:
        tenant_ids = tenancy.list_tenant_ids(session, TENANT_STATUSES)
    totals = {"tenants": len(tenant_ids), "received": 0, "reminders": 0, "failed": 0}
    for tenant_id in tenant_ids:
        try:
            totals["received"] += breach_sync.sync_school(tenant_id)
            totals["reminders"] += service.send_reminders(tenant_id)
        except (SQLAlchemyError, DomainError) as exc:
            totals["failed"] += 1
            log.warning(
                "privacy.breach_sweep_failed", tenant_id=tenant_id, error_type=type(exc).__name__
            )
    return totals


@shared_task(name="privacy.breach_sweep", acks_late=True, ignore_result=True)
def sweep() -> dict[str, int]:
    return sweep_all()


def beat_schedule() -> dict[str, dict[str, Any]]:
    """Beat entries for the breach register (both deployment modes; pulling is shared-only)."""
    return {
        "privacy-breach-sweep": {
            "task": "privacy.breach_sweep",
            "schedule": float(service.sweep_interval_seconds()),
        },
    }
