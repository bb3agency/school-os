"""Deferred budget settlements (audit 2026-10-04 W3-10; FR-KB-011): the task is registered,
runs on "maintenance" (never the ingestion queue) and is fed by its outbox event."""

from __future__ import annotations

from sos_worker.celery_app import celery_app


def test_W3_10_settle_spend_registered_routed_and_fed_by_the_outbox() -> None:
    from app.ops import service as ops

    celery_app.loader.import_default_modules()
    assert "knowledge.settle_spend" in celery_app.tasks
    route = celery_app.amqp.router.route
    assert route({}, "knowledge.settle_spend")["queue"].name == "maintenance"
    assert ops.OUTBOX_ROUTES["kb.budget.settle_requested"] == "knowledge.settle_spend"
