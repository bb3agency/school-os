"""Wiring from ``documents`` to the ingestion jobs, through the transactional outbox.

``documents`` has no outbox event for "version is clean"; it offers extension hooks instead
(``documents.service.READY_HOOKS``, called in the worker's transaction right after a version
turns ``ready``, and ``ACL_CHANGED_HOOKS``, called in the ``set_acl`` transaction). These hooks
only enqueue an event in that same transaction, so a job is queued if and only if the change
commits (docs/04 §6, FR-OPS-004):

- ``kb.version.ready`` -> ``knowledge.ingest_version`` (queue ``ingest``)
- ``kb.document.acl_changed`` -> ``knowledge.refresh_acl`` (queue ``ingest``)

Nothing is enqueued while ``SOS_KB_ENABLED`` is off. Importing this module installs the hooks
and routes (idempotent); the worker imports it through ``app.knowledge.tasks`` and the API must
import it too (``set_acl`` runs there). Payloads are IDs only.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Final

from app.core.config import get_settings
from app.documents import service as documents
from app.ops import service as ops

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

READY_EVENT: Final = "kb.version.ready"
ACL_EVENT: Final = "kb.document.acl_changed"
INGEST_TASK: Final = "knowledge.ingest_version"
ACL_TASK: Final = "knowledge.refresh_acl"
REMOVE_TASK: Final = "knowledge.remove_document"
"""No producer yet: ``document.deleted`` already has a consumer (see docs/06 §4.8 as built)."""


def on_version_ready(session: Session, document_id: uuid.UUID, version_id: uuid.UUID) -> None:
    if get_settings().kb_enabled:
        ops.enqueue_event(
            session, READY_EVENT, {"document_id": document_id, "version_id": version_id}
        )


def on_acl_changed(session: Session, document_id: uuid.UUID) -> None:
    if get_settings().kb_enabled:
        ops.enqueue_event(session, ACL_EVENT, {"document_id": document_id})


def install() -> None:
    ops.register_outbox_route(READY_EVENT, INGEST_TASK)
    ops.register_outbox_route(ACL_EVENT, ACL_TASK)
    if on_version_ready not in documents.READY_HOOKS:
        documents.READY_HOOKS.append(on_version_ready)
    if on_acl_changed not in documents.ACL_CHANGED_HOOKS:
        documents.ACL_CHANGED_HOOKS.append(on_acl_changed)


install()

__all__ = [
    "ACL_EVENT",
    "ACL_TASK",
    "INGEST_TASK",
    "READY_EVENT",
    "REMOVE_TASK",
    "install",
    "on_acl_changed",
    "on_version_ready",
]
