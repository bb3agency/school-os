"""Celery tasks for data quality (FR-DQ-002, NFR-PERF-005). Queue ``dq``; IDs only in arguments.

Outbox consumers (``ops.dispatch_outbox`` calls them with ``tenant_id``, ``event_id`` and the
event ``payload``; routes registered in :mod:`app.dq.service`):

- ``dq.run_incremental``: ``student.values.changed``, ``import.committed``, ``import.reverted``,
  ``extraction.confirmed``, ``change_request.approved`` -> re-check the students the write touched.
- ``dq.execute_run``: ``dq.run.requested`` -> a queued manual run (big scopes).
- ``dq.link_change_request`` / ``dq.unlink_change_request``: ``change_request.submitted`` /
  ``change_request.rejected``.

Register this module in ``sos_worker.celery_app.TASK_MODULES``; ``dq.*`` routes to queue ``dq``.
"""

from __future__ import annotations

import uuid
from typing import Any

from celery import shared_task

from app.dq import service
from app.ops.service import TenantTask


def _uuid(value: object) -> uuid.UUID:
    return uuid.UUID(str(value))


@shared_task(
    name=service.INCREMENTAL_TASK, base=TenantTask, queue="dq", acks_late=True, ignore_result=True
)
def run_incremental(tenant_id: str, event_id: str, payload: dict[str, Any]) -> str | None:
    run = service.run_for_event(_uuid(tenant_id), payload)
    return None if run is None else str(run.id)


@shared_task(
    name=service.EXECUTE_TASK, base=TenantTask, queue="dq", acks_late=True, ignore_result=True
)
def execute_run(tenant_id: str, event_id: str, payload: dict[str, Any]) -> str:
    return service.execute_queued_run(_uuid(tenant_id), _uuid(payload["run_id"]))


@shared_task(
    name=service.LINK_TASK, base=TenantTask, queue="dq", acks_late=True, ignore_result=True
)
def link_change_request(tenant_id: str, event_id: str, payload: dict[str, Any]) -> int:
    return service.link_change_request(_uuid(tenant_id), payload)


@shared_task(
    name=service.UNLINK_TASK, base=TenantTask, queue="dq", acks_late=True, ignore_result=True
)
def unlink_change_request(tenant_id: str, event_id: str, payload: dict[str, Any]) -> int:
    return service.unlink_change_request(_uuid(tenant_id), payload)
