"""Celery tasks for knowledge ingestion (docs/06 §4, docs/04 §6). IDs only in arguments.

- ``knowledge.ingest_version`` (queue ``ingest``): consumer of ``kb.version.ready``; extract,
  redact, chunk, embed and index one ``ready`` version. Extraction, chunking and embedding run in
  one task: the chunk text must not travel through the broker (docs/04 §6 "IDs only"), and the
  per-tenant embeddings cache makes a retry cheap. A later split onto the ``embed`` queue needs a
  staging table first.
- ``knowledge.refresh_acl`` (queue ``ingest``): consumer of ``kb.document.acl_changed``.
- ``knowledge.remove_document`` (queue ``ingest``): delete a document's chunks; no producer yet
  (see :mod:`app.knowledge.ingestion.hooks`).
- ``knowledge.purge_queries`` (daily, queue ``maintenance``): per school, delete the query log
  (``kb.queries``) older than its retention (180 days; docs/05 §13) with the conversations and
  summaries that rested on it, each school in its own ``tenant_session``; a failing school does
  not stop the others (logged with ids only, counted in ``failed``, retried on the next run).
  Counts only in the result and the log.
- ``knowledge.tidy_conversations`` (daily, queue ``maintenance``; ADR-0033): per school, the
  memory retention (unconfirmed suggestions after 24 hours, people who left the school) and a
  conversation for questions asked before conversations existed (``adopt_conversations``).
- ``knowledge.summarise_conversation`` (queue ``ingest``, explicit route): consumer of the outbox
  event ``kb.conversation.summary_requested`` queued with an answer; the rolling summary of a
  conversation's older turns through the gateway (docs/06 §5). Ids only in the payload.

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
from celery.schedules import crontab

from app.core.db import context_free_session, tenant_session
from app.core.logging import get_logger
from app.knowledge import composition, service
from app.knowledge.ingestion import hooks, runtime
from app.tenancy import service as tenancy

log = get_logger(__name__)

MAX_RETRIES: Final = 5
PURGE_QUERIES_TASK: Final = "knowledge.purge_queries"
SUMMARY_TASK: Final = service.SUMMARY_TASK
TIDY_CONVERSATIONS_TASK: Final = "knowledge.tidy_conversations"
# Schools whose query log is still kept (an offboarded school's rows go with the whole purge).
PURGE_TENANT_STATUSES: Final = ("active", "suspended", "offboarding")

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


def purge_queries_all() -> dict[str, int]:
    """Delete every school's query log past its retention (one ``tenant_session`` per school).
    A school that fails is rolled back, logged (ids and error type only) and counted; the others
    still run, and its rows are due again on the next run."""
    with context_free_session() as session:
        tenant_ids = tenancy.list_tenant_ids(session, PURGE_TENANT_STATUSES)
    purged = failed = 0
    for tenant_id in tenant_ids:
        try:
            with tenant_session(tenant_id) as session:
                purged += service.purge_old_queries(session)
        except Exception as exc:  # database: this school only, retried next run
            failed += 1
            log.warning(
                "knowledge.queries.purge_failed",
                tenant_id=tenant_id,
                error_type=type(exc).__name__,
            )
    log.info("knowledge.queries.purged", count=purged, failed=failed)
    return {"tenants": len(tenant_ids), "purged": purged, "failed": failed}


def tidy_conversations_all() -> dict[str, int]:
    """Per school (one ``tenant_session`` each; a failing school is logged and retried on the
    next run): the memory retention (expired suggestions, people who left the school) and a
    conversation for questions asked before conversations existed (ADR-0033)."""
    with context_free_session() as session:
        tenant_ids = tenancy.list_tenant_ids(session, PURGE_TENANT_STATUSES)
    memories = adopted = failed = 0
    for tenant_id in tenant_ids:
        try:
            with tenant_session(tenant_id) as session:
                memories += service.purge_memories(session)
                adopted += service.adopt_conversations(session)
        except Exception as exc:  # database: this school only, retried next run
            failed += 1
            log.warning(
                "knowledge.conversations.tidy_failed",
                tenant_id=tenant_id,
                error_type=type(exc).__name__,
            )
    log.info("knowledge.conversations.tidied", count=memories + adopted, failed=failed)
    return {
        "tenants": len(tenant_ids),
        "memories_deleted": memories,
        "conversations_adopted": adopted,
        "failed": failed,
    }


@shared_task(
    name=SUMMARY_TASK,
    bind=True,
    queue="ingest",
    acks_late=True,
    max_retries=MAX_RETRIES,
    ignore_result=True,
)
def summarise_conversation(
    self: Task[Any, Any], tenant_id: str, event_id: str, payload: dict[str, Any]
) -> str:
    """Rolling summary of one conversation (ADR-0033); a failed provider call is not retried
    (the next answer asks again), a database error is."""
    del event_id
    try:
        return service.summarise_conversation(_uuid(tenant_id), payload)
    except Exception as exc:
        if self.request.retries >= MAX_RETRIES:
            log.error(
                "knowledge.task.gave_up",
                error_code="retries_exhausted",
                error_type=type(exc).__name__,
                action=SUMMARY_TASK,
            )
            return "failed"
        raise self.retry(exc=exc, countdown=min(30 * 2**self.request.retries, 900)) from exc


@shared_task(name=PURGE_QUERIES_TASK, queue="maintenance", acks_late=True)
def purge_queries() -> dict[str, int]:
    return purge_queries_all()


@shared_task(name=TIDY_CONVERSATIONS_TASK, queue="maintenance", acks_late=True)
def tidy_conversations() -> dict[str, int]:
    return tidy_conversations_all()


def beat_schedule() -> dict[str, dict[str, Any]]:
    """Beat entries for knowledge (both deployment modes)."""
    return {
        "knowledge-purge-queries": {
            "task": PURGE_QUERIES_TASK,
            "schedule": crontab(minute=25, hour=21),  # 02:55 IST
        },
        "knowledge-tidy-conversations": {
            "task": TIDY_CONVERSATIONS_TASK,
            "schedule": crontab(minute=40, hour=21),  # 03:10 IST
        },
    }
