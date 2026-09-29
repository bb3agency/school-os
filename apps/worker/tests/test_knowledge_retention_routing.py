"""The query-log purge is registered, routed to "maintenance" (not "ingest") and scheduled
daily (docs/05 §13, docs/08 §7: Ask-the-school questions and answers kept 180 days)."""

from __future__ import annotations

from sos_worker.celery_app import celery_app


def test_FR_ADM_002_query_log_purge_registered_routed_and_scheduled() -> None:
    celery_app.loader.import_default_modules()
    assert "knowledge.purge_queries" in celery_app.tasks
    # send_task and beat honour task_routes: the exact name wins over "knowledge.*" -> ingest.
    assert celery_app.amqp.router.route({}, "knowledge.purge_queries")["queue"].name == (
        "maintenance"
    )
    assert celery_app.amqp.router.route({}, "knowledge.ingest_version")["queue"].name == "ingest"
    entry = celery_app.conf.beat_schedule["knowledge-purge-queries"]
    assert entry["task"] == "knowledge.purge_queries"
