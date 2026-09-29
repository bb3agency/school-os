"""Admin tasks are registered, routed and scheduled (FR-ADM-001: the full export runs on
"exports"; the archive purge on "maintenance", hourly), and the worker loads the retention
provider the other modules' purge jobs read (FR-ADM-002)."""

from __future__ import annotations

import pytest

from app.core import retention
from app.ops import service as ops
from sos_worker.celery_app import celery_app


@pytest.mark.parametrize(
    ("task", "queue"),
    [
        ("admin.tenant_export", "exports"),
        ("admin.purge_tenant_exports", "maintenance"),
    ],
)
def test_FR_ADM_001_admin_tasks_registered_and_routed(task: str, queue: str) -> None:
    celery_app.loader.import_default_modules()
    assert task in celery_app.tasks
    # send_task (used by the outbox dispatcher) honours task_routes, not the task's own queue.
    assert celery_app.amqp.router.route({}, task)["queue"].name == queue


def test_FR_ADM_001_outbox_event_reaches_the_export_task_and_purge_is_scheduled() -> None:
    celery_app.loader.import_default_modules()
    assert ops.OUTBOX_ROUTES["admin.tenant_export.requested"] == "admin.tenant_export"
    entry = celery_app.conf.beat_schedule["admin-purge-tenant-exports"]
    assert entry["task"] == "admin.purge_tenant_exports"


def test_FR_ADM_001_export_task_has_longer_time_limits_than_the_default() -> None:
    celery_app.loader.import_default_modules()
    task = celery_app.tasks["admin.tenant_export"]
    assert task.time_limit is not None
    assert task.soft_time_limit is not None
    assert task.soft_time_limit < task.time_limit
    assert task.time_limit > celery_app.conf.task_time_limit


def test_FR_ADM_002_worker_registers_the_retention_provider() -> None:
    """The purge jobs in imports, exports and notifications ask app.core.retention; without the
    admin module's provider they would silently ignore the school's setting."""
    celery_app.loader.import_default_modules()
    from app.admin import service

    assert service._retention_provider in retention.providers()
