"""Keep the index in step with document lifecycle changes, in the change's own transaction.

``documents`` offers extension points that run inside its transactions (documents.service):

- ``STATUS_CHANGED_HOOKS`` (archive / unarchive, FR-DOC-005): archiving hides every chunk of the
  document from retrieval (``demote_document``; the chunks are kept for version history), and
  unarchiving makes the current version searchable again (``promote_version``) when it has been
  indexed. Ingestion never promotes a version of an archived document (``DocumentFacts.status``).
- Deletion (FR-DOC-007, docs/06 §4.8): deleting ``kb.documents`` / ``kb.document_versions``
  rows cascades to their chunks in the same statement (0021_kb_tables ``ON DELETE CASCADE``), so
  no job is needed. Verified answers citing the document must be flagged ``needs_review`` in
  that transaction too; ``documents`` has no "deleted" hook, so :func:`flag_citing_answers` is
  registered in ``DELETE_GUARDS`` (called with the session right before the delete, in the same
  transaction): it flags and never refuses (returns ``None``). If a later guard refuses the
  delete, the whole transaction, flags included, rolls back.

These run whether or not ``SOS_KB_ENABLED`` is on: hiding and flagging only ever narrow what
can be retrieved (fail closed). Importing this module installs them (idempotent); the API and
worker import it through the composition root.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from app.core.logging import get_logger
from app.documents import service as documents
from app.knowledge import repository as repo
from app.knowledge.ingestion.documents_source import DocumentsServiceSource
from app.knowledge.store import SqlChunkStore

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

log = get_logger(__name__)
_store = SqlChunkStore()


def on_status_changed(session: Session, document_id: uuid.UUID, status: str) -> None:
    _store.lock_document(session, document_id)
    tenant_id = repo.current_tenant_id(session)
    if status == "archived":
        count = repo.demote_document(session, document_id)
    else:
        count = 0
        facts = DocumentsServiceSource().document(session, tenant_id, document_id)
        current = facts.current_version_id if facts is not None else None
        if current is not None and repo.has_version_chunks(session, current):
            count = repo.promote_version(session, document_id=document_id, version_id=current)
    log.info(
        "knowledge.document.status_applied",
        tenant_id=tenant_id,
        resource_type="document",
        resource_id=document_id,
        action=status,
        count=count,
    )


def flag_citing_answers(session: Session, document_id: uuid.UUID) -> str | None:
    """A ``DELETE_GUARDS`` entry that never refuses: flags citing verified answers."""
    flagged = repo.flag_verified_answers_citing(session, document_id)
    if flagged:
        log.info(
            "knowledge.verified_answers.flagged",
            resource_type="document",
            resource_id=document_id,
            count=flagged,
        )
    return None


def install() -> None:
    if on_status_changed not in documents.STATUS_CHANGED_HOOKS:
        documents.STATUS_CHANGED_HOOKS.append(on_status_changed)
    if flag_citing_answers not in documents.DELETE_GUARDS:
        documents.DELETE_GUARDS.append(flag_citing_answers)


install()

__all__ = ["flag_citing_answers", "install", "on_status_changed"]
