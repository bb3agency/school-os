"""Pulling the control plane's AI answer quota alerts and plan-limit crossings into each
school's in-app notifications (ADR-0038 follow-up, docs/16 §5.10, §19 Q16; FR-PLT-020,
FR-PLT-021, FR-NOT-001; D57).

The breach-notice pattern (``app.privacy.breach_sync``): the usage collector records the first
crossing of 80 % and of 100 % of a bundle's included answers per school and calendar month, and
of each plan limit per school and billing period, in the control plane
(``platform.usage_threshold_events``). These school-side sweeps read this school's highest
alerts through ``app.platform.service`` (as ``sos_platform``; codes and counts, nothing else)
and notify the school's owner and principal in the school's own ``tenant_session``. The control
plane never writes tenant tables (ADR-0020). Each sweep reads the previous and the current month
(or billing period): a month's last day is recorded on the 1st under that month, and the
previous period's crossings must not be lost at the roll (D57). Idempotent per recipient
through the notification ``dedupe_key`` (period, metric and threshold): repeated sweeps add
nothing. Alert only: nothing is switched off at 100 % and limits never block school work.

Shared deployment only: a dedicated host has no control plane. This is the only file of
``app.notifications`` that imports the control plane (import-linter contract
``tenant-side-without-control-plane``).
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Callable
from typing import Any, Final

from celery import shared_task
from sqlalchemy.orm import Session

from app.core.config import DeploymentMode, Settings, get_settings
from app.core.db import context_free_session, tenant_session
from app.core.logging import get_logger
from app.core.report_format import IST
from app.identity import service as identity
from app.notifications import service as notifications
from app.platform import service as control_plane
from app.tenancy import service as tenancy

log = get_logger(__name__)

TEMPLATE: Final = "billing.ai_answers_quota"
PLAN_LIMIT_TEMPLATE: Final = "billing.plan_limit"
# The school's owner and principal (system roles), never the whole billing audience.
RECIPIENT_ROLES: Final = frozenset({"owner", "principal"})
TENANT_STATUSES: Final = ("active", "suspended")
TASK: Final = "notifications.ai_quota_sweep"
PLAN_LIMIT_TASK: Final = "notifications.plan_limit_sweep"
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


def _notify(tenant_id: uuid.UUID, items: list[tuple[str, dict[str, Any]]], template: str) -> int:
    """Notify the owner and principal once per ``(dedupe_key, params)`` item."""
    if not items:
        return 0
    created = 0
    with tenant_session(tenant_id) as session:
        recipients = _owner_and_principal(session)
        if not recipients:
            return 0
        for dedupe_key, params in items:
            created += notifications.notify(
                session,
                tenant_id=tenant_id,
                recipients=recipients,
                template_key=template,
                params=params,
                dedupe_key=dedupe_key,
            )
    return created


def sync_school(tenant_id: uuid.UUID, *, today: dt.date | None = None) -> int:
    """Notify this school's owner and principal of the highest AI answer quota alert of the
    previous and of the current month, once per month and threshold. Returns how many
    notifications were created."""
    if not control_plane_here():
        return 0
    month = (today or dt.datetime.now(IST).date()).replace(day=1)
    items: list[tuple[str, dict[str, Any]]] = []
    for alert in control_plane.ai_quota_alerts_for_school(tenant_id):
        level = "used_up" if alert.threshold >= 100 else "near"
        if alert.month_start < month:
            level += "_last_month"
        items.append(
            (
                f"billing.ai_quota:{alert.month_start:%Y-%m}:{alert.threshold}",
                {"level": level, "included": alert.included_answers},
            )
        )
    created = _notify(tenant_id, items, TEMPLATE)
    if created:
        log.info("notifications.ai_quota_alert_sent", count=created)
    return created


def sync_plan_limits(tenant_id: uuid.UUID) -> int:
    """Notify this school's owner and principal of its plan-limit crossings (the highest
    threshold per metric in the previous and the current billing period), once per period,
    metric and threshold. Returns how many notifications were created."""
    if not control_plane_here():
        return 0
    items: list[tuple[str, dict[str, Any]]] = [
        (
            f"billing.plan_limit:{a.period_start:%Y-%m-%d}:{a.metric}:{a.threshold}",
            {"metric": a.metric, "threshold": a.threshold},
        )
        for a in control_plane.plan_limit_alerts_for_school(tenant_id)
    ]
    created = _notify(tenant_id, items, PLAN_LIMIT_TEMPLATE)
    if created:
        log.info("notifications.plan_limit_alert_sent", count=created)
    return created


def _sweep(sync: Callable[[uuid.UUID], int], failure_event: str) -> dict[str, int]:
    if not control_plane_here():
        return {"tenants": 0, "sent": 0, "failed": 0}
    with context_free_session() as session:
        tenant_ids = tenancy.list_tenant_ids(session, TENANT_STATUSES)
    totals = {"tenants": len(tenant_ids), "sent": 0, "failed": 0}
    for tenant_id in tenant_ids:
        try:
            totals["sent"] += sync(tenant_id)
        except Exception as exc:  # this school only; the next sweep retries
            totals["failed"] += 1
            log.warning(failure_event, tenant_id=tenant_id, error_type=type(exc).__name__)
    return totals


def sweep_all() -> dict[str, int]:
    return _sweep(sync_school, "notifications.ai_quota_sweep_failed")


def sweep_plan_limits() -> dict[str, int]:
    return _sweep(sync_plan_limits, "notifications.plan_limit_sweep_failed")


@shared_task(name=TASK, acks_late=True, ignore_result=True)
def sweep() -> dict[str, int]:
    return sweep_all()


@shared_task(name=PLAN_LIMIT_TASK, acks_late=True, ignore_result=True)
def plan_limit_sweep() -> dict[str, int]:
    return sweep_plan_limits()


def beat_schedule(settings: Settings | None = None) -> dict[str, dict[str, Any]]:
    """Beat entry on the shared tier only (a dedicated host has no control plane)."""
    if not control_plane_here(settings):
        return {}
    return {
        "notifications-ai-quota-sweep": {
            "task": TASK,
            "schedule": SWEEP_SECONDS,
            "options": {"queue": "maintenance"},
        },
        "notifications-plan-limit-sweep": {
            "task": PLAN_LIMIT_TASK,
            "schedule": SWEEP_SECONDS,
            "options": {"queue": "maintenance"},
        },
    }


__all__ = [
    "beat_schedule",
    "plan_limit_sweep",
    "sweep",
    "sweep_all",
    "sweep_plan_limits",
    "sync_plan_limits",
    "sync_school",
]
