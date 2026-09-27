""":class:`DocumentIngestionPipeline`, the :class:`app.knowledge.interfaces.IngestionPipeline`.

``ingest_version`` (docs/06 §4, docs/04 §7.3), three short transactions so no transaction stays
open while the file is parsed or the embeddings provider is called:

1. read: document facts and ACL, then the bytes of the ``ready`` (malware-scanned) version,
   through ``documents.service`` (:class:`DocumentSource`);
2. no transaction: extract (DOCX, plain text), clean + **redact Aadhaar** (invariant 4), chunk;
   then embed the chunks through the :class:`TenantEmbedder` (its per-tenant cache in its own
   transaction);
3. write, under the document's index lock: re-read the facts (a delete or ACL change that
   committed meanwhile wins), replace the version's chunks with the fresh ACL copies and
   filters, mark the version ``is_latest`` if it is the document's current version (every
   other version's chunks then stop being latest), and drop the chunks of retired
   (quarantined/discarded) versions.

Idempotent: a rerun rewrites the same chunks (the chunker is deterministic). A version that is
not ``ready``, gone, of an unsupported type or unreadable is not indexed; if it is the current
version, older versions still stop being "latest" so retrieval never presents superseded text as
current. Documents whose purpose or sensitivity is excluded (``chunking.yaml`` ``extraction``)
are never indexed and any chunks they have are removed.

Nothing here logs document text, titles or names: events carry IDs, counts and codes only
(invariant 5).
"""

from __future__ import annotations

import dataclasses
import uuid
from collections.abc import Callable, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from app.core.db import tenant_session
from app.core.logging import get_logger
from app.core.redaction import mask_aadhaar
from app.knowledge.chunking import StructureChunker, count_tokens
from app.knowledge.config.chunking import ChunkingConfig, load_chunking_config
from app.knowledge.domain import Chunk, DocumentContext
from app.knowledge.ingestion.clean import clean_document
from app.knowledge.ingestion.extract import ExtractionFailed, extract_pages
from app.knowledge.ingestion.ports import (
    ChunkAcl,
    ChunkFilters,
    ChunkStore,
    DocumentFacts,
    DocumentNotReady,
    DocumentSource,
    IndexedChunk,
    VersionFacts,
    VersionIndex,
)
from app.knowledge.interfaces import Chunker, TenantEmbedder

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

log = get_logger(__name__)

SessionFactory = Callable[[uuid.UUID], AbstractContextManager["Session"]]
RETIRED_STATUSES: Final = ("quarantined", "failed")

INDEXED: Final = "indexed"
MISSING: Final = "missing"
NOT_READY: Final = "not_ready"
EXCLUDED: Final = "excluded"
UNSUPPORTED: Final = "unsupported_type"


@dataclass(frozen=True, slots=True)
class _Read:
    facts: DocumentFacts
    version: VersionFacts
    data: bytes | None
    """None: a type ingestion cannot read yet (nothing is indexed for the version)."""


def embedding_text(chunk: Chunk) -> str:
    """What is embedded (and what full-text search indexes): header, blank line, content."""
    if not chunk.context_header:
        return chunk.content
    return f"{chunk.context_header}\n\n{chunk.content}"


def _session(tenant_id: uuid.UUID) -> AbstractContextManager[Session]:
    return tenant_session(tenant_id)


class DocumentIngestionPipeline:
    def __init__(
        self,
        *,
        source: DocumentSource,
        store: ChunkStore,
        embedder: TenantEmbedder,
        chunker: Chunker | None = None,
        config: ChunkingConfig | None = None,
        session_factory: SessionFactory = _session,
    ) -> None:
        self._config = config or load_chunking_config()
        self._source = source
        self._store = store
        self._embedder = embedder
        self._chunker = chunker or StructureChunker(self._config)
        self._session = session_factory

    # --- IngestionPipeline ------------------------------------------------------------------

    def ingest_version(
        self, tenant_id: uuid.UUID, document_id: uuid.UUID, version_id: uuid.UUID
    ) -> None:
        self.ingest(tenant_id, document_id, version_id)

    def refresh_acl(self, tenant_id: uuid.UUID, document_id: uuid.UUID) -> None:
        with self._session(tenant_id) as s:
            self._store.lock_document(s, document_id)
            facts = self._source.document(s, tenant_id, document_id)
            if facts is None or not self._eligible(facts):
                # Deleted (the chunks went with it) or no longer indexable: fail closed.
                count = self._store.delete_document(s, document_id)
                outcome = MISSING if facts is None else EXCLUDED
            else:
                count = self._store.update_acl(s, document_id, ChunkAcl.from_entries(facts.acl))
                outcome = "updated"
        log.info(
            "knowledge.acl.refreshed",
            tenant_id=tenant_id,
            resource_type="document",
            resource_id=document_id,
            count=count,
            outcome=outcome,
        )

    def remove_document(self, tenant_id: uuid.UUID, document_id: uuid.UUID) -> None:
        with self._session(tenant_id) as s:
            self._store.lock_document(s, document_id)
            removed = self._store.delete_document(s, document_id)
        log.info(
            "knowledge.document.removed",
            tenant_id=tenant_id,
            resource_type="document",
            resource_id=document_id,
            count=removed,
        )

    # --- ingestion ----------------------------------------------------------------------------

    def ingest(self, tenant_id: uuid.UUID, document_id: uuid.UUID, version_id: uuid.UUID) -> str:
        """Run :meth:`ingest_version` and return its outcome code (for the task result)."""
        read = self._read(tenant_id, document_id, version_id)
        if not isinstance(read, _Read):
            return self._done(tenant_id, document_id, *read)
        outcome, chunks = UNSUPPORTED, []
        if read.data is not None:
            try:
                chunks = self._chunks(read, read.data, tenant_id)
                outcome = INDEXED
            except ExtractionFailed as exc:
                outcome = exc.code
        vectors = self._embed(tenant_id, chunks)
        written = self._write(tenant_id, document_id, version_id, chunks, vectors)
        if not isinstance(written, int):
            return self._done(tenant_id, document_id, *written)
        return self._done(tenant_id, document_id, outcome, written)

    def _read(
        self, tenant_id: uuid.UUID, document_id: uuid.UUID, version_id: uuid.UUID
    ) -> _Read | tuple[str, int]:
        """Transaction 1: facts, policy and the bytes of a ready version."""
        with self._session(tenant_id) as s:
            facts = self._source.document(s, tenant_id, document_id)
            version = facts.version(version_id) if facts is not None else None
            if facts is None or version is None:
                return MISSING, 0
            if version.status != "ready":
                return NOT_READY, 0
            if not self._eligible(facts):
                self._store.lock_document(s, document_id)
                return EXCLUDED, self._store.delete_document(s, document_id)
            data = None
            if self._supported(version):
                try:
                    data = self._source.read_version(s, document_id, version.version_no)
                except DocumentNotReady:
                    return NOT_READY, 0
            return _Read(facts=facts, version=version, data=data)

    def _write(
        self,
        tenant_id: uuid.UUID,
        document_id: uuid.UUID,
        version_id: uuid.UUID,
        chunks: Sequence[Chunk],
        vectors: Sequence[Sequence[float]],
    ) -> int | tuple[str, int]:
        """Transaction 3, under the document's index lock, with facts read again."""
        with self._session(tenant_id) as s:
            self._store.lock_document(s, document_id)
            fresh = self._source.document(s, tenant_id, document_id)
            current = fresh.version(version_id) if fresh is not None else None
            if fresh is None or current is None or current.status != "ready":
                return MISSING, 0
            if not self._eligible(fresh):
                return EXCLUDED, self._store.delete_document(s, document_id)
            archived = fresh.status == "archived"
            is_latest = fresh.current_version_id == version_id and not archived
            written = self._store.replace_version(
                s,
                VersionIndex(
                    document_id=document_id,
                    version_id=version_id,
                    version_no=current.version_no,
                    embedding_model=self._embedder.model,
                    filters=_filters(fresh),
                    acl=ChunkAcl.from_entries(fresh.acl),
                    is_latest=is_latest,
                    chunks=tuple(
                        IndexedChunk(chunk=c, embedding=tuple(v))
                        for c, v in zip(chunks, vectors, strict=True)
                    ),
                ),
            )
            if is_latest:
                self._store.set_latest(s, document_id, version_id)
            elif archived:
                self._store.hide_document(s, document_id)
            retired = [v.id for v in fresh.versions if v.status in RETIRED_STATUSES]
            if retired:
                self._store.delete_versions(s, document_id, retired)
            return written

    def _chunks(self, read: _Read, data: bytes, tenant_id: uuid.UUID) -> list[Chunk]:
        facts, version = read.facts, read.version
        pages = extract_pages(data, version.mime_type, self._config.extraction)
        cleaned = clean_document(
            pages,
            tenant_id=tenant_id,
            document_id=facts.id,
            version_id=version.id,
            version_no=version.version_no,
            dominant_share=self._config.language_dominant_share,
        )
        if cleaned.redactions:
            log.info(
                "knowledge.ingest.aadhaar_masked",
                tenant_id=tenant_id,
                resource_type="document",
                resource_id=facts.id,
                count=cleaned.redactions,
            )
        chunks = self._chunker.chunk(cleaned.document, _context(facts))
        return [self._recheck(c) for c in chunks]

    def _recheck(self, chunk: Chunk) -> Chunk:
        """Defence in depth: nothing unmasked reaches the embedder or the index (invariant 4)."""
        content = mask_aadhaar(chunk.content)
        header = mask_aadhaar(chunk.context_header)
        path = tuple(mask_aadhaar(h) for h in chunk.heading_path)
        if (content, header, path) == (chunk.content, chunk.context_header, chunk.heading_path):
            return chunk
        return dataclasses.replace(
            chunk,
            content=content,
            context_header=header,
            heading_path=path,
            token_count=count_tokens(content, self._config.token_estimate),
        )

    def _embed(self, tenant_id: uuid.UUID, chunks: Sequence[Chunk]) -> list[list[float]]:
        if not chunks:
            return []
        texts = [embedding_text(c) for c in chunks]
        with self._session(tenant_id) as s:
            vectors = self._embedder.embed(s, tenant_id, texts, "document")
        if len(vectors) != len(texts):
            raise RuntimeError("the embedder returned a different number of vectors")
        return vectors

    # --- policy -------------------------------------------------------------------------------

    def _eligible(self, facts: DocumentFacts) -> bool:
        rules = self._config.extraction
        return (
            facts.purpose not in rules.excluded_purposes
            and facts.sensitivity in rules.indexed_sensitivities
        )

    def _supported(self, version: VersionFacts) -> bool:
        mime = version.mime_type.split(";", 1)[0].strip().lower()
        return mime in self._config.extraction.supported_mime_types

    @staticmethod
    def _done(tenant_id: uuid.UUID, document_id: uuid.UUID, outcome: str, count: int = 0) -> str:
        fields: dict[str, object] = {
            "tenant_id": tenant_id,
            "resource_type": "document",
            "resource_id": document_id,
            "outcome": outcome,
            "count": count,
        }
        if outcome == INDEXED:
            log.info("knowledge.ingest.done", **fields)
        elif outcome in (MISSING, NOT_READY, EXCLUDED, UNSUPPORTED):
            log.info("knowledge.ingest.skipped", **fields)
        else:
            log.warning("knowledge.ingest.failed", error_code=outcome, **fields)
        return outcome


def _context(facts: DocumentFacts) -> DocumentContext:
    """Header metadata the office entered (masked like every other text; never guessed)."""
    return DocumentContext(
        doc_type=facts.doc_type,
        title=mask_aadhaar(facts.title),
        issuer=mask_aadhaar(facts.issuer) if facts.issuer else None,
        issued_on=facts.issued_on,
    )


def _filters(facts: DocumentFacts) -> ChunkFilters:
    return ChunkFilters(
        doc_type=facts.doc_type,
        title=mask_aadhaar(facts.title),
        issued_on=facts.issued_on,
        academic_year_id=facts.academic_year_id,
        sensitivity=facts.sensitivity,
    )


__all__ = [
    "EXCLUDED",
    "INDEXED",
    "MISSING",
    "NOT_READY",
    "UNSUPPORTED",
    "DocumentIngestionPipeline",
    "SessionFactory",
    "embedding_text",
]
