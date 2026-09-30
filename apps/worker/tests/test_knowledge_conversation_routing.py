"""Ask conversation jobs (ADR-0033; docs/06 §5): the rolling summary runs on an explicit route
("ingest", after the answer, never on a question's path) and is fed by its outbox event; the
daily memory retention and conversation adoption run on "maintenance"."""

from __future__ import annotations

from sos_worker.celery_app import celery_app


def test_FR_KB_012_summary_and_tidy_jobs_registered_routed_and_scheduled() -> None:
    from app.ops import service as ops

    celery_app.loader.import_default_modules()
    for name in ("knowledge.summarise_conversation", "knowledge.tidy_conversations"):
        assert name in celery_app.tasks
    route = celery_app.amqp.router.route
    assert route({}, "knowledge.summarise_conversation")["queue"].name == "ingest"
    assert route({}, "knowledge.tidy_conversations")["queue"].name == "maintenance"
    entry = celery_app.conf.beat_schedule["knowledge-tidy-conversations"]
    assert entry["task"] == "knowledge.tidy_conversations"
    assert (
        ops.OUTBOX_ROUTES["kb.conversation.summary_requested"] == "knowledge.summarise_conversation"
    )
