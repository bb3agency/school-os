"""Celery tasks for documents (FR-DOC-002, FR-DOC-007, FR-DOC-008). IDs only in arguments.

- ``documents.scan`` (queue ``ingest``): consumer of the outbox event
  ``document.version.registered``; AV scan -> ``ready`` | ``quarantined``. Retries with backoff
  while the scanner is unavailable; after the last attempt the version is ``failed``.
- ``documents.purge_objects`` (queue ``maintenance``): consumer of ``document.deleted``;
  removes the stored objects of a deleted document.
- ``documents.purge_expired_uploads`` (beat, daily): unregistered uploads past expiry.
- ``documents.discard_object`` (queue ``maintenance``): consumer of
  ``document.version.discarded`` (PRV-016); deletes the object of a discarded version, tagged
  for the short lifecycle rule. Retries with backoff while the store fails.
- ``documents.sweep_discarded_objects`` (beat, daily): the same for versions discarded in the
  last 7 days, in case the outbox task gave up.

Register this module in ``sos_worker.celery_app.TASK_MODULES`` and merge
:func:`beat_schedule` into the beat configuration.
"""

from __future__ import annotations

import uuid
from typing import Any

from celery import Task, shared_task
from celery.schedules import crontab

from app.core.db import context_free_session
from app.core.logging import get_logger
from app.documents import service
from app.documents.scanning import ScannerUnavailable
from app.documents.storage import ObjectStoreError
from app.tenancy import service as tenancy

log = get_logger(__name__)

SCAN_MAX_RETRIES = 6
DISCARD_MAX_RETRIES = 8


def _uuid(value: object) -> uuid.UUID:
    return uuid.UUID(str(value))


@shared_task(
    name=service.SCAN_TASK,
    bind=True,
    queue="ingest",
    acks_late=True,
    max_retries=SCAN_MAX_RETRIES,
    ignore_result=True,
)
def scan(self: Task[Any, Any], tenant_id: str, event_id: str, payload: dict[str, Any]) -> str:
    document_id = _uuid(payload["document_id"])
    version_id = _uuid(payload["version_id"])
    tid = _uuid(tenant_id)
    try:
        return service.scan_version(tid, document_id, version_id)
    except ScannerUnavailable as exc:
        if self.request.retries >= SCAN_MAX_RETRIES:
            service.mark_scan_failed(tid, document_id, version_id, "scan_unavailable")
            log.error("documents.scan.failed", resource_type="document", resource_id=document_id)
            return "failed"
        raise self.retry(exc=exc, countdown=min(30 * 2**self.request.retries, 900)) from exc


@shared_task(name=service.PURGE_TASK, queue="maintenance", acks_late=True, ignore_result=True)
def purge_objects(tenant_id: str, event_id: str, payload: dict[str, Any]) -> int:
    batch_ids = [_uuid(b) for b in payload.get("batch_ids", [])]
    return service.purge_document_objects(
        _uuid(tenant_id), _uuid(payload["document_id"]), batch_ids
    )


@shared_task(
    name=service.DISCARD_TASK,
    bind=True,
    queue="maintenance",
    acks_late=True,
    max_retries=DISCARD_MAX_RETRIES,
    ignore_result=True,
)
def discard_object(
    self: Task[Any, Any], tenant_id: str, event_id: str, payload: dict[str, Any]
) -> bool:
    document_id = _uuid(payload["document_id"])
    try:
        return service.discard_object(
            _uuid(tenant_id),
            document_id,
            _uuid(payload["version_id"]),
            str(payload["object_key"]),
        )
    except ObjectStoreError as exc:
        if self.request.retries >= DISCARD_MAX_RETRIES:
            # The daily sweep tries again; alert on this event.
            log.error(
                "documents.discard.failed",
                error_code="storage_unavailable",
                resource_type="document",
                resource_id=document_id,
            )
            return False
        raise self.retry(exc=exc, countdown=min(30 * 2**self.request.retries, 3600)) from exc


@shared_task(name="documents.sweep_discarded_objects", queue="maintenance", acks_late=True)
def sweep_discarded_objects() -> dict[str, int]:
    with context_free_session() as s:
        tenant_ids = tenancy.list_tenant_ids(s, None)
    total = 0
    for tenant_id in tenant_ids:
        total += service.sweep_discarded_objects(tenant_id)
    return {"discarded": total}


@shared_task(name="documents.purge_expired_uploads", queue="maintenance", acks_late=True)
def purge_expired_uploads() -> dict[str, int]:
    with context_free_session() as s:
        tenant_ids = tenancy.list_tenant_ids(s, None)
    total = 0
    for tenant_id in tenant_ids:
        total += service.purge_expired_uploads(tenant_id)
    return {"purged": total}


def beat_schedule() -> dict[str, dict[str, Any]]:
    return {
        "documents-purge-expired-uploads": {
            "task": "documents.purge_expired_uploads",
            "schedule": crontab(minute=20, hour=21),  # 02:50 IST
        },
        "documents-sweep-discarded-objects": {
            "task": "documents.sweep_discarded_objects",
            "schedule": crontab(minute=35, hour=21),  # 03:05 IST
        },
    }
