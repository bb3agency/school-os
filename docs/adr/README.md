# Architecture Decision Records (ADRs)

An ADR records one significant decision: what we chose, why, and what it costs us. ADRs let a future reader (or a coding assistant) understand why the system looks the way it does before changing it.

## When to write an ADR

Write one before you:

- change anything listed in CLAUDE.md §3 (tech stack) or §11 (never do this);
- add a sub-processor, a new data class, a new deployment shape, or AI write capability (14-roadmap §5);
- add a new cross-tenant access path, database role, or `SECURITY DEFINER` function;
- add a runtime dependency with a licence other than a permissive one;
- make any change that would be expensive to undo later.

If you are unsure, write a short ADR with status **Proposed** and ask for review.

## Process

1. Copy the template below into `docs/adr/ADR-XXXX-short-title.md`. Use the next free number; numbers are never reused.
2. Fill it in with plain language. Link requirement IDs (`FR-…`, `NFR-…`, `SEC-…`, `PRV-…`) and the docs affected.
3. Open a PR with the ADR and the doc changes it implies. Status stays **Proposed** until the product owner approves.
4. On approval set **Accepted** and the date. Add the ADR to the index below.
5. **Accepted ADRs are not rewritten.** When a later decision changes one, write a new ADR. The only edit allowed on an accepted ADR is to its status line:
   - `Amended by ADR-00yy` when the new ADR changes part of the decision and the rest still holds;
   - `Superseded by ADR-00yy` when the new ADR replaces it completely.
6. A rejected proposal keeps its file with status **Rejected** and a one-line reason, so the idea is not re-proposed without new facts.

### Status values

| Status | Meaning |
|---|---|
| Proposed | Under discussion; do not build on it yet |
| Accepted | In force |
| Accepted (recorded retroactively 2026-09-26) | The decision was already stated in the v0.1 docs; the ADR writes it down in one place |
| Amended by ADR-00yy | Still in force, but read the amending ADR for the parts it changes |
| Superseded by ADR-00yy | No longer in force |
| Rejected | Considered and declined |

## Template (MADR-style)

```markdown
# ADR-XXXX: <decision in a few words>

| Field | Value |
|---|---|
| Status | Proposed |
| Date | YYYY-MM-DD |
| Deciders | <names or roles> |
| Amends / supersedes | <ADR IDs or "none"> |

## Context
What problem are we solving? What forces are at play (users, law, budget, team size, existing decisions)?
State facts with the date they were checked if they may change.

## Decision
What we will do, stated so that someone can check whether code follows it.
Use MUST / SHOULD / MAY (RFC 2119) where it helps.

## Consequences
- Good: …
- Bad / costs: …
- Follow-up work: …

## Alternatives considered
| Option | Why not chosen |
|---|---|
| … | … |

## Related requirements
FR-…, NFR-…, SEC-…, PRV-…; docs sections affected.
```

## Index

| ADR | Title | Status |
|---|---|---|
| [ADR-0001](ADR-0001-modular-monolith.md) | Modular monolith with async workers | Accepted (recorded retroactively 2026-09-26) |
| [ADR-0002](ADR-0002-postgresql-pgvector.md) | PostgreSQL with pgvector as the single store of record | Accepted (recorded retroactively 2026-09-26) |
| [ADR-0003](ADR-0003-pool-tenancy-rls.md) | Pool multi-tenancy with row-level security | Accepted (recorded retroactively 2026-09-26) · Amended by ADR-0013, ADR-0015 |
| [ADR-0004](ADR-0004-technology-stack.md) | Technology stack | Accepted (recorded retroactively 2026-09-26) · Amended by ADR-0014 |
| [ADR-0005](ADR-0005-llm-gateway-and-provider.md) | LLM gateway and provider (Anthropic Claude) | Accepted (recorded retroactively 2026-09-26) |
| [ADR-0006](ADR-0006-embeddings-by-evaluation.md) | Embeddings provider chosen by evaluation | Accepted (recorded retroactively 2026-09-26) |
| [ADR-0007](ADR-0007-no-aadhaar-storage.md) | Never store Aadhaar numbers | Accepted (recorded retroactively 2026-09-26) |
| [ADR-0008](ADR-0008-tools-not-text-to-sql.md) | Read-only typed tools instead of text-to-SQL | Accepted (recorded retroactively 2026-09-26) |
| [ADR-0009](ADR-0009-aws-india-hosting.md) | Host on AWS in India | Accepted (recorded retroactively 2026-09-26) · Amended by ADR-0015 |
| [ADR-0010](ADR-0010-maker-checker.md) | Maker-checker for identity changes | Accepted (recorded retroactively 2026-09-26) |
| [ADR-0011](ADR-0011-hash-chained-audit.md) | Hash-chained, append-only audit log | Accepted (recorded retroactively 2026-09-26) · Amended by ADR-0013 |
| [ADR-0012](ADR-0012-managed-oidc-identity.md) | Managed OIDC identity provider | Accepted (recorded retroactively 2026-09-26) · Amended by ADR-0013, ADR-0018 |
| [ADR-0013](ADR-0013-cross-tenant-access-and-platform-privilege-separation.md) | Cross-tenant access paths, platform privilege separation and platform identity | Accepted |
| [ADR-0014](ADR-0014-local-ci-service-images.md) | Local and CI service images: SeaweedFS and Valkey | Accepted |
| [ADR-0015](ADR-0015-deployment-and-commercial-model.md) | Deployment and commercial model: managed SaaS, shared and dedicated tiers | Accepted |
| [ADR-0016](ADR-0016-payments-provider.md) | Payments provider | Proposed |
| [ADR-0017](ADR-0017-platform-admin-panel-architecture.md) | Platform admin panel (control plane) architecture | Accepted |
| [ADR-0018](ADR-0018-mfa-and-step-up-with-cognito.md) | MFA enforcement and step-up with Amazon Cognito | Accepted |
