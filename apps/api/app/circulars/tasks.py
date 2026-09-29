"""Celery tasks for circulars, tasks and notices (M4; docs/04 §6). IDs only in arguments.

- ``circulars.read_version`` (queue ``ingest``, with the knowledge jobs): consumer of the outbox
  event ``circulars.read.requested``; reads one circular version through the knowledge gateway
  (FR-CIR-002). Idempotent (the reading's status gates the work); retried with backoff on
  database errors; after the last attempt the reading shows "needs manual review"
  (``worker_error``).
- ``circulars.draft_notice`` (queue ``ingest``, with the circular reading): consumer of
  ``circulars.notice.draft_requested``; drafts one parent notice in English and Telugu through
  the knowledge gateway with no transaction open (FR-NOTICE-003). Idempotent (only a
  ``drafting`` notice is drafted); retried with backoff; after the last attempt the notice shows
  ``draft_failed`` (``worker_error``) and can be tried again or written by hand.
- ``circulars.render_notice`` (queue ``pdf``, Chromium workers): consumer of
  ``circulars.notice.render_requested``; the A4 PDF and PNG of an approved notice (FR-NOTICE-006).
- ``circulars.send_task_reminders`` (beat, daily, queue ``maintenance``): due-soon and overdue
  reminders per school (FR-TASK-007), one tenant session per school; one failing school does not
  stop the others.

Register this module in ``sos_worker.celery_app.TASK_MODULES`` and merge :func:`beat_schedule`.
"""

from __future__ import annotations

import uuid
from typing import Any, Final

from celery import Task, shared_task
from celery.schedules import crontab
from sqlalchemy.exc import SQLAlchemyError

from app.circulars import service
from app.core.db import context_free_session, tenant_session
from app.core.errors import DomainError
from app.core.logging import get_logger
from app.tenancy import service as tenancy

log = get_logger(__name__)

MAX_RETRIES: Final = 3
REMINDER_TASK: Final = "circulars.send_task_reminders"
TENANT_STATUSES: Final = ("active", "suspended")


def _uuid(value: object) -> uuid.UUID:
    return uuid.UUID(str(value))


@shared_task(
    name=service.READ_TASK,
    bind=True,
    queue="ingest",
    acks_late=True,
    max_retries=MAX_RETRIES,
    ignore_result=True,
)
def read_version(
    self: Task[Any, Any], tenant_id: str, event_id: str, payload: dict[str, Any]
) -> str:
    tid, reading_id = _uuid(tenant_id), _uuid(payload["reading_id"])
    try:
        return service.run_reading(tid, reading_id)
    except Exception as exc:
        if self.request.retries >= MAX_RETRIES:
            service.abandon_reading(tid, reading_id, "worker_error")
            raise
        raise self.retry(exc=exc, countdown=min(30 * 2**self.request.retries, 600)) from exc


@shared_task(
    name=service.DRAFT_TASK,
    bind=True,
    queue="ingest",
    acks_late=True,
    max_retries=MAX_RETRIES,
    ignore_result=True,
)
def draft_notice(
    self: Task[Any, Any], tenant_id: str, event_id: str, payload: dict[str, Any]
) -> str:
    tid, notice_id = _uuid(tenant_id), _uuid(payload["notice_id"])
    try:
        return service.run_notice_draft(tid, notice_id)
    except Exception as exc:
        if self.request.retries >= MAX_RETRIES:
            service.abandon_draft(tid, notice_id, "worker_error")
            raise
        raise self.retry(exc=exc, countdown=min(30 * 2**self.request.retries, 600)) from exc


@shared_task(
    name=service.RENDER_TASK,
    bind=True,
    queue="pdf",
    acks_late=True,
    max_retries=MAX_RETRIES,
    ignore_result=True,
)
def render_notice(
    self: Task[Any, Any], tenant_id: str, event_id: str, payload: dict[str, Any]
) -> str:
    tid, notice_id = _uuid(tenant_id), _uuid(payload["notice_id"])
    try:
        return service.render_notice(tid, notice_id)
    except Exception as exc:
        if self.request.retries >= MAX_RETRIES:
            service.abandon_render(tid, notice_id, "worker_error")
            raise
        raise self.retry(exc=exc, countdown=min(30 * 2**self.request.retries, 600)) from exc


def remind_all() -> dict[str, int]:
    with context_free_session() as session:
        tenant_ids = tenancy.list_tenant_ids(session, TENANT_STATUSES)
    totals = {"tenants": len(tenant_ids), "sent": 0, "failed": 0}
    for tenant_id in tenant_ids:
        try:
            with tenant_session(tenant_id) as session:
                totals["sent"] += service.send_reminders(session)
        except (SQLAlchemyError, DomainError) as exc:
            totals["failed"] += 1
            log.warning(
                "circulars.reminders_failed", tenant_id=tenant_id, error_type=type(exc).__name__
            )
    log.info("circulars.reminders_sent", count=totals["sent"])
    return totals


@shared_task(name=REMINDER_TASK, queue="maintenance", acks_late=True, ignore_result=True)
def send_task_reminders() -> dict[str, int]:
    return remind_all()


def beat_schedule() -> dict[str, dict[str, Any]]:
    """Beat entries for circulars and tasks (both deployment modes)."""
    return {
        # FR-TASK-007: every morning at 07:10 IST, before the office opens.
        "circulars-task-reminders": {
            "task": REMINDER_TASK,
            "schedule": crontab(minute=40, hour=1),
        },
    }


__all__ = [
    "beat_schedule",
    "draft_notice",
    "read_version",
    "remind_all",
    "render_notice",
    "send_task_reminders",
]
