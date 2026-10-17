"""Celery tasks for public handles (FR-PUB-006; docs/05 §15.5). IDs and counts only.

- ``public.purge_handles`` (daily): for each school, delete the parent link handles that expired
  more than ``purge_after_days`` ago (the database refuses anything sooner). Each school runs in
  its own transactions (``core.list_tenant_ids`` then one ``tenant_session`` per school); one
  failing school does not stop the others. Offboarding schools are left to the offboarding
  purge (ADR-0029).
"""

from __future__ import annotations

from typing import Any

from celery import shared_task
from celery.schedules import crontab
from sqlalchemy.exc import SQLAlchemyError

from app.core.db import context_free_session
from app.core.logging import get_logger
from app.public import service
from app.tenancy import service as tenancy

log = get_logger(__name__)
TENANT_STATUSES = ("active", "suspended")


def purge_all() -> dict[str, int]:
    with context_free_session() as session:
        tenant_ids = tenancy.list_tenant_ids(session, TENANT_STATUSES)
    totals = {"tenants": len(tenant_ids), "deleted": 0, "failed": 0}
    for tenant_id in tenant_ids:
        try:
            totals["deleted"] += service.purge_expired_handles(tenant_id)
        except SQLAlchemyError as exc:
            totals["failed"] += 1
            log.warning("public.purge_failed", tenant_id=tenant_id, error_type=type(exc).__name__)
    log.info("public.purge_done", count=totals["deleted"], outcome="ok")
    return totals


@shared_task(name="public.purge_handles", acks_late=True, ignore_result=True)
def purge_handles() -> dict[str, int]:
    return purge_all()


def beat_schedule() -> dict[str, dict[str, Any]]:
    """Daily at 21:15 UTC (02:45 IST), after the other nightly maintenance jobs."""
    return {
        "public-purge-handles": {
            "task": "public.purge_handles",
            "schedule": crontab(minute=15, hour=21),
        },
    }


__all__ = ["beat_schedule", "purge_all", "purge_handles"]
