# ADR-0011: Hash-chained, append-only audit log

| Field | Value |
|---|---|
| Status | Accepted (recorded retroactively 2026-09-26) · Amended by [ADR-0013](ADR-0013-cross-tenant-access-and-platform-privilege-separation.md) (per-tenant `seq`, RFC 8785 canonical JSON, genesis hash, TRUNCATE trigger, RLS on partitions, separate platform audit chain) |
| Date | 2026-09-26 |
| Deciders | Founder |
| Amends / supersedes | none |

## Context

Schools must be able to show who changed or viewed sensitive data (US-1001). DPDP Rules require logs for breach detection and investigation for at least one year; CERT-In requires 180 days in India (08 §3, §6). An audit log that an insider or attacker can quietly edit is worth little. The audit event must exist if and only if the change it describes was committed.

## Decision

- Audit events are written with `audit.record()` **in the same database transaction** as the audited change (FR-AUD-001; CLAUDE.md §6 invariant 7).
- `audit.events` is **append-only**: no UPDATE/DELETE/TRUNCATE grants for application roles, plus a trigger that blocks UPDATE and DELETE (FR-AUD-002).
- Each tenant has its own **hash chain**: every event stores `prev_hash` and `hash = sha256(prev_hash || canonical_json(event))`. The chain head (`audit.chain_heads`) is locked `FOR UPDATE` while writing, which serializes audit writes per tenant (FR-AUD-003; 05 §7).
- Events contain IDs, field names and counts, never raw personal values.
- A daily job verifies every chain and exports signed daily batches to an S3 bucket with **Object Lock** (compliance mode) (FR-AUD-004).
- `audit.events` is range-partitioned by month; partitions are created ahead by a scheduled job.
- The audit viewer supports filters, CSV export and an integrity check for `audit.read` holders (FR-AUD-005).
- Audited actions include identity data changes, role/permission changes, exports, AI queries, break-glass and logins.

## Consequences

- Good: tampering is detectable; the school can prove the integrity of its history.
- Good: same-transaction writes mean no "ghost" or missing events.
- Bad: per-tenant serialization of audit writes limits write throughput per tenant; acceptable at our scale.
- Bad: canonical JSON must be exactly reproducible across versions; ADR-0013 pins RFC 8785.

## Alternatives considered

| Option | Why not chosen |
|---|---|
| Application logs as the audit trail | Mutable, not transactional, contain technical noise |
| External audit service / ledger database | Extra sub-processor or service; loses same-transaction guarantee |
| Single global chain | Serializes all tenants; a tenant export would include others' hashes |

## Related requirements

FR-AUD-001..005, SEC-007, T4, T6 (07 §4), NFR-PRV-002; 05 §7, §13; 11 §5–6; 12 §4.7.
