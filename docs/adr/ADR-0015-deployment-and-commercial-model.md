# ADR-0015: Deployment and commercial model: managed SaaS with shared and dedicated tiers

| Field | Value |
|---|---|
| Status | Accepted · Amended by ADR-0037 (commercial catalogue) |
| Date | 2026-09-26 |
| Deciders | Founder (product owner approval of build proposals B1–B25) |
| Amends / supersedes | Supersedes the "pool only, silo as escape hatch" part of [ADR-0003](ADR-0003-pool-tenancy-rls.md) and the "ECS-only hosting" part of [ADR-0009](ADR-0009-aws-india-hosting.md); the rest of both stays in force |

## Context

The v0.1 docs described a pooled multi-tenant SaaS and left the commercial model as hypotheses (01-BRD §11). Conversations with schools raised three recurring asks:

- Most schools want the lowest price and no IT work.
- Some larger or more cautious schools (or groups) ask for "our own server", their own web address, and a clear answer to "who else is on this machine?".
- Every school expects one supplier to run it, patch it, back it up and answer the phone.

Installing software on the school's own premises would make patching, backups, physical security and incident response impossible to guarantee, and SchoolOS would still be the Data Processor answerable for safeguards (08 §3). We need a model that is one codebase, one release process and one security baseline, with a premium option for isolation.

## Decision

### Commercial model

- SchoolOS is a **managed SaaS** run by us. Schools pay a **recurring subscription** in INR (monthly or annual; GST extra), billed by the platform admin panel (16 §9–10).
- **Shared tier** (default plan): the school is a tenant on the pooled platform.
- **Dedicated tier** (premium plan): the school gets its own isolated host, its own storage bucket and encryption key, and an **optional custom domain** (for example `office.<school>.edu.in`).
- Pilot schools may start with a trial; price and conversion trigger are still agreed before the pilot (01-BRD §11).

### Shared tier

Pooled platform in AWS ap-south-1: ECS Fargate (web, api, worker, beat), RDS PostgreSQL 16 + pgvector, ElastiCache for Valkey, S3, KMS, Cognito; backups copied to ap-south-2 (ADR-0009). Tenant isolation per ADR-0003 and ADR-0013.

### Dedicated tier

- **One isolated EC2 host per school** in ap-south-1, created by the Terraform module `dedicated_host`.
- The host runs the **same container images** through `deploy/dedicated/compose.yaml`: `caddy`, `web`, `api`, `worker`, `beat`, `postgres` (+ pgvector), `valkey`.
- A dedicated host is a **one-tenant install**: `tenant_id`, RLS, roles and every invariant stay on exactly as in the shared tier. No code path is "dedicated-only".
- `SOS_DEPLOYMENT_MODE=dedicated` **disables the control-plane routes** (`/api/v1/platform/*`) and the platform web route group on that host.
- Own S3 bucket and own KMS key per host; optional custom domain with TLS from Caddy (ACME).
- Backups: continuous WAL archiving and nightly base backups with WAL-G, plus a nightly `pg_dump`, encrypted, to S3 in **ap-south-2**.
- Hardening, patching and fleet upgrades follow 10 §15 and SEC-030.

### Control plane

- The **control plane** (platform admin panel, billing, fleet registry) runs **only in the shared deployment** (ADR-0017).
- Dedicated hosts send an **outbound heartbeat** every 5 minutes to `POST /api/v1/fleet/heartbeat` carrying version, health and aggregate usage counts, **never personal data**, signed with a per-deployment HMAC key (16 §12; SEC-028).
- **The control plane never pulls data from a school** and has no inbound access path to a dedicated host's database or files. Operational access to a host is through the deploy pipeline and SSM, under the same break-glass rules as the shared tier.

### Recovery targets

| Tier | RPO | RTO | Notes |
|---|---|---|---|
| Shared | ≤ 15 min (Stage 0–1) | ≤ 4 h | RDS PITR + snapshot copies (NFR-AVL-002) |
| Dedicated | ≤ 15 min | ≤ 8 h | WAL-G archiving; rebuild host from Terraform and restore (NFR-AVL-005) |

## Consequences

- Good: one codebase and one release train serve both tiers; the dedicated tier is the v0.1 "silo escape hatch" turned into a product.
- Good: schools that need isolation get it without on-premise risk; data stays in India in both tiers.
- Good: control plane stays small and cannot reach school data.
- Bad: each dedicated host is a server we must patch, monitor, back up and upgrade; fleet tooling (heartbeat, upgrade waves, backup checks) is required before the first dedicated school.
- Bad: a single host has no automatic failover; recovery is by rebuild and restore (RTO ≤ 8 h).
- Bad: version skew between shared and dedicated deployments must be tracked; migrations must stay backward compatible (CLAUDE.md §6 invariant 12).
- Bad: identity for dedicated hosts (callback URLs on custom domains) needs per-deployment OIDC app clients (16 §19).
- Follow-up: 01 §11, 04 §16, 10 §15, 11 §11, NFR-AVL-005, NFR-FLT-001..002, SEC-028, SEC-030, roadmap Task 11.

## Alternatives considered

| Option | Why not chosen |
|---|---|
| On-premise install at the school | Patching, backups, physical security and incident response cannot be guaranteed; still our processor liability |
| Silo database per school behind the shared app | Kept possible; does not answer the "own server / own domain" ask and adds routing complexity to the shared app |
| Separate AWS account or ECS stack per dedicated school | Stronger isolation but much higher fixed cost and operational load at our size; revisit for large groups |
| Kubernetes namespace per school | Violates CLAUDE.md §11 without clear benefit |
| Shared tier only | Loses schools that require isolation; no premium plan |

## Related requirements

FR-PLT-002, FR-PLT-003, FR-PLT-023..025, NFR-AVL-002, NFR-AVL-005, NFR-FLT-001, NFR-FLT-002, NFR-PRV-001, SEC-028, SEC-030; 01 §11; 04 §16; 08 §1, §14; 10 §15; 16.
