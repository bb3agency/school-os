"""Worker configuration (docs/04 §6, docs/13 §4)."""

from __future__ import annotations

from sos_worker.celery_app import QUEUES, celery_app
from sos_worker.tasks import ping


def test_queues_match_architecture() -> None:
    names = {q.name for q in celery_app.conf.task_queues}
    assert names == {"ingest", "embed", "ocr", "dq", "exports", "pdf", "maintenance"}
    assert set(QUEUES) == names


def test_tasks_are_safe_to_retry_by_default() -> None:
    conf = celery_app.conf
    assert conf.task_acks_late is True
    assert conf.task_reject_on_worker_lost is True
    assert conf.accept_content == ["json"]
    assert conf.task_time_limit is not None


def test_ping_runs() -> None:
    assert ping.apply().get() == "pong"


def test_FR_DOC_002_documents_tasks_registered_and_routed_to_ingest() -> None:
    celery_app.loader.import_default_modules()
    assert "documents.scan" in celery_app.tasks
    # send_task (used by the outbox dispatcher) honours task_routes, not the task's own queue.
    route = celery_app.amqp.router.route({}, "documents.scan")
    assert route["queue"].name == "ingest"
    assert "documents-purge-expired-uploads" in celery_app.conf.beat_schedule
