# ADR-0035: Contextual chunk headers and a reranker for "Ask the school"

| Field | Value |
|---|---|
| Status | Proposed |
| Date | 2026-09-30 |
| Deciders | Product owner (approved building it behind a switch on 2026-09-30; enabling per environment pending the live evaluation); privacy review (pending, for a reranker provider) |
| Amends / supersedes | Extends [ADR-0006](ADR-0006-embeddings-by-evaluation.md) (a reranker is chosen by evaluation like an embeddings model) and [ADR-0005](ADR-0005-llm-gateway-and-provider.md) (a new model role through the gateway). Relates to ADR-0033 (the gateway's provider switch to Gemini via Vertex AI) and ADR-0034 (Ask conversations and memory). |

## Context

"Ask the school" retrieves chunks by hybrid search (vector, full text, keyword) fused with RRF (docs/06 §6). Office documents often split a subject from its details: the subject is on page 1 ("Sub: Science exhibition 2026"), the title the office typed is a reference number ("Circular No. 14/2026-27"), and later pages say "the above" or "the said event". Such a chunk cannot be found by the subject a teacher asks about. Anthropic's published "Contextual Retrieval" technique addresses this: a model writes a short context situating each chunk in its document at ingestion, embedded and indexed with the chunk; Anthropic reports 35 % / 49 % / 67 % fewer top-20 retrieval failures (contextual embeddings / plus contextual BM25 / plus reranking) on its own corpora. A reranker (a model that scores the question and each candidate together) was already an "optional, adopt only if evals show a gain" item in docs/06 §6. The product owner approved (2026-09-30) building both behind a setting, enabled only if evaluation shows a measurable improvement.

## Decision

1. **Contextual chunk headers** (docs/06 §4.11): made at ingestion by a new gateway role `contextualize` (models.yaml; provider-neutral: the small tier of whichever provider the gateway runs, per ADR-0033, with the model chosen by the §13.6 live evaluation), from the document's own already-masked text; validated server-side (no new numbers or capitalised names, script, links, length) and redacted; stored in `kb.document_chunks.chunk_context` (plain text like the chunk; C3 is never indexed) and used only for the embedding, full-text and keyword search. Never shown to users, never sent to the answer model. Budget-aware (deferred when the school's budget or AI switch says no), metered per document, reused per version, backfilled by a rate-limited task. This adds no sub-processor: the same LLM provider already receives document excerpts (docs/08 §1), and it receives no more than one document the index already holds.
2. **Reranking** (docs/06 §6 as built): an `interfaces.Reranker` provider interface (`app/knowledge/rerank/`), an offline fake, and network adapters in `knowledge/gateway` only. Only candidates that passed the caller's ACL predicate in SQL, read again under it and masked again, are sent; a failure or a slow call keeps the RRF order. Candidate adapters: **Voyage AI rerank** (built, `gateway/rerank_voyage.py`; same vendor and organization key as the ADR-0006 embeddings candidate) and **Google Vertex AI ranking API** (named, not built; relevant as the platform moves to Google per ADR-0033). Under ADR-0033 the Vertex AI ranking API served from asia-south1 is the **in-region candidate**: passages stay in India and it uses the gateway's Vertex service identity, while Voyage would add a sub-processor outside India; the switch stays `off` until the evaluation decides.
3. **Both are OFF** (`retrieval.yaml` `contextual_chunks: off`, `rerank.provider: off`; per environment `SOS_KB_CONTEXTUAL_CHUNKS`, `SOS_KB_RERANK`). An environment switches one on only after a **live** run of the docs/06 §13.6 evaluation (real contextualize model, the selected embeddings model, the candidate reranker, synthetic data only) meets the soft gates in `evals/gates.toml` (contexts +10 points recall@5; reranking no MRR loss; recall@5 ≥ 0.90 with both) with the hard gates unchanged, plus latency within docs/06 §12.
4. **A reranker provider is a new sub-processor purpose.** Before `SOS_KB_RERANK` names a live provider in any environment with school data: sub-processor register entry (docs/08 §1: Voyage AI for reranking, or Google), advance notice to schools, DPIA refresh, and the provider's zero-retention / no-training setting confirmed for the organization.

## Consequences

- Good: a documented, testable path to better recall for the most common failure (subject only on page 1) and fewer, better passages for the answer model; everything reversible by a switch.
- Good: invariants kept by construction and tests: the reranker never receives a passage the caller could not read (DB test with a forbidden best match), nothing Aadhaar-like reaches a model (masked before and again), model IDs, prompts and thresholds in versioned files.
- Bad: ingestion cost (≈ $3.5-4 one-off per 300-document school at Haiku list prices, docs/06 §4.11) out of the school's monthly AI budget; per-query rerank cost and 50-300 ms latency when on; a one-time table rewrite for the generated `context_tsv` column.
- Bad: offline evals with deterministic stand-ins show the mechanism works, not how much a real model helps (docs/06 §13.6); the decision to enable waits for a live run.

## Alternatives considered

| Option | Why not chosen |
|---|---|
| Metadata header only (§4.5, today) | Cannot add the subject when the title is a reference number |
| Contextualise at query time | Adds a model call to every question and sends more text per question |
| Put the context in the displayed chunk | Users and the answer model would read model-written text as if it were the document |
| A separate vector store or search service for reranking | CLAUDE.md §11 (no separate vector database without an ADR); PostgreSQL stays the one store |
| Self-hosted cross-encoder | Needs a serving component (like the self-hosted embeddings option of ADR-0006); can be added as another `Reranker` if it wins the evaluation |

## Related requirements

FR-KB-001, FR-KB-002, FR-KB-006, FR-KB-009, FR-KB-011, SEC-018, NFR-CST-001, NFR-PRV-001; docs/05 §6.2, docs/06 §4.11, §6, §12, §13.6, docs/08 §1, docs/10 §11.
