"""What the ingestion pipeline needs from the outside, as Protocols (dependency inversion).

- :class:`ChunkStore`: writes ``kb.document_chunks`` (docs/05 §6). Implemented by the retrieval
  package over SQL (it owns the table and its indexes) and, for tests and local wiring, by
  :class:`app.knowledge.ingestion.memory.InMemoryChunkStore`.
- :class:`DocumentSource`: document facts, ACL and file bytes. Implemented over
  ``documents.service`` only (:mod:`app.knowledge.ingestion.documents_source`), never its
  repository or models (CLAUDE.md §4).

Every method receives the caller's ``tenant_session`` (RLS context set, invariant 1); none opens
its own transaction. Values carry IDs, codes and already-redacted text only (invariant 4).
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING, Final, Protocol, runtime_checkable

from app.knowledge.domain import Chunk

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

PRINCIPAL_TYPES: Final = ("role", "section", "class", "membership")


# --- chunk store ------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ChunkAcl:
    """Copies of ``kb.document_acl`` on every chunk (``acl_roles`` ... ``acl_memberships``).

    Empty everywhere = the document ACL is empty: visible only to school-wide readers (docs/05
    §6.1, fail closed), never public. Sorted and de-duplicated, so equal ACLs compare equal.
    """

    roles: tuple[str, ...] = ()
    sections: tuple[uuid.UUID, ...] = ()
    classes: tuple[uuid.UUID, ...] = ()
    memberships: tuple[uuid.UUID, ...] = ()

    @classmethod
    def from_entries(cls, entries: Iterable[tuple[str, str]]) -> ChunkAcl:
        """From ``(principal_type, principal_ref)`` pairs; an unknown type raises (never
        silently widens or narrows visibility)."""
        roles: set[str] = set()
        refs: dict[str, set[uuid.UUID]] = {"section": set(), "class": set(), "membership": set()}
        for principal_type, ref in entries:
            if principal_type == "role":
                roles.add(ref)
            elif principal_type in refs:
                refs[principal_type].add(uuid.UUID(ref))
            else:
                raise ValueError(f"unknown ACL principal type {principal_type!r}")
        return cls(
            roles=tuple(sorted(roles)),
            sections=tuple(sorted(refs["section"])),
            classes=tuple(sorted(refs["class"])),
            memberships=tuple(sorted(refs["membership"])),
        )


@dataclass(frozen=True, slots=True)
class ChunkFilters:
    """Denormalised document columns every chunk row carries (one indexed retrieval query)."""

    doc_type: str
    title: str
    issued_on: date | None
    academic_year_id: uuid.UUID | None
    sensitivity: str


@dataclass(frozen=True, slots=True)
class IndexedChunk:
    chunk: Chunk
    """Content, contextual header (for ``content_tsv`` = ``to_tsvector('simple', header ||
    content)``), heading path, pages, token count, language, table flag."""
    embedding: tuple[float, ...]
    """Embedding of :func:`app.knowledge.ingestion.pipeline.embedding_text` of the chunk."""


@dataclass(frozen=True, slots=True)
class VersionIndex:
    """Everything needed to (re)write the chunks of one document version."""

    document_id: uuid.UUID
    version_id: uuid.UUID
    version_no: int
    embedding_model: str
    filters: ChunkFilters
    acl: ChunkAcl
    is_latest: bool
    chunks: tuple[IndexedChunk, ...]


@runtime_checkable
class ChunkStore(Protocol):
    """``kb.document_chunks`` writes for the ingestion pipeline (implemented by retrieval)."""

    def lock_document(self, session: Session, document_id: uuid.UUID) -> None:
        """Serialise index writes for one document until the transaction ends (e.g.
        ``pg_advisory_xact_lock``), so an ACL refresh cannot interleave with a version write
        and leave stale ACL copies."""
        ...

    def replace_version(self, session: Session, index: VersionIndex) -> int:
        """Delete every chunk of ``index.version_id`` and insert ``index.chunks`` (with the
        filters, ACL copies, ``embedding_model`` and ``is_latest``). Idempotent: running it twice
        leaves one copy. Returns the number of chunks written."""
        ...

    def set_latest(self, session: Session, document_id: uuid.UUID, version_id: uuid.UUID) -> int:
        """``is_latest = (version_id = :version_id)`` for every chunk of the document. Returns
        the number of chunks that changed."""
        ...

    def hide_document(self, session: Session, document_id: uuid.UUID) -> int:
        """``is_latest = false`` on every chunk of the document (archived: kept, not searched).
        Returns the number of chunks that changed."""
        ...

    def update_acl(self, session: Session, document_id: uuid.UUID, acl: ChunkAcl) -> int:
        """Rewrite the ACL copies on every chunk of the document (docs/05 §6)."""
        ...

    def delete_versions(
        self, session: Session, document_id: uuid.UUID, version_ids: Sequence[uuid.UUID]
    ) -> int:
        """Delete the chunks of retired versions (quarantined or discarded, PRV-016)."""
        ...

    def delete_document(self, session: Session, document_id: uuid.UUID) -> int:
        """Delete every chunk of the document (FR-DOC-007). Idempotent."""
        ...


# --- document source --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class VersionFacts:
    id: uuid.UUID
    version_no: int
    mime_type: str
    status: str


@dataclass(frozen=True, slots=True)
class DocumentFacts:
    """A document as ingestion sees it: metadata, ACL and versions (no content)."""

    id: uuid.UUID
    purpose: str
    doc_type: str
    title: str
    issuer: str | None
    issued_on: date | None
    academic_year_id: uuid.UUID | None
    sensitivity: str
    current_version_id: uuid.UUID | None
    acl: tuple[tuple[str, str], ...]
    versions: tuple[VersionFacts, ...]
    status: str = "active"
    """``archived`` documents keep their chunks but none is searchable (FR-DOC-005)."""

    def version(self, version_id: uuid.UUID) -> VersionFacts | None:
        return next((v for v in self.versions if v.id == version_id), None)


class DocumentNotReady(Exception):
    """The version is not ``ready`` (not scanned, quarantined, discarded) or its bytes changed."""


@runtime_checkable
class DocumentSource(Protocol):
    def document(
        self, session: Session, tenant_id: uuid.UUID, document_id: uuid.UUID
    ) -> DocumentFacts | None:
        """The document of the current school (``tenant_id`` is the session's tenant), or None
        when it does not exist (deleted)."""
        ...

    def read_version(self, session: Session, document_id: uuid.UUID, version_no: int) -> bytes:
        """The bytes of a ``ready`` (malware-scanned) version, SHA-256 checked. Raises
        :class:`DocumentNotReady` otherwise."""
        ...


__all__ = [
    "PRINCIPAL_TYPES",
    "ChunkAcl",
    "ChunkFilters",
    "ChunkStore",
    "DocumentFacts",
    "DocumentNotReady",
    "DocumentSource",
    "IndexedChunk",
    "VersionFacts",
    "VersionIndex",
]
