"""The early-warning jobs are registered, routed to "maintenance" and scheduled; attendance and
marks writes reach the rules through the outbox (FR-ATT-005, FR-EW-005, FR-EW-006, FR-EW-017)."""

from __future__ import annotations

from app.ops import service as ops
from sos_worker.celery_app import celery_app

TASKS = (
    "insights.evaluate_students",
    "insights.evaluate_all",
    "insights.send_flag_reminders",
    "insights.purge_expired",
)


def test_FR_EW_005_tasks_registered_and_routed_to_maintenance() -> None:
    celery_app.loader.import_default_modules()
    for name in TASKS:
        assert name in celery_app.tasks, name
        # send_task (outbox dispatcher) and beat honour task_routes, not the task's own queue.
        assert celery_app.amqp.router.route({}, name)["queue"].name == "maintenance", name


def test_FR_ATT_005_record_writes_and_concern_notes_reach_the_rules() -> None:
    celery_app.loader.import_default_modules()
    assert ops.OUTBOX_ROUTES["academics.records.changed"] == "insights.evaluate_students"
    assert ops.OUTBOX_ROUTES["insights.evaluate.requested"] == "insights.evaluate_students"


def test_FR_EW_006_daily_jobs_are_scheduled() -> None:
    schedule = celery_app.conf.beat_schedule
    assert schedule["insights-evaluate-daily"]["task"] == "insights.evaluate_all"
    assert schedule["insights-flag-reminders"]["task"] == "insights.send_flag_reminders"
    assert schedule["insights-purge-expired"]["task"] == "insights.purge_expired"
