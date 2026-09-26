"""Worker-level maintenance tasks."""

from __future__ import annotations

from sos_worker.celery_app import celery_app


@celery_app.task(name="maintenance.ping")
def ping() -> str:
    """Liveness probe for the worker pipeline."""
    return "pong"
