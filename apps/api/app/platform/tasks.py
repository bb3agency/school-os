"""Celery tasks for the control plane (shared deployment) and the dedicated-host heartbeat client.

Beat entries come from :func:`beat_schedule`, which registers control-plane jobs only when
``SOS_DEPLOYMENT_MODE=shared`` and the heartbeat client only when ``dedicated`` (ADR-0017);
the school-chain audit delivery runs in both modes (ADR-0020).
Times are UTC (celery ``timezone=UTC``); IST = UTC + 5:30. Tasks carry IDs/dates only.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from celery import shared_task
from celery.schedules import crontab

from app.core.config import DeploymentMode, Settings, get_settings
from app.platform import (
    announcements,
    billing,
    fleet,
    heartbeat_client,
    invoice_files,
    support,
    tenant_audit,
    usage,
)
from app.platform.common import today_ist


@shared_task(name="billing.generate_invoices", acks_late=True)
def generate_invoices(month: str | None = None) -> dict[str, Any]:
    """Daily at 02:00 IST; only the first run of a month does work (idempotent per month)."""
    target = month or today_ist().strftime("%Y-%m")
    job = billing.generate_invoices(target)
    return {"job_id": str(job.id), "status": job.status}


@shared_task(name="billing.daily", acks_late=True)
def billing_daily() -> dict[str, int]:
    """06:00 IST: roll ended periods, then mark past-due subscriptions (never suspends)."""
    return {"rolled": billing.roll_periods(), "past_due": billing.mark_past_due()}


@shared_task(name="billing.render_invoice_pdfs", queue="pdf", acks_late=True, ignore_result=True)
def render_invoice_pdfs() -> dict[str, int]:
    """Every minute on the ``pdf`` queue (Chromium workers, ADR-0025): render and store the PDFs
    of issued invoices that have none yet (idempotent per invoice; docs/16 §5.8)."""
    result = invoice_files.render_pending()
    return {"rendered": result.rendered, "existing": result.existing, "failed": result.failed}


@shared_task(name="usage.collect_daily", acks_late=True)
def collect_usage(day: str | None = None) -> dict[str, int]:
    return {"tenants": usage.collect_daily(dt.date.fromisoformat(day) if day else None)}


@shared_task(name="fleet.check_staleness", acks_late=True)
def check_staleness() -> dict[str, int]:
    return {"unreachable": fleet.check_staleness()}


@shared_task(name="announcements.publish", acks_late=True, ignore_result=True)
def publish_announcements() -> dict[str, int]:
    return {"active": announcements.publish()}


@shared_task(name="support.purge_closed", acks_late=True)
def purge_tickets() -> dict[str, int]:
    return {"purged": support.purge_closed()}


@shared_task(name="fleet.send_heartbeat", acks_late=True, ignore_result=True)
def send_heartbeat() -> dict[str, Any]:
    """Dedicated hosts: outbound heartbeat to the control plane every 5 minutes."""
    return heartbeat_client.send()


@shared_task(name="platform.deliver_tenant_audit", acks_late=True, ignore_result=True)
def deliver_tenant_audit() -> dict[str, int]:
    """Both modes, every minute (queue ``maintenance``): copy queued platform actions into the
    schools' own audit chains exactly once, in order per school (ADR-0020, FR-AUD-001)."""
    result = tenant_audit.deliver_pending()
    return {
        "delivered": result.delivered,
        "already_present": result.already_present,
        "failed": result.failed,
        "backlog": tenant_audit.check_backlog(),
    }


# Both deployment modes: the dedicated host's own provisioning queues school-chain copies too.
_BOTH_MODES: dict[str, dict[str, Any]] = {
    "platform-deliver-tenant-audit": {
        "task": "platform.deliver_tenant_audit",
        "schedule": 60.0,
        "options": {"queue": "maintenance"},
    },
}


def beat_schedule(settings: Settings | None = None) -> dict[str, dict[str, Any]]:
    settings = settings or get_settings()
    if settings.deployment_mode is DeploymentMode.DEDICATED:
        return {
            "fleet-send-heartbeat": {"task": "fleet.send_heartbeat", "schedule": 300.0},
            **_BOTH_MODES,
        }
    return {
        **_BOTH_MODES,
        "billing-generate-invoices": {
            "task": "billing.generate_invoices",
            "schedule": crontab(minute=30, hour=20),  # 02:00 IST
        },
        "billing-daily": {"task": "billing.daily", "schedule": crontab(minute=30, hour=0)},
        "billing-render-invoice-pdfs": {
            "task": "billing.render_invoice_pdfs",
            "schedule": 60.0,
            "options": {"queue": "pdf"},
        },
        "usage-collect-daily": {
            "task": "usage.collect_daily",
            "schedule": crontab(minute=0, hour=20),  # 01:30 IST
        },
        "fleet-check-staleness": {"task": "fleet.check_staleness", "schedule": 300.0},
        "announcements-publish": {"task": "announcements.publish", "schedule": 60.0},
        "support-purge-closed": {
            "task": "support.purge_closed",
            "schedule": crontab(minute=15, hour=21),  # 02:45 IST
        },
    }
