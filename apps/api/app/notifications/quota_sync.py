"""Pulling the control plane's AI answer quota alerts into each school's in-app notifications
(ADR-0038 follow-up, docs/16 §19 Q16; FR-PLT-020, FR-NOT-001).

The breach-notice pattern (``app.privacy.breach_sync``): the usage collector records the first
crossing of 80 % and of 100 % of a bundle's included answers per school and calendar month in
the control plane (``platform.usage_threshold_events``). This school-side sweep reads this
school's highest alert of the current month through ``app.platform.service`` (as
``sos_platform``; a level and two counts, nothing else) and notifies the school's owner and
principal in the school's own ``tenant_session``. The control plane never writes tenant tables
(ADR-0020). Idempotent per recipient through the notification ``dedupe_key`` (month and level):
repeated sweeps add nothing. Alert only: nothing is switched off at 100 %.

Shared deployment only: a dedicated host has no control plane. This is the only file of
``app.notifications`` that imports the control plane (import-linter contract
``tenant-side-without-control-plane``).
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any, Final

from celery import shared_task
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.config import DeploymentMode, Settings, get_settings
from app.core.db import context_free_session, tenant_session
from app.core.errors import DomainError
from app.core.logging import get_logger
from app.identity import service as identity
from app.notifications import service as notifications
from app.platform import service as control_plane
from app.tenancy import service as tenancy

log = get_logger(__name__)

TEMPLATE: Final = "billing.ai_answers_quota"
# The school's owner and principal (system roles), never the whole billing audience.
RECIPIENT_ROLES: Final = frozenset({"owner", "principal"})
TENANT_STATUSES: Final = ("active", "suspended")
TASK: Final = "notifications.ai_quota_sweep"
# The collector records crossings once a day (01:30 IST); an hourly pull is ample.
SWEEP_SECONDS: Final = 3600.0
_PAGE: Final = 200


def control_plane_here(settings: Settings | None = None) -> bool:
    return (settings or get_settings()).deployment_mode is DeploymentMode.SHARED


def _owner_and_principal(session: Session) -> list[uuid.UUID]:
    now = dt.datetime.now(dt.UTC)
    out: list[uuid.UUID] = []
    after: uuid.UUID | None = None
    while True:
        users, after = identity.list_users(session, limit=_PAGE, after=after)
        out.extend(
            u.membership_id
            for u in users
            if u.status == "active"
            and (u.expires_at is None or u.expires_at > now)
            and RECIPIENT_ROLES.intersection(u.roles)
        )
        if after is None:
            return out


def sync_school(tenant_id: uuid.UUID) -> int:
    """Notify this school's owner and principal of this month's highest AI answer quota alert,
    once per month and level. Returns how many notifications were created."""
    if not control_plane_here():
        return 0
    alert = control_plane.ai_quota_alert_for_school(tenant_id)
    if alert is None:
        return 0
    level = "used_up" if alert.threshold >= 100 else "near"
    with tenant_session(tenant_id) as session:
        recipients = _owner_and_principal(session)
        if not recipients:
            return 0
        created = notifications.notify(
            session,
            tenant_id=tenant_id,
            recipients=recipients,
            template_key=TEMPLATE,
            params={"level": level, "included": alert.included_answers},
            dedupe_key=f"billing.ai_quota:{alert.month_start:%Y-%m}:{alert.threshold}",
        )
    if created:
        log.info("notifications.ai_quota_alert_sent", count=created, action=level)
    return created


def sweep_all() -> dict[str, int]:
    if not control_plane_here():
        return {"tenants": 0, "sent": 0, "failed": 0}
    with context_free_session() as session:
        tenant_ids = tenancy.list_tenant_ids(session, TENANT_STATUSES)
    totals = {"tenants": len(tenant_ids), "sent": 0, "failed": 0}
    for tenant_id in tenant_ids:
        try:
            totals["sent"] += sync_school(tenant_id)
        except (SQLAlchemyError, DomainError) as exc:  # this school only; the next sweep retries
            totals["failed"] += 1
            log.warning(
                "notifications.ai_quota_sweep_failed",
                tenant_id=tenant_id,
                error_type=type(exc).__name__,
            )
    return totals


@shared_task(name=TASK, acks_late=True, ignore_result=True)
def sweep() -> dict[str, int]:
    return sweep_all()


def beat_schedule(settings: Settings | None = None) -> dict[str, dict[str, Any]]:
    """Beat entry on the shared tier only (a dedicated host has no control plane)."""
    if not control_plane_here(settings):
        return {}
    return {
        "notifications-ai-quota-sweep": {
            "task": TASK,
            "schedule": SWEEP_SECONDS,
            "options": {"queue": "maintenance"},
        }
    }


__all__ = ["beat_schedule", "sweep", "sweep_all", "sync_school"]
