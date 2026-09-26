# ADR-0003: Pool multi-tenancy with row-level security

| Field | Value |
|---|---|
| Status | Accepted (recorded retroactively 2026-09-26) · Amended by [ADR-0013](ADR-0013-cross-tenant-access-and-platform-privilege-separation.md) (cross-tenant access paths, composite tenant keys) and [ADR-0015](ADR-0015-deployment-and-commercial-model.md) (dedicated tier) |
| Date | 2026-09-26 |
| Deciders | Founder |
| Amends / supersedes | none |

## Context

SchoolOS serves many schools. Each school's data, especially children's data, must never be visible to another school (BR-06, FR-TEN-001/002). At Stage 0–1 there are few schools and a small budget, so running a separate database or stack per school is expensive to operate. Application-only filtering (`WHERE tenant_id = …` in code) fails silently when one query forgets it.

## Decision

- Use the **pool model**: one shared schema; every tenant-owned table has `tenant_id uuid NOT NULL`.
- Enforce isolation in the database with PostgreSQL **row-level security**: `ENABLE` + `FORCE` on every tenant table and the standard policy `tenant_isolation` (`USING` and `WITH CHECK` on `tenant_id = core.current_tenant()`), created in the same migration as the table (05 §3, §14).
- The application database role has **no `BYPASSRLS`** and is not superuser. Tenant context is set per transaction; when it is not set, queries return no rows (fail closed).
- Isolation is layered: (1) RLS, (2) scoped repositories requiring a `UserContext`, (3) retrieval SQL filters, (4) tenant-prefixed S3 keys and per-tenant DEKs, plus a cross-tenant test suite in CI (04 §9; 07 §7).
- A catalog test fails the build if any table with `tenant_id` lacks ENABLE + FORCE and the policy (SEC-001).
- Escape hatch: a very large or regulated tenant can be moved to its own database with the same schema.

## Consequences

- Good: cheapest to run; one migration path; isolation is enforced by the database, not by remembering filters.
- Good: the same schema and policies work unchanged if a tenant is later moved to its own database or host.
- Bad: every query pays the RLS predicate cost; `tenant_id` must lead every composite index.
- Bad: paths that legitimately cross tenants (login before a tenant is chosen, outbox dispatch, provisioning) need narrow, reviewed exceptions. ADR-0013 defines them.
- Bad: noisy neighbours share resources; mitigated by per-tenant rate limits, job concurrency caps and AI budgets.

## Alternatives considered

| Option | Why not chosen |
|---|---|
| Silo: database per tenant | Higher cost and operational load at Stage 0–1; kept as an escape hatch (later offered as the dedicated tier, ADR-0015) |
| Bridge: schema per tenant | Migrations multiply per tenant; connection and catalog bloat; RLS still needed for shared tables |
| Application-level filtering only | One missed `WHERE` leaks data; not verifiable by a catalog test |

## Related requirements

FR-TEN-001, FR-TEN-002, FR-IAM-012, NFR-SCAL-003, SEC-001, SEC-002, SEC-015; 04 §9; 05 §3; 07 §7; 12 §4.4–4.5.
