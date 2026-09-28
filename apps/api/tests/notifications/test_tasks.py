"""Notification jobs are registered with the worker (FR-NOT-001 retention)."""

from __future__ import annotations

from app.notifications.tasks import beat_schedule


def test_FR_NOT_001_purge_is_scheduled_daily() -> None:
    entry = beat_schedule()["notifications-purge-read"]
    assert entry["task"] == "notifications.purge_read"


def test_FR_NOT_001_worker_imports_and_schedules_notification_tasks() -> None:
    from sos_worker.celery_app import TASK_MODULES, celery_app

    assert "app.notifications.tasks" in TASK_MODULES
    assert "notifications-purge-read" in celery_app.conf.beat_schedule


def test_US_102_email_requests_route_to_the_send_task() -> None:
    from app.notifications import service
    from app.ops import service as ops
    from sos_worker.celery_app import celery_app

    assert ops.OUTBOX_ROUTES[service.EMAIL_EVENT] == service.EMAIL_TASK
    assert service.EMAIL_TASK == "notifications.send_email"
    assert service.EMAIL_TASK in celery_app.tasks
