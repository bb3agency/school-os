"""Celery tasks for WhatsApp sending (R2-3G; FR-MSG-011..016). IDs and counts only.

Outbox consumers (``ops.TenantTask``: held while the school is suspended or offboarding, D31):
- ``messaging.send_due``: send the school's queued messages whose time has come (woken by a
  confirmed broadcast or an automatic run);
- ``messaging.receipt_message``: the receipt message for ``fee.receipt.message``;
- ``messaging.notice_withdrawn``: a withdrawn notice's queued messages and links (R2-3J).

Beat (registered through ``app.messaging.tasks.beat_schedule``): ``messaging.send_sweep`` every
minute (quiet hours ending, retries, the next day's cap), ``messaging.automatic_runs`` every five
minutes (absence alerts at the school's time, fee reminders at 10:00 IST; once per day each).
Only active schools are visited; one failing school does not stop the others.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from celery import Task, shared_task
from sqlalchemy.exc import SQLAlchemyError

from app.core.errors import DomainError
from app.core.logging import get_logger
from app.messaging import service as messaging
from app.messaging.providers import ProviderError
from app.messaging.sends import service
from app.ops.service import TenantTask

log = get_logger(__name__)


def _each_school(action: str, run: Any) -> dict[str, int]:
    if not messaging.available():
        return {"skipped": 1}
    totals = {"schools": 0, "failed": 0}
    for tenant_id in messaging.live_tenant_ids():
        try:
            run(tenant_id)
            totals["schools"] += 1
        except (SQLAlchemyError, DomainError, ProviderError) as exc:
            totals["failed"] += 1
            log.warning(
                "messaging.school_task_failed",
                tenant_id=tenant_id,
                action=action,
                error_type=type(exc).__name__,
            )
    return totals


@shared_task(
    name=service.SEND_TASK,
    base=TenantTask,
    bind=True,
    queue="maintenance",
    acks_late=True,
    ignore_result=True,
)
def send_due(
    self: Task[Any, Any], tenant_id: str, event_id: str, payload: dict[str, Any]
) -> dict[str, int]:
    del self, event_id, payload
    return service.send_due(uuid.UUID(tenant_id))


@shared_task(
    name=service.RECEIPT_TASK,
    base=TenantTask,
    bind=True,
    queue="maintenance",
    acks_late=True,
    ignore_result=True,
)
def receipt_message(
    self: Task[Any, Any], tenant_id: str, event_id: str, payload: dict[str, Any]
) -> dict[str, int]:
    del self, event_id
    if not messaging.available():
        return {}
    return service.queue_receipt_message(
        uuid.UUID(tenant_id), uuid.UUID(str(payload["receipt_id"]))
    )


@shared_task(
    name=service.NOTICE_WITHDRAWN_TASK,
    base=TenantTask,
    bind=True,
    queue="maintenance",
    acks_late=True,
    ignore_result=True,
)
def notice_withdrawn(
    self: Task[Any, Any], tenant_id: str, event_id: str, payload: dict[str, Any]
) -> dict[str, int]:
    """R2-3J (FR-NOTICE-009, FR-PUB-007): cancel a withdrawn notice's queued messages and
    revoke its parent links. Runs with messaging off too (links exist only when it was on)."""
    del self, event_id
    return service.cancel_notice_messages(
        uuid.UUID(tenant_id), uuid.UUID(str(payload["notice_id"]))
    )


@shared_task(name="messaging.send_sweep", acks_late=True, ignore_result=True)
def send_sweep() -> dict[str, int]:
    return _each_school("send", service.send_due)


def _automatic(tenant_id: uuid.UUID) -> None:
    now = dt.datetime.now(dt.UTC)
    service.run_absence_alerts(tenant_id, now)
    service.run_fee_reminders(tenant_id, now)


@shared_task(name="messaging.automatic_runs", acks_late=True, ignore_result=True)
def automatic_runs() -> dict[str, int]:
    return _each_school("automatic", _automatic)


def beat_schedule() -> dict[str, dict[str, Any]]:
    """The sender every minute, the automatic runs every five minutes."""
    return {
        "messaging-send-sweep": {"task": "messaging.send_sweep", "schedule": 60.0},
        "messaging-automatic-runs": {"task": "messaging.automatic_runs", "schedule": 300.0},
    }


__all__ = [
    "automatic_runs",
    "beat_schedule",
    "notice_withdrawn",
    "receipt_message",
    "send_due",
    "send_sweep",
]
