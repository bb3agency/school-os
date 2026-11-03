"""Celery tasks for online payments (R2-2D; FR-PAY-006..012; docs/04 §6). IDs and counts only in
arguments, results and logs.

Outbox consumers (``app.ops``, relayed every 5 s):
- ``fees.process_payment_events`` (event ``fees.payments.inbox``): re-fetch and apply the school's
  stored Razorpay events (receipts within a minute of the webhook, US-2108 AC2);
- ``fees.send_online_refund`` (event ``fees.refund.send``): send an approved online refund to the
  provider (retried with backoff while the provider cannot be reached);
- ``fees.cancel_payment_links`` (event ``fees.payment_links.cancel``): cancel at the provider the
  links a counter receipt closed.

Beat: ``fees.sweep_payment_events`` every minute (events whose wake-up was lost and retries that
are due), ``fees.payments_hourly`` (expired links, refunds still in progress),
``fees.reconcile_online`` daily at 06:00 IST (FR-PAY-010) and ``fees.payments_daily`` at 03:10
IST (OAuth token refresh and expiry alerts; inbox retention, suspended and offboarding schools
included). Each school runs in its own ``tenant_session``; one failing school does not stop the
others; schools without a connection are skipped cheaply.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Any, Final

from celery import Task, shared_task
from celery.schedules import crontab

from app.core.db import context_free_session
from app.core.logging import get_logger
from app.fees import links, online, refund_hooks
from app.fees.providers import ProviderError
from app.ops.service import TenantTask
from app.tenancy import service as tenancy

log = get_logger(__name__)

MAX_RETRIES: Final = 6
SWEEP_TASK: Final = "fees.sweep_payment_events"
HOURLY_TASK: Final = "fees.payments_hourly"
RECONCILE_TASK: Final = "fees.reconcile_online"
DAILY_TASK: Final = "fees.payments_daily"


# Retention keeps running while a school is suspended or offboarding (docs/04 §6, docs/16 §5.5).
PURGE_TENANT_STATUSES: Final = ("active", "suspended", "offboarding")


def _purge_tenant_ids() -> list[uuid.UUID]:
    with context_free_session() as session:
        return tenancy.list_tenant_ids(session, PURGE_TENANT_STATUSES)


def _each_school(
    action: str,
    run: Callable[[uuid.UUID], object],
    tenant_ids: Callable[[], list[uuid.UUID]] | None = None,
) -> dict[str, int]:
    totals = {"schools": 0, "failed": 0}
    for tenant_id in (tenant_ids or online.connected_tenant_ids)():
        try:
            run(tenant_id)
            totals["schools"] += 1
        except Exception as exc:
            totals["failed"] += 1
            log.warning(
                "payments.school_task_failed",
                tenant_id=tenant_id,
                action=action,
                error_type=type(exc).__name__,
            )
    return totals


@shared_task(
    name=online.INBOX_TASK,
    base=TenantTask,
    bind=True,
    queue="maintenance",
    acks_late=True,
    ignore_result=True,
)
def process_payment_events(
    self: Task[Any, Any], tenant_id: str, event_id: str, payload: dict[str, Any]
) -> dict[str, int]:
    del self, event_id, payload
    return online.process_events(uuid.UUID(tenant_id))


@shared_task(
    name=refund_hooks.SEND_TASK,
    base=TenantTask,
    bind=True,
    queue="maintenance",
    acks_late=True,
    max_retries=MAX_RETRIES,
)
def send_online_refund(
    self: Task[Any, Any], tenant_id: str, event_id: str, payload: dict[str, Any]
) -> str:
    del event_id
    try:
        return online.send_refund(uuid.UUID(tenant_id), uuid.UUID(str(payload["refund_id"])))
    except ProviderError as exc:  # transient: provider unreachable or rate limited
        raise self.retry(exc=exc, countdown=min(60 * 2**self.request.retries, 3600)) from exc


@shared_task(
    name=links.CANCEL_TASK,
    base=TenantTask,
    bind=True,
    queue="maintenance",
    acks_late=True,
    max_retries=MAX_RETRIES,
)
def cancel_payment_links(
    self: Task[Any, Any], tenant_id: str, event_id: str, payload: dict[str, Any]
) -> int:
    del event_id
    ids = [uuid.UUID(str(v)) for v in payload.get("intent_ids") or []]
    try:
        return online.cancel_links_at_provider(uuid.UUID(tenant_id), ids)
    except ProviderError as exc:
        raise self.retry(exc=exc, countdown=min(60 * 2**self.request.retries, 3600)) from exc


@shared_task(name=SWEEP_TASK, acks_late=True, ignore_result=True)
def sweep_payment_events() -> dict[str, int]:
    return _each_school("inbox", online.process_events)


def _hourly(tenant_id: uuid.UUID) -> None:
    online.expire_intents(tenant_id)
    online.poll_refunds(tenant_id)


@shared_task(name=HOURLY_TASK, acks_late=True, ignore_result=True)
def payments_hourly() -> dict[str, int]:
    return _each_school("hourly", _hourly)


@shared_task(name=RECONCILE_TASK, queue="maintenance", acks_late=True, ignore_result=True)
def reconcile_online() -> dict[str, int]:
    return _each_school("reconcile", online.reconcile)


@shared_task(name=DAILY_TASK, queue="maintenance", acks_late=True, ignore_result=True)
def payments_daily() -> dict[str, int]:
    """Token refresh for connected active schools; inbox retention for every school that keeps
    data (a failed refresh no longer holds back the purge)."""
    tokens = _each_school("daily", online.refresh_tokens)
    purge = _each_school("purge", online.purge_events, _purge_tenant_ids)
    return {
        "schools": tokens["schools"],
        "purged_schools": purge["schools"],
        "failed": tokens["failed"] + purge["failed"],
    }


def beat_schedule() -> dict[str, dict[str, Any]]:
    """The beat runs in UTC: 00:30 UTC = 06:00 IST (reconciliation); 21:40 UTC = 03:10 IST."""
    return {
        "fees-sweep-payment-events": {"task": SWEEP_TASK, "schedule": 60.0},
        "fees-payments-hourly": {"task": HOURLY_TASK, "schedule": crontab(minute=17)},
        "fees-reconcile-online": {
            "task": RECONCILE_TASK,
            "schedule": crontab(hour=0, minute=30),
        },
        "fees-payments-daily": {"task": DAILY_TASK, "schedule": crontab(hour=21, minute=40)},
    }


__all__ = [
    "beat_schedule",
    "cancel_payment_links",
    "payments_daily",
    "payments_hourly",
    "process_payment_events",
    "reconcile_online",
    "send_online_refund",
    "sweep_payment_events",
]
