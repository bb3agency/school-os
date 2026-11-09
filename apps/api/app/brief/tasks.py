"""Celery task for the weekly management brief (FA-05; FR-BRF-010). Counts only in results.

Beat: ``brief.build_weekly`` (Mondays 07:45 IST, queue ``maintenance``): per active school, in its
own ``tenant_session``, store last week's brief once, notify the holders of ``brief.read`` and
queue the opted-in e-mails. One failing school does not stop the others. A suspended or
offboarding school gets no brief (its people cannot open it; BR-08, like the portal reminders).

Register this module in ``sos_worker.celery_app.TASK_MODULES`` and merge :func:`beat_schedule`.
"""

from __future__ import annotations

import datetime as dt
from typing import Any, Final

from celery import shared_task
from celery.schedules import crontab

from app.brief import service
from app.core.db import context_free_session, tenant_session
from app.core.logging import get_logger
from app.tenancy import service as tenancy

log = get_logger(__name__)

WEEKLY_TASK: Final = "brief.build_weekly"
TENANT_STATUSES: Final = ("active",)


def build_all(*, today: dt.date | None = None) -> dict[str, int]:
    """Every active school's weekly brief, one ``tenant_session`` per school."""
    with context_free_session() as session:
        tenant_ids = tenancy.list_tenant_ids(session, TENANT_STATUSES)
    totals = {"tenants": len(tenant_ids), "notified": 0, "failed": 0}
    for tenant_id in tenant_ids:
        try:
            with tenant_session(tenant_id) as session:
                totals["notified"] += service.build_week(session, today=today)
        except Exception as exc:
            totals["failed"] += 1
            log.warning("brief.weekly_failed", tenant_id=tenant_id, error_type=type(exc).__name__)
    log.info("brief.weekly_done", count=totals["notified"])
    return totals


@shared_task(name=WEEKLY_TASK, queue="maintenance", acks_late=True, ignore_result=True)
def build_weekly() -> dict[str, int]:
    return build_all()


def beat_schedule() -> dict[str, dict[str, Any]]:
    """Beat entries for the weekly brief (both deployment modes)."""
    return {
        # FR-BRF-010: Mondays at 07:45 IST (02:15 UTC), after the portal reminders (07:30).
        "brief-weekly": {
            "task": WEEKLY_TASK,
            "schedule": crontab(minute=15, hour=2, day_of_week=1),
        },
    }
