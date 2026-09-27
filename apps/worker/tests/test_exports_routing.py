"""Export tasks are registered and routed (docs/04 §6: spreadsheets on "exports", anything with a
PDF on "pdf" where Chromium runs; FR-EXP-002)."""

from __future__ import annotations

import pytest

from app.ops import service as ops
from sos_worker.celery_app import celery_app


@pytest.mark.parametrize(
    ("task", "queue"),
    [
        ("exports.generate", "exports"),
        ("exports.render", "pdf"),
        ("exports.purge_expired", "maintenance"),
    ],
)
def test_FR_EXP_002_export_tasks_registered_and_routed(task: str, queue: str) -> None:
    celery_app.loader.import_default_modules()
    assert task in celery_app.tasks
    # send_task (used by the outbox dispatcher) honours task_routes, not the task's own queue.
    assert celery_app.amqp.router.route({}, task)["queue"].name == queue


def test_FR_EXP_002_outbox_events_reach_the_export_tasks() -> None:
    celery_app.loader.import_default_modules()
    assert ops.OUTBOX_ROUTES["export.requested"] == "exports.generate"
    assert ops.OUTBOX_ROUTES["export.render_requested"] == "exports.render"
    assert "exports-purge-expired" in celery_app.conf.beat_schedule
