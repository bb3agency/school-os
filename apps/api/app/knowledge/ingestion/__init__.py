"""Ingestion pipeline (worker): document version -> searchable chunks (docs/06 §4).

Responsibility: implement :class:`app.knowledge.interfaces.IngestionPipeline` as idempotent
Celery stages keyed by ``(document_version_id, stage)``: extraction (text layer, OCR, DOCX,
XLSX), cleaning (NFC, hyphenation, headers/footers, Telugu OCR fixes), mandatory Aadhaar
redaction with ``core.redaction`` before storage, indexing, logging or model calls
(invariant 4), language ID, metadata extraction through the gateway (strict JSON; low
confidence -> empty, never guessed), chunking, embedding and indexing (``is_latest`` flip, ACL
copies, ``kb.document.ready``), plus deletion and ACL-change propagation. Reads files and
versions only through ``documents.service``.

Boundary: may use gateway, embeddings, chunking, prompts and config; must not import tools,
retrieval or service (import-linter ``knowledge-layers``). Never auto-corrects records
(invariant 6): register scans go to the extraction queue for human confirmation.

As built (K5), one module per stage:

- ``ports``: the :class:`ChunkStore` (``kb.document_chunks`` writes, implemented by retrieval)
  and :class:`DocumentSource` Protocols and their value types; ``memory``: an in-memory store.
- ``documents_source``: :class:`DocumentSource` over ``documents.service`` (system read context).
- ``extract``: DOCX (zipfile + defusedxml) and plain text; ``clean``: NFC, control characters,
  hyphenation, whitespace, Aadhaar masking, language by script.
- ``pipeline``: :class:`DocumentIngestionPipeline` (``ingest_version``, ``refresh_acl``,
  ``remove_document``).
- ``hooks``: documents hooks -> outbox events -> ``knowledge.*`` tasks (``app.knowledge.tasks``);
  ``runtime``: the pipeline factory the composition root sets.

Not yet: PDF text layer (ADR-0027, proposed), OCR, XLSX, metadata extraction, the per-stage
version statuses of FR-DOC-008 and the ``kb.document.ready`` notification.
"""
