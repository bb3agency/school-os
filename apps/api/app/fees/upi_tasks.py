"""Celery task for UPI reconciliation upkeep (R2-2E; FR-FEE-017, FR-FEE-026; docs/05 §13).

- ``fees.upi_morning`` (beat, 07:00 IST, queue ``maintenance``): per school (suspended and
  offboarding schools and schools with fees off included: retention) erases bank statement
  narrations older than 90 days and, for an active school with fees on, when there is anything
  to look at, notifies the holders of ``fee.reconcile`` with the counts (UPI receipts unconfirmed
  past the school's window; bank credits needing a person). One tenant session per school; one
  failing school does not stop the others. Ids and counts only in logs.

Register this module in ``sos_worker.celery_app.TASK_MODULES`` and merge :func:`beat_schedule`.
"""

from __future__ import annotations

import uuid
from typing import Any, Final

from celery import shared_task
from celery.schedules import crontab

from app.core.db import context_free_session, tenant_session
from app.core.logging import get_logger
from app.fees import service
from app.fees.upi_config import load_upi_config
from app.notifications import service as notifications
from app.tenancy import service as tenancy

log = get_logger(__name__)

MORNING_TASK: Final = "fees.upi_morning"
TEMPLATE: Final = "fee.upi_exceptions"
TENANT_STATUSES: Final = ("active",)
# The 90-day narration erasure is retention (PRV-024, docs/05 §13): it keeps running while the
# school is suspended or offboarding and when fees were switched off after an import (docs/04
# §6, docs/16 §5.5); the notification goes to active schools with fees on only.
ERASE_TENANT_STATUSES: Final = ("active", "suspended", "offboarding")
MAX_ERASE_BATCHES: Final = 50


def morning_for_school(tenant_id: uuid.UUID, *, notify: bool = True) -> dict[str, int]:
    """Erase old narrations and notify (see the module docstring) for one school."""
    erased = 0
    with tenant_session(tenant_id) as session:
        for _ in range(MAX_ERASE_BATCHES):
            done = service.erase_statement_narrations(session)
            erased += done
            if done == 0:
                break
        if not notify or not service.enabled(session, tenant_id):
            return {"erased": erased, "notified": 0}
        unconfirmed, credits = service.upi_exception_counts(session)
        notified = 0
        if unconfirmed or credits:
            today = service.today(session)
            notified = notifications.notify(
                session,
                tenant_id=tenant_id,
                recipients=notifications.PermissionSelector(service.RECONCILE),
                template_key=TEMPLATE,
                params={"unconfirmed": unconfirmed, "credits": credits},
                dedupe_key=f"{TEMPLATE}:{today.isoformat()}",
            )
    return {"erased": erased, "notified": notified}


def morning_all() -> dict[str, int]:
    with context_free_session() as session:
        tenant_ids = tenancy.list_tenant_ids(session, ERASE_TENANT_STATUSES)
        active = set(tenancy.list_tenant_ids(session, TENANT_STATUSES))
    totals = {"tenants": len(tenant_ids), "erased": 0, "notified": 0, "failed": 0}
    for tenant_id in tenant_ids:
        try:
            out = morning_for_school(tenant_id, notify=tenant_id in active)
        except Exception as exc:
            totals["failed"] += 1
            log.warning(
                "fees.upi_morning_failed", tenant_id=tenant_id, error_type=type(exc).__name__
            )
            continue
        totals["erased"] += out["erased"]
        totals["notified"] += out["notified"]
    log.info("fees.upi_morning_done", count=totals["notified"])
    return totals


@shared_task(name=MORNING_TASK, queue="maintenance", acks_late=True, ignore_result=True)
def upi_morning() -> dict[str, int]:
    return morning_all()


def beat_schedule() -> dict[str, dict[str, Any]]:
    """Beat entries (both deployment modes). The beat runs in UTC: 01:30 UTC = 07:00 IST."""
    spec = load_upi_config().exceptions
    return {
        "fees-upi-morning": {
            "task": MORNING_TASK,
            "schedule": crontab(hour=spec.notify_hour_utc, minute=spec.notify_minute_utc),
        },
    }


__all__ = ["MORNING_TASK", "beat_schedule", "morning_all", "morning_for_school", "upi_morning"]
