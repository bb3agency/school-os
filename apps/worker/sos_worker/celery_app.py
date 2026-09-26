"""Celery application (docs/04 §6). Imports task modules from ``app.*``.

Queues are separate so a big OCR batch cannot delay exports. Tasks are idempotent, carry IDs
only, acknowledge late and have time limits.
"""

from __future__ import annotations

from app.core.config import get_settings
from celery import Celery
from celery.schedules import crontab
from kombu import Queue

QUEUES: tuple[str, ...] = ("ingest", "embed", "ocr", "dq", "exports", "pdf", "maintenance")

# Task modules registered as they are built (each module owns its tasks.py).
TASK_MODULES: list[str] = ["sos_worker.tasks", "app.audit.tasks"]


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
        task_routes={"maintenance.*": {"queue": "maintenance"}},
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
        },
    )
    return app


celery_app = create_celery()
