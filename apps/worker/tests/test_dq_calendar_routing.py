"""The portal calendar reminder job is registered, routed to "maintenance" (database work only,
not the "dq" check queue) and scheduled every morning at 07:30 IST (FR-RDY-006, ADR-0053)."""

from __future__ import annotations

from celery.schedules import crontab

from app.dq import tasks as dq_tasks
from sos_worker.celery_app import celery_app

TASK = "dq.send_portal_reminders"


def test_FR_RDY_006_reminder_job_is_registered_and_routed_to_maintenance() -> None:
    celery_app.loader.import_default_modules()
    assert TASK in celery_app.tasks
    assert dq_tasks.PORTAL_REMINDER_TASK == TASK
    # Beat honours task_routes, not the task's own queue; the other dq tasks stay on "dq".
    assert celery_app.amqp.router.route({}, TASK)["queue"].name == "maintenance"
    assert celery_app.amqp.router.route({}, "dq.execute_run")["queue"].name == "dq"


def test_FR_RDY_006_reminders_run_daily_at_0730_ist() -> None:
    entry = celery_app.conf.beat_schedule["dq-portal-reminders"]
    assert entry["task"] == TASK
    # 07:30 IST = 02:00 UTC (the worker runs in UTC).
    assert entry["schedule"] == crontab(minute=0, hour=2)


def test_D31_only_active_schools_are_reminded() -> None:
    assert dq_tasks.TENANT_STATUSES == ("active",)
