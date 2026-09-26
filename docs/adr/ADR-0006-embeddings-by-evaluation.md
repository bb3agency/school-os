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
