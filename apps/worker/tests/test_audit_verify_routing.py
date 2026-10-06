"""An on-demand audit-chain check queued by POST /audit/verify reaches its task on the
"maintenance" queue (audit 2026-10-06 R-19; FR-AUD-004)."""

from __future__ import annotations

from app.audit import verification
from app.ops import service as ops
from sos_worker.celery_app import celery_app


def test_R_19_verify_request_is_routed_to_the_verify_task() -> None:
    celery_app.loader.import_default_modules()
    assert ops.OUTBOX_ROUTES[verification.VERIFY_EVENT] == verification.VERIFY_TASK
    assert verification.VERIFY_TASK in celery_app.tasks
    # send_task (used by the outbox dispatcher) honours task_routes, not the task's own queue.
    assert celery_app.amqp.router.route({}, verification.VERIFY_TASK)["queue"].name == "maintenance"
