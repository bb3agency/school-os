"""The weekly management brief job is registered, routed to "maintenance" and scheduled on
Monday mornings at 07:45 IST, for active schools only (FA-05, FR-BRF-010)."""

from __future__ import annotations

from celery.schedules import crontab

from app.brief import tasks as brief_tasks
from sos_worker.celery_app import celery_app

TASK = "brief.build_weekly"


def test_FR_BRF_010_weekly_job_is_registered_and_routed_to_maintenance() -> None:
    celery_app.loader.import_default_modules()
    assert TASK in celery_app.tasks
    assert brief_tasks.WEEKLY_TASK == TASK
    assert celery_app.amqp.router.route({}, TASK)["queue"].name == "maintenance"


def test_FR_BRF_010_runs_on_mondays_at_0745_ist() -> None:
    entry = celery_app.conf.beat_schedule["brief-weekly"]
    assert entry["task"] == TASK
    # 07:45 IST = 02:15 UTC (the worker runs in UTC), Mondays.
    assert entry["schedule"] == crontab(minute=15, hour=2, day_of_week=1)


def test_FR_BRF_010_only_active_schools_get_a_brief() -> None:
    assert brief_tasks.TENANT_STATUSES == ("active",)
