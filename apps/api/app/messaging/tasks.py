"""Celery tasks for WhatsApp messaging (ADR-0043; FR-MSG-006, FR-MSG-010, FR-MSG-016,
FR-MSG-020, FR-MSG-021, FR-MSG-023). IDs and counts only in arguments, results and logs.

Outbox consumers (``app.ops``, relayed every 5 s):
- ``messaging.process_inbox``: apply the school's stored webhook events (a STOP is applied well
  within 60 s of Meta's delivery, FR-MSG-010);
- ``messaging.provision_templates``: create the catalogue's templates in the school's WABA and
  read their statuses back;
The sending wave (R2-3G, ``app.messaging.sends.tasks``) adds ``messaging.send_due`` and
``messaging.receipt_message`` and its beat entries (imported here so the worker registers them).

Beat: ``messaging.sweep_inbox`` every minute (events whose wake-up was lost),
``messaging.sync_accounts`` hourly (quality, limit, currency, template statuses; pauses on a red
or flagged rating), ``messaging.purge_expired`` daily (retention, docs/05 §13; suspended
and offboarding schools included) and ``messaging.rates_recheck`` on 1 Jan, 1 Apr, 1 Jul and 1 Oct
(FR-MSG-031: a warning for the SchoolOS team to recheck Meta's rates in ``config.yaml``).
Each school runs in its own ``tenant_session``; one failing school does not stop the others.
Nothing runs while messaging is off (FR-MSG-024).
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from celery import Task, shared_task
from celery.schedules import crontab

from app.core.db import context_free_session
from app.core.logging import get_logger
from app.messaging import service
from app.messaging.config import load_config
from app.messaging.sends import service as sends
from app.messaging.sends import tasks as sends_tasks
from app.ops.service import TenantTask
from app.tenancy import service as tenancy

log = get_logger(__name__)


# Retention keeps running while a school is suspended or offboarding (docs/04 §6, docs/16 §5.5).
PURGE_TENANT_STATUSES = ("active", "suspended", "offboarding")


def _purge_tenant_ids() -> list[uuid.UUID]:
    with context_free_session() as session:
        return tenancy.list_tenant_ids(session, PURGE_TENANT_STATUSES)


def _each_school(action: str, run: Any, tenant_ids: Any = None) -> dict[str, int]:
    if not service.available():
        return {"skipped": 1}
    totals = {"schools": 0, "failed": 0}
    for tenant_id in (tenant_ids or service.live_tenant_ids)():
        try:
            run(tenant_id)
            totals["schools"] += 1
        except Exception as exc:
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


@shared_task(name="messaging.sweep_inbox", acks_late=True, ignore_result=True)
def sweep_inbox() -> dict[str, int]:
    return _each_school("inbox", service.process_inbox)


def _sync(tenant_id: uuid.UUID) -> None:
    service.sync_account(tenant_id)
    service.sync_templates(tenant_id)


@shared_task(name="messaging.sync_accounts", acks_late=True, ignore_result=True)
def sync_accounts() -> dict[str, int]:
    return _each_school("sync", _sync)


def _purge(tenant_id: uuid.UUID) -> None:
    service.purge_expired(tenant_id)
    sends.purge_expired(tenant_id)


@shared_task(name="messaging.purge_expired", acks_late=True, ignore_result=True)
def purge_expired() -> dict[str, int]:
    return _each_school("purge", _purge, _purge_tenant_ids)


def rates_recheck_status(today: dt.date) -> dict[str, Any]:
    """Whether Meta may have changed its rates since ``config.yaml`` ``cost.rates_checked_on``
    (FR-MSG-031). Meta changes rates only on 1 Jan, 1 Apr, 1 Jul and 1 Oct."""
    cost = load_config().cost
    return {
        "due": cost.rates_recheck_due(today),
        "rates_checked_on": cost.rates_checked_on.isoformat(),
        "next_rate_change": cost.next_rate_change(cost.rates_checked_on).isoformat(),
        "unverified": sorted(k for k, r in cost.rates.items() if not r.verified),
    }


@shared_task(name="messaging.rates_recheck", acks_late=True, ignore_result=True)
def rates_recheck() -> dict[str, Any]:
    """The quarterly reminder (FR-MSG-031): a warning in the logs, which the SchoolOS team's
    alerting picks up, until the rates are rechecked and ``rates_checked_on`` updated. Runs
    whether or not messaging is on (the rates are SchoolOS-wide configuration)."""
    status = rates_recheck_status(dt.datetime.now(service.IST).date())
    if status["due"] or status["unverified"]:
        log.warning("messaging.rates_recheck_due", **status)
    return status


def beat_schedule() -> dict[str, dict[str, Any]]:
    """Inbox sweep every minute, account and template sync hourly, retention daily at 02:50 IST,
    the rates reminder at 09:00 IST on 1 Jan, 1 Apr, 1 Jul and 1 Oct (FR-MSG-031)."""
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
        "messaging-rates-recheck": {
            "task": "messaging.rates_recheck",
            "schedule": crontab(minute=30, hour=3, day_of_month=1, month_of_year="1,4,7,10"),
        },
        **sends_tasks.beat_schedule(),
    }


__all__ = [
    "beat_schedule",
    "process_inbox",
    "provision_templates",
    "purge_expired",
    "rates_recheck",
    "rates_recheck_status",
    "sweep_inbox",
    "sync_accounts",
]
