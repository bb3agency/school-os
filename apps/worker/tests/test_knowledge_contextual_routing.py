"""The contextual-header backfill is registered, routed to "ingest" by an explicit route and
scheduled (docs/06 §4.11; FR-KB-001). It is a no-op while contextual chunks are off."""

from __future__ import annotations

from sos_worker.celery_app import celery_app


def test_FR_KB_001_contextual_backfill_registered_routed_and_scheduled() -> None:
    celery_app.loader.import_default_modules()
    assert "knowledge.contextualize_backfill" in celery_app.tasks
    routes = celery_app.conf.task_routes
    assert routes["knowledge.contextualize_backfill"] == {"queue": "ingest"}
    route = celery_app.amqp.router.route({}, "knowledge.contextualize_backfill")
    assert route["queue"].name == "ingest"
    entry = celery_app.conf.beat_schedule["knowledge-contextualize-backfill"]
    assert entry["task"] == "knowledge.contextualize_backfill"
    assert entry["schedule"] == 3600.0  # contextual.yaml backfill.every_minutes = 60
