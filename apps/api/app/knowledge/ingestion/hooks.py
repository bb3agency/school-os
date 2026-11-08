"""Wiring from ``documents`` to the ingestion jobs, through the transactional outbox.

``documents`` has no outbox event for "version is clean"; it offers extension hooks instead
(``documents.service.READY_HOOKS``, called in the worker's transaction right after a version
turns ``ready``, ``ACL_CHANGED_HOOKS``, called in the ``set_acl`` transaction, and
``METADATA_CHANGED_HOOKS``, called in the metadata PATCH's transaction). These hooks
only enqueue an event in that same transaction, so a job is queued if and only if the change
commits (docs/04 §6, FR-OPS-004):

- ``kb.version.ready`` -> ``knowledge.ingest_version`` (queue ``ingest``)
- ``kb.document.acl_changed`` -> ``knowledge.refresh_acl`` (queue ``ingest``), for an ACL change
  and for a metadata change (the refresh rewrites the ACL copies and the facets)

An ACL change ALSO rewrites the chunks' ACL copies at once, in the ``set_acl`` transaction
(:func:`apply_acl_now`, audit 2026-11-09 YK-01): otherwise a member removed from the ACL keeps
retrieving the passages (and having them sent to the model) until the worker runs, or for good
when its retries run out (invariant 8). This runs whether or not ``SOS_KB_ENABLED`` is on (it
only rewrites chunks that exist); the queued refresh still rewrites the facets.

Nothing is enqueued while ``SOS_KB_ENABLED`` is off. Importing this module installs the hooks
and routes (idempotent); the worker imports it through ``app.knowledge.tasks`` and the API must
import it too (``set_acl`` runs there). Payloads are IDs only.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Final

from app.core.config import get_settings
from app.documents import service as documents
from app.knowledge import repository as repo
from app.knowledge.ingestion.documents_source import DocumentsServiceSource
from app.knowledge.ingestion.ports import ChunkAcl
from app.knowledge.store import SqlChunkStore
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


def _enqueue_refresh(session: Session, document_id: uuid.UUID) -> None:
    if get_settings().kb_enabled:
        ops.enqueue_event(session, ACL_EVENT, {"document_id": document_id})


def apply_acl_now(session: Session, document_id: uuid.UUID) -> int:
    """Rewrite the ACL copies on the document's chunks from its CURRENT ACL, in the caller's
    transaction, under the per-document index lock (an ingestion writing chunks meanwhile
    re-reads the facts under the same lock). Returns the rows rewritten."""
    store = SqlChunkStore()
    store.lock_document(session, document_id)
    facts = DocumentsServiceSource().document(session, repo.current_tenant_id(session), document_id)
    if facts is None:
        return store.delete_document(session, document_id)
    return store.update_acl(session, document_id, ChunkAcl.from_entries(facts.acl))


def on_acl_changed(session: Session, document_id: uuid.UUID) -> None:
    apply_acl_now(session, document_id)
    _enqueue_refresh(session, document_id)


def on_metadata_changed(session: Session, document_id: uuid.UUID, fields: frozenset[str]) -> None:
    """A PATCH changed the document's metadata: the same refresh rewrites the facets the chunks
    copy (``doc_type``, ``issued_on``; docs/05 §6), so search filters use the new values."""
    _enqueue_refresh(session, document_id)


def install() -> None:
    ops.register_outbox_route(READY_EVENT, INGEST_TASK)
    ops.register_outbox_route(ACL_EVENT, ACL_TASK)
    if on_version_ready not in documents.READY_HOOKS:
        documents.READY_HOOKS.append(on_version_ready)
    if on_acl_changed not in documents.ACL_CHANGED_HOOKS:
        documents.ACL_CHANGED_HOOKS.append(on_acl_changed)
    if on_metadata_changed not in documents.METADATA_CHANGED_HOOKS:
        documents.METADATA_CHANGED_HOOKS.append(on_metadata_changed)


install()

__all__ = [
    "ACL_EVENT",
    "ACL_TASK",
    "INGEST_TASK",
    "READY_EVENT",
    "REMOVE_TASK",
    "apply_acl_now",
    "install",
    "on_acl_changed",
    "on_metadata_changed",
    "on_version_ready",
]
