"""Celery tasks for break-glass (US-103 AC2, FR-OPS-004). IDs and counts only.

- ``breakglass.sweep`` (every ``sweep_interval_seconds``): for each school, pull new requests
  from the control plane (shared deployment), end grants whose window has passed, expire
  unanswered requests and report outcomes back. Each school runs in its own transactions; one
  failing school does not stop the others.
"""

from __future__ import annotations

from typing import Any

from celery import shared_task

from app.breakglass import service
from app.core.db import context_free_session
from app.core.logging import get_logger
from app.tenancy import service as tenancy

log = get_logger(__name__)
TENANT_STATUSES = ("active", "suspended")


def sweep_all() -> dict[str, int]:
    with context_free_session() as session:
        tenant_ids = tenancy.list_tenant_ids(session, TENANT_STATUSES)
    totals = {"tenants": len(tenant_ids), "received": 0, "expired": 0, "failed": 0}
    for tenant_id in tenant_ids:
        # Pulling from the control plane and ending grants are independent: a failed pull must
        # not keep a grant whose window has passed from being ended (and its approvers told).
        failed = False
        try:
            synced = service.sync_school(tenant_id)
            totals["received"] += synced.received + synced.emergency
        except Exception as exc:  # this school only; the next sweep retries
            failed = True
            log.warning(
                "breakglass.sweep_failed", tenant_id=tenant_id, error_type=type(exc).__name__
            )
        try:
            swept = service.sweep_school(tenant_id)
            totals["expired"] += swept["expired"] + swept["stale_requests"]
        except Exception as exc:  # this school only; the next sweep retries
            failed = True
            log.warning(
                "breakglass.sweep_failed", tenant_id=tenant_id, error_type=type(exc).__name__
            )
        totals["failed"] += int(failed)
    return totals


@shared_task(name="breakglass.sweep", acks_late=True, ignore_result=True)
def sweep() -> dict[str, int]:
    return sweep_all()


def beat_schedule() -> dict[str, dict[str, Any]]:
    """Beat entries for break-glass (both deployment modes; pulling is shared-only)."""
    return {
        "breakglass-sweep": {
            "task": "breakglass.sweep",
            "schedule": float(service.sweep_interval_seconds()),
        },
    }
