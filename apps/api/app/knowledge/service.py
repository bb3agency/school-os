"""Knowledge public API ("Ask the school"; docs/06). Other modules import only this module.

M2 skeleton (K0): the contracts are fixed here; implementations land in later packages and are
wired together here (composition root). Until then nothing in the product calls into knowledge,
there are no routes (``SOS_KB_ENABLED`` defaults to false) and no model is ever called.

What other modules will use:

- :class:`KnowledgeService` (``ask`` streams the SSE events of docs/06 §5.1;
  ``search_documents`` is search-only retrieval and the fallback mode). Routes wrap it with
  ``require("kb.ask")`` (docs/09 Knowledge); every call audits in the caller's transaction
  (invariant 7) and logs no question or answer text (invariant 5; ``kb.queries`` stores them
  encrypted, FR-KB-009).
- :class:`IngestionPipeline` (worker jobs reacting to ``documents``' outbox events: version
  ready, ACL changed, document deleted).
- The value types below, re-exported so callers never import ``app.knowledge.domain``.

Rules every implementation keeps (docs/06, CLAUDE.md §6): retrieval filters by tenant and
permissions in SQL before ranking (invariant 8, FR-KB-002); the model sees only
``search_result`` blocks built from what the caller may read; citations are validated
server-side (FR-KB-005); tools are read-only (invariant 9, ADR-0008); every provider call goes
through ``knowledge.gateway`` (ADR-0005).
"""

from __future__ import annotations

from app.knowledge.domain import (
    AclKeys,
    AnswerSegment,
    AskEvent,
    AskMode,
    AskRequest,
    Citation,
    CitationEvent,
    DoneEvent,
    ErrorEvent,
    Locale,
    MetaEvent,
    RankedChunk,
    SearchFilters,
    TokenEvent,
)
from app.knowledge.interfaces import IngestionPipeline, KnowledgeService

__all__ = [
    "AclKeys",
    "AnswerSegment",
    "AskEvent",
    "AskMode",
    "AskRequest",
    "Citation",
    "CitationEvent",
    "DoneEvent",
    "ErrorEvent",
    "IngestionPipeline",
    "KnowledgeService",
    "Locale",
    "MetaEvent",
    "RankedChunk",
    "SearchFilters",
    "TokenEvent",
]
