"""Hybrid retrieval behind ``search_documents`` (docs/06 §6; FR-KB-001, FR-KB-002, SEC-018).

Responsibility: implement :class:`app.knowledge.interfaces.Retriever`: vector (HNSW), full-text
(``simple``) and trigram candidate lists over ``kb.document_chunks``, each filtered IN SQL by
tenant (RLS), ``is_latest``, the caller's ACL keys and optional filters BEFORE ranking, then
Reciprocal Rank Fusion, recency and verified-answer boosts, diversity and adjacent-chunk merging.
The ACL predicate comes from one function (``acl_predicate``) so the three branches cannot
drift; an empty ACL is visible only to school-wide readers (docs/05 §6.1, fail closed). Settings
come from ``knowledge/config/retrieval.yaml``.

Boundary: read-only; no model calls. Must not import gateway, tools, ingestion or service
(import-linter ``knowledge-layers``). Query translation and query embedding are done by the
caller, which passes texts and vectors in :class:`app.knowledge.domain.RetrievalQuery`.
"""

from app.knowledge.retrieval.acl import acl_predicate
from app.knowledge.retrieval.hybrid import HybridRetriever

__all__ = ["HybridRetriever", "acl_predicate"]
