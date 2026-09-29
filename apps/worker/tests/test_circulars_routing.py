"""The circulars tasks are registered and routed (docs/04 §6; send_task honours task_routes,
not the task's own queue): the AI reading and the parent-notice draft run on "ingest" next to
the knowledge jobs (FR-CIR-002, FR-NOTICE-003), notice files on the Chromium workers ("pdf",
FR-NOTICE-006)."""

from __future__ import annotations

import pytest

from app.ops import service as ops
from sos_worker.celery_app import celery_app


@pytest.mark.parametrize(
    ("event", "task", "queue"),
    [
        ("circulars.read.requested", "circulars.read_version", "ingest"),
        ("circulars.notice.draft_requested", "circulars.draft_notice", "ingest"),
        ("circulars.notice.render_requested", "circulars.render_notice", "pdf"),
    ],
)
def test_FR_NOTICE_003_outbox_events_reach_their_tasks_on_their_queues(
    event: str, task: str, queue: str
) -> None:
    celery_app.loader.import_default_modules()
    assert task in celery_app.tasks
    assert ops.OUTBOX_ROUTES[event] == task
    assert celery_app.amqp.router.route({}, task)["queue"].name == queue
