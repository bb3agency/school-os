"""Celery tasks for the students module: DEK re-encryption (SEC-012; docs/05 §9, 07 §8).

- ``maintenance.reencrypt_tenant`` consumes ``keys.rotated`` (queued by
  :func:`app.students.rotation.rotate`) and ``keys.reencrypt_requested`` (its own continuation).
  One school per run, in that school's ``tenant_session``; at most :data:`MAX_BATCHES` batches
  per run so a task stays well inside the worker time limit, then it queues its continuation.
  Idempotent: a redelivered or duplicate event only re-checks what is left.

IDs, key versions and counts only in logs and results.
"""

from __future__ import annotations

import uuid
from typing import Any, Final

from celery import shared_task

from app.core.logging import get_logger
from app.ops import service as ops
from app.students import rotation

log = get_logger(__name__)

MAX_BATCHES: Final = 100

ops.register_outbox_route(rotation.ROTATED_EVENT, rotation.REENCRYPT_TASK)
ops.register_outbox_route(rotation.CONTINUE_EVENT, rotation.REENCRYPT_TASK)


@shared_task(name=rotation.REENCRYPT_TASK, queue="maintenance", acks_late=True, ignore_result=True)
def reencrypt_tenant(tenant_id: str, event_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    run = rotation.run_reencryption(
        uuid.UUID(tenant_id), max_batches=MAX_BATCHES, continue_later=True
    )
    return {
        "key_version": run.key_version,
        "batches": run.batches,
        "total": run.total,
        "remaining": run.remaining,
        "done": run.done,
    }
