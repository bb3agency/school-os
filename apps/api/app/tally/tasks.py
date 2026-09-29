"""Celery tasks for the Tally connector (M6; ADR-0032; docs/04 §6). IDs only in arguments.

- ``tally.check_silent_agents`` (beat, every 30 minutes, queue ``maintenance``): per school with
  the connector flag on, an in-app notice when an active agent has not called for the silence
  limit (FR-TALLY-009), and sync records past their retention deleted. One tenant session per
  school; one failing school does not stop the others.

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
from app.tally import service
from app.tenancy import service as tenancy

log = get_logger(__name__)

SILENCE_TASK: Final = "tally.check_silent_agents"
TENANT_STATUSES: Final = ("active",)


def check_all() -> dict[str, int]:
    with context_free_session() as session:
        tenant_ids = tenancy.list_tenant_ids(session, TENANT_STATUSES)
    totals = {"tenants": len(tenant_ids), "notified": 0, "purged": 0, "failed": 0}
    for tenant_id in tenant_ids:
        try:
            with tenant_session(tenant_id) as session:
                if not service.connector_enabled(session, tenant_id):
                    continue
                totals["notified"] += service.notify_silent_devices(session)
                totals["purged"] += service.purge_old_syncs(session)
        except (SQLAlchemyError, DomainError) as exc:
            totals["failed"] += 1
            log.warning(
                "tally.silence_check_failed", tenant_id=tenant_id, error_type=type(exc).__name__
            )
    log.info("tally.silence_checked", count=totals["notified"])
    return totals


@shared_task(name=SILENCE_TASK, queue="maintenance", acks_late=True, ignore_result=True)
def check_silent_agents() -> dict[str, int]:
    return check_all()


def beat_schedule() -> dict[str, dict[str, Any]]:
    """Beat entries for the Tally connector (both deployment modes)."""
    return {
        # FR-TALLY-009: silent agents and sync-record retention, every 30 minutes.
        "tally-check-silent-agents": {
            "task": SILENCE_TASK,
            "schedule": crontab(minute="7,37"),
        },
    }


__all__ = ["SILENCE_TASK", "beat_schedule", "check_all", "check_silent_agents"]
