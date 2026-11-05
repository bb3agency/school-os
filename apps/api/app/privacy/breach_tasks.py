"""Celery tasks of the breach register (FR-DPDP-032, FR-DPDP-035, D58). IDs and counts only.

- ``privacy.breach_sweep`` (every ``sweep_interval_seconds``): for each school, pull SchoolOS's
  confirmed incident notices (shared deployment) and send the 24 h / 60 h reminders that are due.
- ``privacy.breach_purge`` (beat, daily 03:15 IST; lead decision D58): retention of closed
  breaches in active, suspended and offboarding schools (retention keeps running while a school
  is suspended or offboarding, docs/04 §6).

Each school runs in its own transactions; one failing school does not stop the others.
"""

from __future__ import annotations

from typing import Any, Final

from celery import shared_task
from celery.schedules import crontab

from app.core.db import context_free_session, tenant_session
from app.core.logging import get_logger
from app.privacy import breach_service as service
from app.privacy import breach_sync
from app.tenancy import service as tenancy

log = get_logger(__name__)
TENANT_STATUSES = ("active", "suspended")
PURGE_TASK: Final = "privacy.breach_purge"
PURGE_TENANT_STATUSES: Final = ("active", "suspended", "offboarding")


def sweep_all() -> dict[str, int]:
    with context_free_session() as session:
        tenant_ids = tenancy.list_tenant_ids(session, TENANT_STATUSES)
    totals = {"tenants": len(tenant_ids), "received": 0, "reminders": 0, "failed": 0}
    for tenant_id in tenant_ids:
        # The pull and the reminders are independent: a failed pull must not hold back the
        # 24 h / 60 h reminders of breaches already in the register.
        failed = False
        try:
            totals["received"] += breach_sync.sync_school(tenant_id)
        except Exception as exc:  # this school only; the next sweep retries
            failed = True
            log.warning(
                "privacy.breach_sweep_failed", tenant_id=tenant_id, error_type=type(exc).__name__
            )
        try:
            totals["reminders"] += service.send_reminders(tenant_id)
        except Exception as exc:  # this school only; the next sweep retries
            failed = True
            log.warning(
                "privacy.breach_sweep_failed", tenant_id=tenant_id, error_type=type(exc).__name__
            )
        totals["failed"] += int(failed)
    return totals


@shared_task(name="privacy.breach_sweep", acks_late=True, ignore_result=True)
def sweep() -> dict[str, int]:
    return sweep_all()


def purge_all() -> dict[str, int]:
    """Lead decision D58: each school's expired closed breaches, in its own ``tenant_session``
    (the purge and its audit event commit or roll back together; a failed school is due again
    the next night)."""
    with context_free_session() as session:
        tenant_ids = tenancy.list_tenant_ids(session, PURGE_TENANT_STATUSES)
    totals = {"tenants": len(tenant_ids), "purged": 0, "failed": 0}
    for tenant_id in tenant_ids:
        try:
            with tenant_session(tenant_id) as session:
                totals["purged"] += service.purge_expired(session)
        except Exception as exc:  # this school only; the next run retries
            totals["failed"] += 1
            log.warning(
                "privacy.breach_purge_failed", tenant_id=tenant_id, error_type=type(exc).__name__
            )
    log.info("privacy.breach_purge", count=totals["purged"], failed=totals["failed"])
    return totals


@shared_task(name=PURGE_TASK, acks_late=True, ignore_result=True)
def purge() -> dict[str, int]:
    return purge_all()


def beat_schedule() -> dict[str, dict[str, Any]]:
    """Beat entries for the breach register (both deployment modes; pulling is shared-only)."""
    return {
        "privacy-breach-sweep": {
            "task": "privacy.breach_sweep",
            "schedule": float(service.sweep_interval_seconds()),
        },
        # D58: 03:15 IST, with the other nightly retention purges.
        "privacy-breach-purge": {
            "task": PURGE_TASK,
            "schedule": crontab(minute=45, hour=21),
        },
    }
