"""Celery tasks for data quality (FR-DQ-002, NFR-PERF-005). Queue ``dq``; IDs only in arguments.

Outbox consumers (``ops.dispatch_outbox`` calls them with ``tenant_id``, ``event_id`` and the
event ``payload``; routes registered in :mod:`app.dq.service`):

- ``dq.run_incremental``: ``student.values.changed``, ``import.committed``, ``import.reverted``,
  ``extraction.confirmed``, ``change_request.approved`` -> re-check the students the write touched.
- ``dq.execute_run``: ``dq.run.requested`` -> a queued manual run (big scopes).
- ``dq.link_change_request`` / ``dq.unlink_change_request``: ``change_request.submitted`` /
  ``change_request.rejected``.

Beat (R3-1D): ``dq.send_portal_reminders`` (daily, 07:30 IST, queue ``maintenance``): portal
calendar reminders per active school (FR-RDY-006..008): staff tasks and in-app notifications,
never parents.

Register this module in ``sos_worker.celery_app.TASK_MODULES`` and merge :func:`beat_schedule`;
``dq.*`` routes to queue ``dq`` except the reminder job (``maintenance``).
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any, Final

from celery import shared_task
from celery.schedules import crontab
from sqlalchemy.exc import SQLAlchemyError

from app.core.db import context_free_session, tenant_session
from app.core.errors import DomainError
from app.core.logging import get_logger
from app.dq import portal_tasks, service
from app.ops.service import TenantTask
from app.tenancy import service as tenancy

log = get_logger(__name__)


def _uuid(value: object) -> uuid.UUID:
    return uuid.UUID(str(value))


@shared_task(
    name=service.INCREMENTAL_TASK, base=TenantTask, queue="dq", acks_late=True, ignore_result=True
)
def run_incremental(tenant_id: str, event_id: str, payload: dict[str, Any]) -> str | None:
    run = service.run_for_event(_uuid(tenant_id), payload)
    return None if run is None else str(run.id)


@shared_task(
    name=service.EXECUTE_TASK, base=TenantTask, queue="dq", acks_late=True, ignore_result=True
)
def execute_run(tenant_id: str, event_id: str, payload: dict[str, Any]) -> str:
    return service.execute_queued_run(_uuid(tenant_id), _uuid(payload["run_id"]))


@shared_task(
    name=service.LINK_TASK, base=TenantTask, queue="dq", acks_late=True, ignore_result=True
)
def link_change_request(tenant_id: str, event_id: str, payload: dict[str, Any]) -> int:
    return service.link_change_request(_uuid(tenant_id), payload)


@shared_task(
    name=service.UNLINK_TASK, base=TenantTask, queue="dq", acks_late=True, ignore_result=True
)
def unlink_change_request(tenant_id: str, event_id: str, payload: dict[str, Any]) -> int:
    return service.unlink_change_request(_uuid(tenant_id), payload)


# --- portal calendar reminders (R3-1D; FR-RDY-006..008, ADR-0053) -------------------------------

PORTAL_REMINDER_TASK: Final = "dq.send_portal_reminders"
# D31 and audit 2026-10-05 A-12: a suspended or offboarding school gets no reminders (its people
# cannot open the tasks; BR-08). Reminders resume the morning after reactivation; a step that
# was missed is sent once then (app.dq.calendar.due_lead_day).
TENANT_STATUSES: Final = ("active",)


def remind_all(*, today: dt.date | None = None) -> dict[str, int]:
    """Every active school's portal reminders, one ``tenant_session`` per school; one failing
    school does not stop the others. Counts only in the log."""
    with context_free_session() as session:
        tenant_ids = tenancy.list_tenant_ids(session, TENANT_STATUSES)
    totals = {"tenants": len(tenant_ids), "sent": 0, "failed": 0}
    for tenant_id in tenant_ids:
        try:
            with tenant_session(tenant_id) as session:
                totals["sent"] += portal_tasks.send_reminders(session, today=today)
        except (SQLAlchemyError, DomainError) as exc:
            totals["failed"] += 1
            log.warning(
                "dq.calendar.reminders_failed", tenant_id=tenant_id, error_type=type(exc).__name__
            )
    log.info("dq.calendar.reminders_done", count=totals["sent"])
    return totals


@shared_task(name=PORTAL_REMINDER_TASK, queue="maintenance", acks_late=True, ignore_result=True)
def send_portal_reminders() -> dict[str, int]:
    return remind_all()


def beat_schedule() -> dict[str, dict[str, Any]]:
    """Beat entries for data quality (both deployment modes)."""
    return {
        # FR-RDY-006: every morning at 07:30 IST (02:00 UTC), after the task reminders (07:10).
        "dq-portal-reminders": {
            "task": PORTAL_REMINDER_TASK,
            "schedule": crontab(minute=0, hour=2),
        },
    }
