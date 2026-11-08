"""Celery tasks for notifications (FR-NOT-001). IDs and counts only in arguments and results.

- ``notifications.purge_read`` (daily): per school, delete notifications read more than
  ``retention.read_days`` (90) days ago, each school in its own ``tenant_session``.
- ``notifications.send_email`` (queue ``maintenance``): consumer of the outbox event
  ``notification.email.requested`` (IDs only; a 100 % alert email also names its in-app
  notice, D70); renders and sends one email through the
  configured provider (``app.notifications.email``). Retries with backoff while the provider is
  unavailable; a permanent refusal is logged with its code and not retried. At most once per
  outbox event (audit YM-06): the event is claimed before the provider call, a run of an event
  already claimed sends nothing, and an unknown outcome is not retried. Addresses and
  message text are never logged or returned.
"""

from __future__ import annotations

import uuid
from typing import Any

from celery import Task, shared_task
from celery.schedules import crontab

from app.core.db import context_free_session, tenant_session
from app.core.logging import get_logger
from app.notifications import email, service
from app.ops.service import TenantTask
from app.tenancy import service as tenancy

log = get_logger(__name__)
TENANT_STATUSES = ("active", "suspended", "offboarding")
EMAIL_MAX_RETRIES = 6


def purge_all() -> dict[str, int]:
    with context_free_session() as session:
        tenant_ids = tenancy.list_tenant_ids(session, TENANT_STATUSES)
    purged = failed = 0
    for tenant_id in tenant_ids:
        try:
            with tenant_session(tenant_id) as session:
                purged += service.purge_read(session)
        except Exception as exc:  # database: this school only (rolled back), retried next run
            failed += 1
            log.warning(
                "notifications.purge_failed", tenant_id=tenant_id, error_type=type(exc).__name__
            )
    log.info("notifications.purged", count=purged, failed=failed)
    return {"tenants": len(tenant_ids), "purged": purged, "failed": failed}


@shared_task(name="notifications.purge_read", acks_late=True)
def purge_read() -> dict[str, int]:
    return purge_all()


@shared_task(
    name=service.EMAIL_TASK,
    base=TenantTask,
    bind=True,
    queue="maintenance",
    acks_late=True,
    max_retries=EMAIL_MAX_RETRIES,
    ignore_result=True,
)
def send_email(self: Task[Any, Any], tenant_id: str, event_id: str, payload: dict[str, Any]) -> str:
    user_id = uuid.UUID(str(payload["user_id"]))
    template_key = str(payload["template_key"])
    notice = payload.get("notification_id")
    try:
        return service.send_requested_email(
            uuid.UUID(str(tenant_id)),
            user_id,
            template_key,
            notification_id=uuid.UUID(str(notice)) if notice else None,
            event_id=uuid.UUID(str(event_id)),
        )
    except email.EmailRejected as exc:
        log.error("notifications.email.rejected", action=template_key, error_code=exc.code)
        return "rejected"
    except email.EmailOutcomeUnknown as exc:
        # Audit YM-06: it may have been sent; a retry would only skip it (at most once).
        log.error(
            "notifications.email.outcome_unknown",
            action=template_key,
            resource_id=event_id,
            error_code=exc.code,
        )
        return "unknown"
    except email.EmailUnavailable as exc:
        if self.request.retries >= EMAIL_MAX_RETRIES:
            log.error("notifications.email.failed", action=template_key, error_code=exc.code)
            return "failed"
        raise self.retry(exc=exc, countdown=min(30 * 2**self.request.retries, 1800)) from None


def beat_schedule() -> dict[str, dict[str, Any]]:
    """Beat entries for notifications (both deployment modes)."""
    return {
        "notifications-purge-read": {
            "task": "notifications.purge_read",
            "schedule": crontab(minute=20, hour=21),  # 02:50 IST
        },
    }
