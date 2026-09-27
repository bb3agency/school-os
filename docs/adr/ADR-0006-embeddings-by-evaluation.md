# ADR-0006: Embeddings provider chosen by evaluation

| Field | Value |
|---|---|
| Status | Accepted (recorded retroactively 2026-09-26) |
| Date | 2026-09-26 |
| Deciders | Founder |
| Amends / supersedes | none |

## Context

Document search needs vector embeddings that work for English, Telugu script and code-mixed Telugu–English. Anthropic does not offer an embeddings model; its documentation points to Voyage AI while recommending that teams evaluate several providers (checked September 2026). Embedding quality, dimension and precision drive both answer quality and storage cost. Data residency matters: a self-hosted model in ap-south-1 would keep chunk text in India.

## Decision

- Embeddings are produced through a **provider interface** in `app/knowledge/embeddings/`: `EmbeddingsProvider.embed(texts, input_type: "document" | "query") -> list[vector]`, with batching, retries, rate-limit handling and a per-tenant cache keyed by `sha256(text)` (never shared across tenants).
- The provider, model, dimension and precision are **chosen by evaluation**, not by preference: Recall@10 on the Telugu/English/mixed golden set, storage cost, latency and residency (06 §4.6, §13).
- Default candidate: a Voyage multilingual model. Alternative: an open-source multilingual model self-hosted in ap-south-1.
- Prefer 512-dimension `halfvec` if its recall loss is under 2 points compared with 1024 dimensions.
- Every chunk stores `embedding_model`. Changing models uses a **re-embedding migration**: add column and index → backfill in background → dual-read evaluation → switch → drop old.

## Consequences

- Good: the choice can change as models improve without touching retrieval code.
- Good: decisions are backed by numbers from our own synthetic dataset.
- Bad: an evaluation harness and golden set must exist before M2 exit (12 §6).
- Bad: re-embedding costs time and money; planned as a background job.
- Note: until the evaluation runs, the schema placeholder is `halfvec(1024)` (05 §6).

## Alternatives considered

| Option | Why not chosen |
|---|---|
| Pick a provider up front | No evidence it handles Telugu and code-mixed queries well enough |
| Full-text search only | Poor recall for paraphrased and cross-language questions |
| Separate embeddings per tenant model | No benefit; higher cost and complexity |

## Related requirements

FR-KB-001, FR-KB-002, NFR-PERF-003, NFR-PRV-001; 05 §6, §11; 06 §4.6, §6, §13; 08 §1.

## Amendments (2026-09-27)

Implementation facts of M2 package K2; the decision above is unchanged.

- The interface is as decided: `EmbeddingsProvider` / `TenantEmbedder` in `app/knowledge/interfaces.py`. `CachingTenantEmbedder` (`app/knowledge/embeddings/embedder.py`) does batching (`embeddings.yaml` `batching`), the one retry loop (`retry`: transient failures only), the per-tenant document cache keyed `(tenant_id, model, sha256(text))` behind the `EmbeddingCache` Protocol (`embeddings/cache.py`; the `kb.embedding_cache` repository implements it) and the in-process query cache (`query_cache_ttl_s` = 600 s, `query_cache_max_entries`). It refuses a provider whose dimensions differ from `storage.dimensions` and any returned or cached vector of another length.
- Provider selection (`embeddings/selection.py`): `SOS_KB_PROVIDER_MODE=fake` always uses the offline `FakeEmbeddingsProvider` (hashed word and character-trigram features; model `sos-fake-ngram-1024-v1`, stored as `embedding_model` so fake vectors are never mistaken for real ones). `live` uses the `selected` candidate only; with nothing selected, no model, or no implementation for the candidate's provider it refuses to start (`EmbeddingsNotConfiguredError`).
- The Voyage provider is `app/knowledge/gateway/embeddings_voyage.py`: plain `httpx` against `embeddings.yaml` `voyage.endpoint` (no SDK added), `SOS_EMBEDDINGS_API_KEY`, configured timeouts, `truncation: false`, `output_dimension` = the candidate's dimensions. The candidate's model ID stays `null` until the evaluation below picks one. There is no self-hosted implementation yet.

## Evaluation plan (2026-09-27; proposed, for the product owner)

Nothing is selected until this runs and the owner approves the result. It runs only on the synthetic `evals/` corpus (invariant 11), with an organization API key (invariant 10), never on school data.

**Candidates.** Checked against Voyage's API reference on 2026-09-27; re-check model names and limits on the day of the run.

| Candidate | Where text goes | Dims tried | Notes |
|---|---|---|---|
| `voyage-4` | Voyage (outside India; sub-processor, docs/08 §1) | 1024, 512 | Mid tier; 320k tokens per request |
| `voyage-4-lite` | Voyage | 1024, 512 | Cheapest Voyage tier; 1M tokens per request |
| `voyage-4-large` | Voyage | 1024, 512 | Highest quality tier; 120k tokens per request; cost ceiling check |
| `BAAI/bge-m3` (MIT) | Self-hosted in ap-south-1 | 1024 | Multilingual incl. Telugu, 8k-token context; needs a serving component (new ADR if chosen) |
| `intfloat/multilingual-e5-large` (MIT) | Self-hosted in ap-south-1 | 1024 | 512-token context is below our 600-token chunks plus header and 1200-token tables: truncation risk, kept as a reference point |
| Offline fake | none | 1024 | Floor: a real model must beat it clearly, especially on Telugu and mixed questions |

**How the harness compares them.** The harness (`evals/`, `sos_evals`) already computes Recall@10 and MRR@10 through a `RetrievalAdapter`. The comparison adds one adapter per candidate that: embeds every `corpus.jsonl` item (`input_type=document`, contextual header included as in production) and each question (`input_type=query`); filters candidates by the asker's visibility with the harness's own ACL oracle before ranking (as SQL does in production); ranks by cosine similarity (vector-only), and separately through the full hybrid retriever once it exists (vector + FTS + trigram with RRF, docs/06 §6). It reports per locale (`en`, `te`, `mixed`) and per category: Recall@10, MRR@10, p95 embedding latency per batch, tokens and list-price cost per 1,000 chunks, and storage bytes per chunk (`halfvec`: 2 bytes × dims). Every run records the model ID, dimensions, dataset commit and date in `evals/reports/`.

**Proposed decision rule.**
1. Must pass the retrieval gates (Recall@10 ≥ 0.90, MRR@10 ≥ 0.70) overall, and Telugu and mixed each within 5 points of English (vector-only and hybrid).
2. Among those, prefer residency in India (self-hosted) if its hybrid Recall@10 is within 2 points of the best Voyage model; otherwise the cheapest passing Voyage tier.
3. Prefer 512 dimensions if the loss against 1024 is under 2 points (the rule above); that means a re-embedding migration to `halfvec(512)`.
4. The corpus must first grow towards the ~300-document target (docs/06 §13.1; today 21 documents), otherwise differences between candidates are noise.

**Before selecting a Voyage model:** sub-processor register update and advance notice to schools, DPIA refresh (docs/08), and Voyage's no-training / zero-retention setting confirmed for the organization. Before selecting a self-hosted model: a new ADR for the serving component (instance type, scaling, cost).
