"""Knowledge and "Ask the school" (M2; docs/06, ADR-0005, ADR-0006, ADR-0008).

Other modules use only :mod:`app.knowledge.service` (CLAUDE.md §4; import-linter contract
``knowledge-internals``). Layout (import-linter contract ``knowledge-layers``, top to bottom):

- ``service``     public interface; later also the composition root that wires providers
- ``ingestion``   worker pipeline: extract, clean, redact, metadata, chunk, embed, index
- ``gateway``     the ONLY place that talks to model providers (LLM and embeddings SDKs)
- ``tools``       read-only record tools the answer model may call
- ``retrieval``   hybrid search with the ACL predicate applied in SQL before ranking
- ``embeddings``  provider interface, batching and the per-tenant cache
- ``chunking``    pure structure-aware chunking
- ``prompts``     versioned prompt files and their loader
- ``config``      versioned model, embeddings, retrieval, chunking and tool settings
- ``interfaces``  cross-package Protocols; ``domain`` and ``sources`` pure value types
"""
