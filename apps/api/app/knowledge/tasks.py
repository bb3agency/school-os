"""Celery tasks for knowledge ingestion (docs/06 §4, docs/04 §6). IDs only in arguments.

- ``knowledge.ingest_version`` (queue ``ingest``): consumer of ``kb.version.ready``; extract,
  redact, chunk, embed and index one ``ready`` version. Extraction, chunking and embedding run in
  one task: the chunk text must not travel through the broker (docs/04 §6 "IDs only"), and the
  per-tenant embeddings cache makes a retry cheap. A later split onto the ``embed`` queue needs a
  staging table first.
- ``knowledge.refresh_acl`` (queue ``ingest``): consumer of ``kb.document.acl_changed``.
- ``knowledge.remove_document`` (queue ``ingest``): delete a document's chunks; no producer yet
  (see :mod:`app.knowledge.ingestion.hooks`).

Every task is idempotent and retries with exponential backoff (max 5, docs/04 §6); after the
last attempt it logs ``error_code`` and gives up (the next event for the document repairs it).
Register this module in ``sos_worker.celery_app.TASK_MODULES``. Importing it wires the pipeline
from the composition root (:func:`app.knowledge.composition.configure_ingestion`; the pipeline
itself is built on the first task).
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Any, Final

from celery import Task, shared_task

from app.core.logging import get_logger
from app.knowledge import composition
from app.knowledge.ingestion import hooks, runtime

log = get_logger(__name__)

MAX_RETRIES: Final = 5

composition.configure_ingestion()


def _uuid(value: object) -> uuid.UUID:
    return uuid.UUID(str(value))


def _run(task: Task[Any, Any], name: str, document_id: uuid.UUID, work: Callable[[], str]) -> str:
    try:
        return work()
    except runtime.PipelineNotConfigured:
        log.error("knowledge.task.not_configured", error_code="not_configured", action=name)
        raise
    except Exception as exc:
        if task.request.retries >= MAX_RETRIES:
            log.error(
                "knowledge.task.gave_up",
                error_code="retries_exhausted",
                error_type=type(exc).__name__,
                action=name,
                resource_type="document",
                resource_id=document_id,
            )
            return "failed"
        raise task.retry(exc=exc, countdown=min(30 * 2**task.request.retries, 900)) from exc


@shared_task(
    name=hooks.INGEST_TASK,
    bind=True,
    queue="ingest",
    acks_late=True,
    max_retries=MAX_RETRIES,
    ignore_result=True,
)
def ingest_version(
    self: Task[Any, Any], tenant_id: str, event_id: str, payload: dict[str, Any]
) -> str:
    tid, document_id = _uuid(tenant_id), _uuid(payload["document_id"])
    version_id = _uuid(payload["version_id"])
    return _run(
        self,
        hooks.INGEST_TASK,
        document_id,
        lambda: runtime.pipeline().ingest(tid, document_id, version_id),
    )


@shared_task(
    name=hooks.ACL_TASK,
    bind=True,
    queue="ingest",
    acks_late=True,
    max_retries=MAX_RETRIES,
    ignore_result=True,
)
def refresh_acl(
    self: Task[Any, Any], tenant_id: str, event_id: str, payload: dict[str, Any]
) -> str:
    tid, document_id = _uuid(tenant_id), _uuid(payload["document_id"])

    def work() -> str:
        runtime.pipeline().refresh_acl(tid, document_id)
        return "done"

    return _run(self, hooks.ACL_TASK, document_id, work)


@shared_task(
    name=hooks.REMOVE_TASK,
    bind=True,
    queue="ingest",
    acks_late=True,
    max_retries=MAX_RETRIES,
    ignore_result=True,
)
def remove_document(
    self: Task[Any, Any], tenant_id: str, event_id: str, payload: dict[str, Any]
) -> str:
    tid, document_id = _uuid(tenant_id), _uuid(payload["document_id"])

    def work() -> str:
        runtime.pipeline().remove_document(tid, document_id)
        return "done"

    return _run(self, hooks.REMOVE_TASK, document_id, work)
