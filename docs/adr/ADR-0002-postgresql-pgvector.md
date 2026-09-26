# ADR-0002: PostgreSQL with pgvector as the single store of record

| Field | Value |
|---|---|
| Status | Accepted (recorded retroactively 2026-09-26) |
| Date | 2026-09-26 |
| Deciders | Founder |
| Amends / supersedes | none |

## Context

SchoolOS needs relational data with strong constraints (per-source student values, maker-checker, audit), full-text and fuzzy search over English and Telugu names, and vector search for "Ask the school". Every read path, including retrieval for the AI, must be filtered by tenant and user scope **before** ranking (CLAUDE.md §6 invariant 8). Each extra data store adds a security boundary, a backup, and a place where tenant filters can be forgotten.

## Decision

- Use **PostgreSQL 16+** as the one system of record, with extensions `pgvector`, `pg_trgm`, `citext` and `pgcrypto` (03-TRD §2).
- Store document chunks and their embeddings in `kb.document_chunks` with an HNSW index (`halfvec`, cosine), a `tsvector` for full-text search and trigram indexes (05 §6).
- Retrieval is **one SQL query per branch** (vector, full-text, trigram) with tenant (RLS) and ACL filters inside each branch, fused by Reciprocal Rank Fusion (06 §6).
- Use pgvector's iterative index scans for filtered queries (pgvector ≥ 0.8) and partition large tables by tenant at Stage 2 (05 §11–12).
- A dedicated vector engine MAY be added later only behind the same retrieval interface and only through a new ADR (04 §11).

## Consequences

- Good: one backup, one encryption setup, one set of RLS policies; filter-before-rank happens in the same query; records and their search data stay transactionally consistent.
- Good: managed service available in India (RDS in ap-south-1).
- Bad: vector search at very large scale needs care (index tuning, partitioning); Stage 2 triggers are defined in 04 §11.
- Bad: Postgres expertise is needed for tuning HNSW, autovacuum and partitioning.

## Alternatives considered

| Option | Why not chosen |
|---|---|
| Separate vector database | Extra boundary where tenant/ACL filters must be duplicated; extra backups and cost; not needed at Stage 0–1 |
| Search engine (OpenSearch/Elasticsearch) for text + vectors | Heavy to run; second copy of personal data; filter-before-rank harder to prove |
| Document database | Weak constraints for official records; no RLS |

## Related requirements

FR-KB-001, FR-KB-002, FR-STU-010, FR-STU-011, NFR-PERF-001, NFR-SCAL-002; 04 §8, §11; 05 §6, §11, §12; 06 §6.
