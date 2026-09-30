"""Celery application (docs/04 §6). Imports task modules from ``app.*``.

Queues are separate so a big OCR batch cannot delay exports. Tasks are idempotent, carry IDs
only, acknowledge late and have time limits.
"""

from __future__ import annotations

from collections.abc import Mapping
from contextvars import Token
from typing import Any

from celery import Celery, Task, signals
from celery.schedules import crontab
from kombu import Queue

from app.admin.tasks import beat_schedule as admin_beat_schedule
from app.breakglass.tasks import beat_schedule as breakglass_beat_schedule
from app.changes.tasks import beat_schedule as changes_beat_schedule
from app.circulars.tasks import beat_schedule as circulars_beat_schedule
from app.core.config import get_settings
from app.core.logging import bind_task_context, clear_context, reset_context, setup_logging
from app.core.telemetry import setup_telemetry
from app.documents.tasks import beat_schedule as documents_beat_schedule
from app.exports.tasks import beat_schedule as exports_beat_schedule
from app.imports.tasks import beat_schedule as imports_beat_schedule
from app.insights.tasks import beat_schedule as insights_beat_schedule
from app.knowledge.tasks import beat_schedule as knowledge_beat_schedule
from app.notifications.tasks import beat_schedule as notifications_beat_schedule
from app.ops.tasks import beat_schedule as ops_beat_schedule
from app.platform.tasks import beat_schedule as platform_beat_schedule
from app.tally.tasks import beat_schedule as tally_beat_schedule

# Importing identity.service registers the system-role cloning hook in
# tenancy.POST_PROVISION_HOOKS so provisioning behaves the same in workers as in the API.
import app.identity.service  # isort: skip

# Offboarding (FR-PLT-005, ADR-0029): every module that owns school data registers its purge with
# app.tenancy at import; the purge refuses to run unless all of them are registered.
import app.academics.service  # isort: skip
import app.admin.service  # isort: skip
import app.breakglass.service  # isort: skip
import app.changes.service  # isort: skip
import app.circulars.service  # isort: skip
import app.documents.service  # isort: skip
import app.dq.service  # isort: skip
import app.exports.service  # isort: skip
import app.extraction.service  # isort: skip
import app.imports.service  # isort: skip
import app.insights.service  # isort: skip
import app.knowledge.service  # isort: skip
import app.notifications.service  # isort: skip
import app.ops.service  # isort: skip
import app.students.service  # isort: skip
import app.tally.service  # noqa: F401  isort: skip

QUEUES: tuple[str, ...] = ("ingest", "embed", "ocr", "dq", "exports", "pdf", "maintenance")

# Task modules registered as they are built (each module owns its tasks.py).
TASK_MODULES: list[str] = [
    "sos_worker.tasks",
    "app.audit.tasks",
    "app.ops.tasks",
    "app.notifications.tasks",
    "app.breakglass.tasks",
    "app.platform.tasks",
    "app.documents.tasks",
    "app.imports.tasks",
    "app.dq.tasks",
    "app.changes.tasks",
    "app.extraction.tasks",
    "app.exports.tasks",
    "app.knowledge.tasks",
    "app.students.tasks",
    "app.admin.tasks",
    "app.certificates.tasks",
    "app.circulars.tasks",
    "app.insights.tasks",
    "app.tally.tasks",
]


def create_celery() -> Celery:
    settings = get_settings()
    broker = settings.redis_url.get_secret_value()
    app = Celery("schoolos", broker=broker, backend=broker, include=TASK_MODULES)
    app.conf.update(
        task_queues=[Queue(name) for name in QUEUES],
        task_default_queue="maintenance",
        task_acks_late=True,
        task_reject_on_worker_lost=True,
        worker_prefetch_multiplier=1,
        task_time_limit=15 * 60,
        task_soft_time_limit=14 * 60,
        task_serializer="json",
        result_serializer="json",
        accept_content=["json"],
        result_expires=24 * 3600,
        timezone="UTC",
        enable_utc=True,
        broker_connection_retry_on_startup=True,
        task_routes={
            "maintenance.*": {"queue": "maintenance"},
            # FR-DOC-002: AV scans run on the ingest queue (send_task honours routes only).
            "documents.scan": {"queue": "ingest"},
            # PRV-016: deleting the files of discarded versions (outbox consumer + daily sweep).
            "documents.discard_object": {"queue": "maintenance"},
            "documents.sweep_discarded_objects": {"queue": "maintenance"},
            # FR-IMP-001..004: spreadsheet parsing, checking and commit (outbox consumers).
            "imports.parse": {"queue": "ingest"},
            "imports.validate": {"queue": "ingest"},
            "imports.commit": {"queue": "ingest"},
            "imports.purge_raw_files": {"queue": "maintenance"},
            # FR-DQ-002: data-quality runs (outbox consumers and queued runs) on queue "dq".
            "dq.*": {"queue": "dq"},
            # US-402: register-photo extraction runs on the ocr queue.
            "extraction.*": {"queue": "ocr"},
            # FR-EXP-002..004: spreadsheets on "exports"; anything with a PDF on "pdf"
            # (Chromium workers); the daily file purge on "maintenance".
            "exports.generate": {"queue": "exports"},
            "exports.render": {"queue": "pdf"},
            # docs/16 §5.8: invoice PDFs render on the same Chromium workers (ADR-0025).
            "billing.render_invoice_pdfs": {"queue": "pdf"},
            # FR-PLT-005 (ADR-0029): the deletion job on "maintenance", certificates on "pdf".
            "offboarding.process": {"queue": "maintenance"},
            "offboarding.certify": {"queue": "pdf"},
            # FR-CERT-010: certificate PDFs render on the Chromium workers.
            "certificates.render": {"queue": "pdf"},
            "exports.purge_expired": {"queue": "maintenance"},
            # FR-ADM-001: the school's full data export on "exports"; the hourly purge of
            # archives past their 24 hours on "maintenance".
            "admin.tenant_export": {"queue": "exports"},
            "admin.purge_tenant_exports": {"queue": "maintenance"},
            # docs/06 §4: document ingestion (extract, redact, chunk, embed, index), ACL
            # refresh and chunk removal (outbox consumers of the kb.* events). The daily
            # query-log purge (docs/05 §13) is maintenance, not ingestion.
            "knowledge.purge_queries": {"queue": "maintenance"},
            # ADR-0034: the rolling summary of an Ask conversation, after the answer.
            "knowledge.summarise_conversation": {"queue": "ingest"},
            "knowledge.tidy_conversations": {"queue": "maintenance"},
            "knowledge.*": {"queue": "ingest"},
            # M4 (FR-CIR-002): circular reading through the knowledge gateway, next to the
            # ingestion that triggers it; notice PDFs/PNGs on the Chromium workers
            # (FR-NOTICE-006); the daily task reminders on "maintenance" (FR-TASK-007).
            "circulars.read_version": {"queue": "ingest"},
            # FR-NOTICE-003: parent notices are drafted in the background, next to the reading.
            "circulars.draft_notice": {"queue": "ingest"},
            "circulars.render_notice": {"queue": "pdf"},
            "circulars.send_task_reminders": {"queue": "maintenance"},
            # M5 (FR-EW-005..006, FR-EW-017): the early-warning rules after attendance and
            # marks writes (outbox consumer) and daily, overdue reminders and the retention
            # purge; database work only (no files, no AI).
            "insights.evaluate_students": {"queue": "maintenance"},
            "insights.evaluate_all": {"queue": "maintenance"},
            "insights.send_flag_reminders": {"queue": "maintenance"},
            "insights.purge_expired": {"queue": "maintenance"},
            # M6 (FR-TALLY-009): silent Tally agents and sync-record retention.
            "tally.check_silent_agents": {"queue": "maintenance"},
        },
        beat_schedule={
            # FR-AUD-004: 02:00 IST signed archive, then chain verification (SEC-007).
            "audit-archive-daily": {
                "task": "audit.archive_daily",
                "schedule": crontab(minute=30, hour=20),
            },
            "audit-verify-daily": {
                "task": "audit.verify_all_chains",
                "schedule": crontab(minute=45, hour=20),
            },
            # FR-OPS-004: outbox relay and idempotency-key purge (both modes).
            **ops_beat_schedule(),
            # FR-NOT-001: purge read notifications after 90 days (both modes).
            **notifications_beat_schedule(),
            # FR-OPS-004: break-glass pull, expiry and outcome reporting (every minute).
            **breakglass_beat_schedule(),
            # FR-DOC-007: purge expired upload intents and staging objects.
            **documents_beat_schedule(),
            # FR-IMP-007: raw import files deleted 90 days after commit.
            **imports_beat_schedule(),
            # FR-CR-004: pending change requests expire after 30 days (daily).
            **changes_beat_schedule(),
            # docs/05 §13: export files deleted 7 days after they were ready (daily).
            **exports_beat_schedule(),
            # FR-ADM-001: full export archives deleted 24 hours after they were ready (hourly).
            **admin_beat_schedule(),
            # FR-TASK-007: task due-soon and overdue reminders (daily, 07:10 IST).
            **circulars_beat_schedule(),
            # docs/05 §13: Ask-the-school questions and answers deleted after 180 days (daily).
            **knowledge_beat_schedule(),
            # FR-EW-005, FR-EW-006, FR-EW-017: early-warning rules (17:40 IST), overdue flag
            # reminders (07:20 IST) and the retention purge of notes and closed flags.
            **insights_beat_schedule(),
            # FR-TALLY-009: silent Tally agents notified; old sync records deleted (30 min).
            **tally_beat_schedule(),
            # FR-PLT-*: control-plane jobs on shared; heartbeat client on dedicated (ADR-0017).
            **platform_beat_schedule(settings),
        },
    )
    return app


celery_app = create_celery()

# --- Logging and tracing (SEC-008, NFR-OBS-001) ------------------------------------------------

WORKER_SERVICE = "worker"
_task_log_tokens: dict[str, Mapping[str, Token[Any]]] = {}


@signals.setup_logging.connect
def _configure_logging(**_: object) -> None:
    """Use the SchoolOS JSON pipeline. Connecting this signal stops Celery hijacking logging."""
    setup_logging(get_settings(), service=WORKER_SERVICE)


@signals.worker_process_init.connect
def _init_tracing_in_child(**_: object) -> None:
    """Prefork children: create the tracer provider after fork (exporter threads, sockets)."""
    setup_telemetry(None, get_settings(), service=WORKER_SERVICE)


@signals.worker_init.connect
def _init_tracing_in_main(sender: object = None, **_: object) -> None:
    """Solo/threads pools run tasks in the main process, which gets no worker_process_init."""
    if "prefork" not in str(getattr(sender, "pool_cls", "prefork")).lower():
        setup_telemetry(None, get_settings(), service=WORKER_SERVICE)


@signals.task_prerun.connect
def _bind_task_context(
    task_id: str | None = None,
    task: Task[Any, Any] | None = None,
    kwargs: Mapping[str, object] | None = None,
    **_: object,
) -> None:
    """Bind task_name, queue, attempt and (from kwargs, if UUIDs) job_id/tenant_id to logs."""
    if task_id is None or task is None:
        return
    delivery = getattr(task.request, "delivery_info", None)
    queue = delivery.get("routing_key") if isinstance(delivery, dict) else None
    _task_log_tokens[task_id] = bind_task_context(
        task_name=task.name,
        kwargs=kwargs,
        queue=queue if isinstance(queue, str) else None,
        attempt=int(getattr(task.request, "retries", 0) or 0),
    )


@signals.task_postrun.connect
def _clear_task_context(task_id: str | None = None, **_: object) -> None:
    tokens = _task_log_tokens.pop(task_id, None) if task_id else None
    if tokens is None:
        return
    try:
        reset_context(tokens)
    except ValueError:  # token created in another context (should not happen); fail safe
        clear_context()
