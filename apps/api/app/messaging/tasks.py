"""Celery tasks for WhatsApp messaging (ADR-0043; FR-MSG-006, FR-MSG-010, FR-MSG-016,
FR-MSG-020, FR-MSG-021, FR-MSG-023). IDs and counts only in arguments, results and logs.

Outbox consumers (``app.ops``, relayed every 5 s):
- ``messaging.process_inbox``: apply the school's stored webhook events (a STOP is applied well
  within 60 s of Meta's delivery, FR-MSG-010);
- ``messaging.provision_templates``: create the catalogue's templates in the school's WABA and
  read their statuses back;
- ``messaging.deliver``: send one queued message (the sending wave R2-3G queues them).

Beat: ``messaging.sweep_inbox`` every minute (events whose wake-up was lost),
``messaging.sync_accounts`` hourly (quality, limit, currency, template statuses; pauses on a red
or flagged rating) and ``messaging.purge_expired`` daily (retention, docs/05 §13). Each school
runs in its own ``tenant_session``; one failing school does not stop the others. Nothing runs
while messaging is off (FR-MSG-024).
"""

from __future__ import annotations

import uuid
from typing import Any

from celery import Task, shared_task
from celery.schedules import crontab
from sqlalchemy.exc import SQLAlchemyError

from app.core.errors import DomainError
from app.core.logging import get_logger
from app.messaging import service
from app.messaging.providers import ProviderError
from app.ops.service import TenantTask

log = get_logger(__name__)


def _each_school(action: str, run: Any) -> dict[str, int]:
    if not service.available():
        return {"skipped": 1}
    totals = {"schools": 0, "failed": 0}
    for tenant_id in service.live_tenant_ids():
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
    name=service.INBOX_TASK,
    base=TenantTask,
    bind=True,
    queue="maintenance",
    acks_late=True,
    ignore_result=True,
)
def process_inbox(
    self: Task[Any, Any], tenant_id: str, event_id: str, payload: dict[str, Any]
) -> dict[str, int]:
    del self, event_id, payload
    if not service.available():
        return {}
    return service.process_inbox(uuid.UUID(tenant_id))


@shared_task(
    name=service.PROVISION_TASK,
    base=TenantTask,
    bind=True,
    queue="maintenance",
    acks_late=True,
    ignore_result=True,
)
def provision_templates(
    self: Task[Any, Any], tenant_id: str, event_id: str, payload: dict[str, Any]
) -> dict[str, int]:
    del self, event_id
    if not service.available():
        return {}
    school = uuid.UUID(tenant_id)
    out = service.provision_templates(school, uuid.UUID(str(payload["account_id"])))
    synced = service.sync_templates(school)
    return {**out, **{f"sync_{k}": v for k, v in synced.items()}}


@shared_task(
    name=service.DELIVER_TASK,
    base=TenantTask,
    bind=True,
    queue="maintenance",
    acks_late=True,
    ignore_result=True,
)
def deliver(self: Task[Any, Any], tenant_id: str, event_id: str, payload: dict[str, Any]) -> str:
    del self, event_id
    if not service.available():
        return "unavailable"
    return service.deliver(uuid.UUID(tenant_id), uuid.UUID(str(payload["message_id"])))


@shared_task(name="messaging.sweep_inbox", acks_late=True, ignore_result=True)
def sweep_inbox() -> dict[str, int]:
    return _each_school("inbox", service.process_inbox)


def _sync(tenant_id: uuid.UUID) -> None:
    service.sync_account(tenant_id)
    service.sync_templates(tenant_id)


@shared_task(name="messaging.sync_accounts", acks_late=True, ignore_result=True)
def sync_accounts() -> dict[str, int]:
    return _each_school("sync", _sync)


@shared_task(name="messaging.purge_expired", acks_late=True, ignore_result=True)
def purge_expired() -> dict[str, int]:
    return _each_school("purge", service.purge_expired)


def beat_schedule() -> dict[str, dict[str, Any]]:
    """Inbox sweep every minute, account and template sync hourly, retention daily at 02:50 IST."""
    return {
        "messaging-sweep-inbox": {"task": "messaging.sweep_inbox", "schedule": 60.0},
        "messaging-sync-accounts": {
            "task": "messaging.sync_accounts",
            "schedule": crontab(minute=7),
        },
        "messaging-purge-expired": {
            "task": "messaging.purge_expired",
            "schedule": crontab(minute=20, hour=21),
        },
    }


__all__ = [
    "beat_schedule",
    "deliver",
    "process_inbox",
    "provision_templates",
    "purge_expired",
    "sweep_inbox",
    "sync_accounts",
]
