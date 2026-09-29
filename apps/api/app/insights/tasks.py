"""Celery tasks for the early-warning rules (M5; docs/04 §6). IDs only in arguments; queue
``maintenance`` (database work, no files, no AI).

- ``insights.evaluate_students`` (outbox consumer of ``academics.records.changed`` and
  ``insights.evaluate.requested``): the rules for the students just written (FR-ATT-005,
  FR-EW-005). Idempotent (unique keys); retried with backoff on database errors.
- ``insights.evaluate_all`` (beat, daily 17:40 IST, after the school day's registers): the rules
  for every student actively enrolled in the current year, school by school; one failing school
  does not stop the others.
- ``insights.send_flag_reminders`` (beat, daily 07:20 IST): overdue reminders (FR-EW-006).
- ``insights.purge_expired`` (beat, daily 02:50 IST): retention of notes and closed flags
  (FR-EW-017).

Register this module in ``sos_worker.celery_app.TASK_MODULES`` and merge :func:`beat_schedule`.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Any, Final

from celery import Task, shared_task
from celery.schedules import crontab
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.db import context_free_session, tenant_session
from app.core.errors import DomainError
from app.core.logging import get_logger
from app.insights import service
from app.tenancy import service as tenancy

log = get_logger(__name__)

MAX_RETRIES: Final = 3
EVALUATE_ALL_TASK: Final = "insights.evaluate_all"
REMINDER_TASK: Final = "insights.send_flag_reminders"
PURGE_TASK: Final = "insights.purge_expired"
TENANT_STATUSES: Final = ("active",)


def _ids(values: object) -> list[uuid.UUID]:
    if not isinstance(values, list):
        return []
    return [uuid.UUID(str(v)) for v in values]


@shared_task(
    name=service.EVALUATE_TASK,
    bind=True,
    queue="maintenance",
    acks_late=True,
    max_retries=MAX_RETRIES,
    ignore_result=True,
)
def evaluate_students(
    self: Task[Any, Any], tenant_id: str, event_id: str, payload: dict[str, Any]
) -> int:
    student_ids = _ids(payload.get("student_ids"))
    if not student_ids:
        return 0
    try:
        with tenant_session(uuid.UUID(tenant_id)) as session:
            return service.evaluate(session, student_ids)
    except SQLAlchemyError as exc:
        raise self.retry(exc=exc, countdown=min(30 * 2**self.request.retries, 600)) from exc


def for_each_school(label: str, job: Callable[[Session], int | dict[str, int]]) -> dict[str, int]:
    """Run ``job`` in each active school's own ``tenant_session`` (statuses: active only; a
    suspended school's staff cannot act on flags, so nothing is raised or reminded there)."""
    with context_free_session() as session:
        tenant_ids = tenancy.list_tenant_ids(session, TENANT_STATUSES)
    totals = {"tenants": len(tenant_ids), "done": 0, "failed": 0}
    for tenant_id in tenant_ids:
        try:
            with tenant_session(tenant_id) as session:
                result = job(session)
            totals["done"] += result if isinstance(result, int) else sum(result.values())
        except (SQLAlchemyError, DomainError) as exc:
            totals["failed"] += 1
            log.warning(
                "insights.job_failed",
                action=label,
                tenant_id=tenant_id,
                error_type=type(exc).__name__,
            )
    log.info("insights.job_done", action=label, count=totals["done"])
    return totals


@shared_task(name=EVALUATE_ALL_TASK, queue="maintenance", acks_late=True, ignore_result=True)
def evaluate_all() -> dict[str, int]:
    return for_each_school("evaluate", service.evaluate)


@shared_task(name=REMINDER_TASK, queue="maintenance", acks_late=True, ignore_result=True)
def send_flag_reminders() -> dict[str, int]:
    return for_each_school("reminders", service.send_reminders)


@shared_task(name=PURGE_TASK, queue="maintenance", acks_late=True, ignore_result=True)
def purge_expired() -> dict[str, int]:
    return for_each_school("retention", service.purge_expired)


def beat_schedule() -> dict[str, dict[str, Any]]:
    """Beat entries for the early-warning rules (both deployment modes)."""
    return {
        # FR-EW-005: 17:40 IST, after the school day's attendance.
        "insights-evaluate-daily": {
            "task": EVALUATE_ALL_TASK,
            "schedule": crontab(minute=10, hour=12),
        },
        # FR-EW-006: 07:20 IST, before school.
        "insights-flag-reminders": {
            "task": REMINDER_TASK,
            "schedule": crontab(minute=50, hour=1),
        },
        # FR-EW-017: 02:50 IST.
        "insights-purge-expired": {
            "task": PURGE_TASK,
            "schedule": crontab(minute=20, hour=21),
        },
    }


__all__ = [
    "beat_schedule",
    "evaluate_all",
    "evaluate_students",
    "for_each_school",
    "purge_expired",
    "send_flag_reminders",
]
