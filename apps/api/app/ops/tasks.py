"""Celery tasks for ``ops`` (FR-OPS-004). IDs only in arguments and results."""

from __future__ import annotations

from typing import Any

from celery import current_app, shared_task
from celery.schedules import crontab

from app.core.logging import get_logger
from app.ops import service

log = get_logger(__name__)


def _send(task_name: str, kwargs: dict[str, Any]) -> None:
    current_app.send_task(task_name, kwargs=kwargs)


@shared_task(name="ops.dispatch_outbox", acks_late=True, ignore_result=True)
def dispatch_outbox(batch: int = 100) -> dict[str, int]:
    """Relay pending outbox events to their consumers (loops while full batches come back)."""
    total = sent = unrouted = 0
    for _ in range(20):
        result = service.dispatch_outbox(_send, batch=batch)
        total += result.claimed
        sent += result.sent
        unrouted += result.unrouted
        if result.claimed < batch:
            break
    if total:
        log.info("ops.outbox.dispatched", count=sent, outcome="ok" if not unrouted else "unrouted")
    return {"claimed": total, "sent": sent, "unrouted": unrouted}


@shared_task(name="ops.purge_idempotency_keys", acks_late=True)
def purge_idempotency_keys() -> dict[str, int]:
    return {"purged": service.purge_idempotency_keys()}


def beat_schedule() -> dict[str, dict[str, Any]]:
    """Beat entries for ops (both deployment modes)."""
    return {
        "ops-dispatch-outbox": {"task": "ops.dispatch_outbox", "schedule": 5.0},
        "ops-purge-idempotency": {
            "task": "ops.purge_idempotency_keys",
            "schedule": crontab(minute=10, hour=21),  # 02:40 IST
        },
    }
