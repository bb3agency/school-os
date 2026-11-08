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


def test_SEC_008_no_task_result_or_error_is_kept_in_valkey() -> None:
    """Audit 2026-10-05 (hardening H-02): the result backend is the broker (Valkey). Nothing reads
    task results, yet every task without ``ignore_result`` stored its return value and, on
    failure, the exception message and traceback for 24 hours; a database error message can
    quote row values (invariant 5: no PII in error data). Results are now never stored."""
    celery_app.loader.import_default_modules()
    conf = celery_app.conf
    assert conf.task_ignore_result is True
    assert conf.task_store_errors_even_if_ignored is False
    stored = sorted(
        name
        for name, task in celery_app.tasks.items()
        if not name.startswith("celery.") and not task.ignore_result
    )
    assert stored == []


def test_YA_02_no_countdown_reaches_the_broker_visibility_timeout() -> None:
    """Audit 2026-11-09 YA-02: on Valkey a reserved message that is not acknowledged within the
    visibility timeout is delivered again. ETA/countdown messages are reserved at once and, with
    ``acks_late``, acknowledged only after they ran, so a retry countdown of an hour (documents,
    online payments) met the 1 h default and could run twice. The timeout is now pinned above
    every countdown plus a run, and the outbox dedupe lease outlives it."""
    from app.ops.service import EVENT_LEASE_SECONDS, HOLD_COUNTDOWN_SECONDS

    options = celery_app.conf.broker_transport_options or {}
    timeout = options.get("visibility_timeout")
    assert isinstance(timeout, int)
    longest_retry_countdown = 3600  # min(..., 3600) in documents and fees.tasks_online
    assert timeout >= longest_retry_countdown + celery_app.conf.task_time_limit
    assert timeout > HOLD_COUNTDOWN_SECONDS
    assert timeout < EVENT_LEASE_SECONDS


def test_YA_03_interval_beat_jobs_expire_instead_of_piling_up() -> None:
    """Audit 2026-11-09 YA-03: beat keeps publishing while workers are down; every missed run of
    an interval job (the outbox relay every 5 s, the 1-minute sweeps) then ran back to back
    ahead of real work on the same queue. A run older than its interval is now discarded."""
    from celery.schedules import schedule as interval

    for name, entry in celery_app.conf.beat_schedule.items():
        every = entry["schedule"]
        seconds = every.run_every.total_seconds() if isinstance(every, interval) else every
        if isinstance(seconds, int | float):
            expires = entry.get("options", {}).get("expires")
            assert expires is not None, name
            assert 0 < expires <= seconds, name
