"""SQL adapters of the ingestion and embeddings ports over :mod:`app.knowledge.repository`.

- :class:`SqlChunkStore` implements :class:`app.knowledge.ingestion.ports.ChunkStore` on
  ``kb.document_chunks`` (docs/06 §4.7): ``replace_version`` -> ``replace_version_chunks``
  (hidden until promoted), ``set_latest`` -> ``promote_version``, ``hide_document`` ->
  ``demote_document``, ``update_acl`` -> ``refresh_acl``, ``delete_versions`` /
  ``delete_document`` -> ``delete_version_chunks`` / ``delete_document_chunks``. When a new
  version replaces the searchable one, active verified answers citing the document are flagged
  ``needs_review`` in the same transaction (FR-KB-030, docs/06 §4.8).
- :class:`SqlEmbeddingCache` implements :class:`app.knowledge.embeddings.EmbeddingCache` on
  ``kb.embedding_cache`` (document vectors only; query vectors never reach the database).

Every method runs in the caller's ``tenant_session``: RLS limits each statement to that school,
and the cache additionally refuses a ``tenant_id`` that is not the session's (fail closed).
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING

from sqlalchemy import text

from app.knowledge import repository as repo
from app.knowledge.ingestion.ports import ChunkAcl, VersionIndex

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

_LOCK_SQL = text("SELECT pg_advisory_xact_lock(:key)")


def _lock_key(document_id: uuid.UUID) -> int:
    """A signed 64-bit advisory-lock key for the document's index writes."""
    digest = hashlib.sha256(b"sos:kb:index:" + document_id.bytes).digest()
    return int.from_bytes(digest[:8], "big", signed=True)


def _acl(acl: ChunkAcl) -> repo.ChunkAcl:
    return repo.ChunkAcl(
        roles=frozenset(acl.roles),
        sections=frozenset(acl.sections),
        classes=frozenset(acl.classes),
        memberships=frozenset(acl.memberships),
    )


class SqlChunkStore:
    """:class:`~app.knowledge.ingestion.ports.ChunkStore` over ``kb.document_chunks``."""

    def lock_document(self, session: Session, document_id: uuid.UUID) -> None:
        session.execute(_LOCK_SQL, {"key": _lock_key(document_id)})

    def replace_version(self, session: Session, index: VersionIndex) -> int:
        f = index.filters
        return repo.replace_version_chunks(
            session,
            document_id=index.document_id,
            version_id=index.version_id,
            chunks=[repo.EmbeddedChunk(c.chunk, c.embedding) for c in index.chunks],
            embedding_model=index.embedding_model,
            facets=repo.DocumentFacets(
                doc_type=f.doc_type,
                sensitivity=f.sensitivity,
                issued_on=f.issued_on,
                academic_year_id=f.academic_year_id,
            ),
            acl=_acl(index.acl),
        )

    def set_latest(self, session: Session, document_id: uuid.UUID, version_id: uuid.UUID) -> int:
        previous = repo.latest_version_of(session, document_id)
        visible = repo.promote_version(session, document_id=document_id, version_id=version_id)
        if previous is not None and previous != version_id:
            # The cited text may have changed: a person re-checks (FR-KB-030), and cached
            # answers given the old version are not reused (docs/06 answer cache).
            repo.flag_verified_answers_citing(session, document_id)
            repo.invalidate_cache_citing(session, document_id)
        return visible

    def hide_document(self, session: Session, document_id: uuid.UUID) -> int:
        repo.invalidate_cache_citing(session, document_id)
        return repo.demote_document(session, document_id)

    def update_acl(self, session: Session, document_id: uuid.UUID, acl: ChunkAcl) -> int:
        # Who may read it changed: answers given its passages are not reused (answer cache).
        repo.invalidate_cache_citing(session, document_id)
        return repo.refresh_acl(session, document_id=document_id, acl=_acl(acl))

    def delete_versions(
        self, session: Session, document_id: uuid.UUID, version_ids: Sequence[uuid.UUID]
    ) -> int:
        del document_id  # version ids are unique; RLS keeps them to this school
        return sum(repo.delete_version_chunks(session, v) for v in version_ids)

    def delete_document(self, session: Session, document_id: uuid.UUID) -> int:
        return repo.delete_document_chunks(session, document_id)


class SqlEmbeddingCache:
    """:class:`~app.knowledge.embeddings.EmbeddingCache` over ``kb.embedding_cache``."""

    @staticmethod
    def _check(session: Session, tenant_id: uuid.UUID) -> None:
        if repo.current_tenant_id(session) != tenant_id:
            raise RuntimeError("embedding cache used outside the tenant's session (fail closed)")

    def get_many(
        self,
        session: Session,
        tenant_id: uuid.UUID,
        model: str,
        digests: Sequence[bytes],
    ) -> Mapping[bytes, Sequence[float]]:
        self._check(session, tenant_id)
        return repo.get_cached_embeddings(
            session, model=model, input_type="document", digests=digests
        )

    def put_many(
        self,
        session: Session,
        tenant_id: uuid.UUID,
        model: str,
        vectors: Mapping[bytes, Sequence[float]],
    ) -> None:
        self._check(session, tenant_id)
        repo.put_cached_embeddings(session, model=model, input_type="document", entries=vectors)


__all__ = ["SqlChunkStore", "SqlEmbeddingCache"]
