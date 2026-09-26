# ADR-0013: Cross-tenant access paths, platform privilege separation and platform identity

| Field | Value |
|---|---|
| Status | Accepted |
| Date | 2026-09-26 |
| Deciders | Founder (product owner approval of build proposals B1–B25) |
| Amends / supersedes | Amends [ADR-0003](ADR-0003-pool-tenancy-rls.md), [ADR-0011](ADR-0011-hash-chained-audit.md), [ADR-0012](ADR-0012-managed-oidc-identity.md) |

## Context

ADR-0003 isolates tenants with RLS and an application role without `BYPASSRLS`. Some work legitimately crosses tenants or happens before a tenant is known:

- **Login:** the user's memberships must be found before a tenant is chosen.
- **Outbox dispatch:** one dispatcher claims pending events for all tenants.
- **Provisioning, suspension and usage counts:** the platform team creates schools and needs counts per school, but must never see student data (BR-09).
- **School-side plan page:** a school reads its own plan and invoices, which live with platform billing data.

The v0.1 docs handled login with one `SECURITY DEFINER` function owned by `sos_owner` and left the others implicit. That leaves several gaps:

1. A function owned by the table owner (`sos_owner`) can reach every table; a mistake in one function exposes everything.
2. The control plane (admin panel, billing, fleet) would run as `sos_app` and could read student tables if code went wrong.
3. PostgreSQL foreign-key checks do not apply RLS. A child row in tenant A can reference a parent row in tenant B by UUID if the FK is on `id` alone; `WITH CHECK` only checks the child's own `tenant_id`.
4. `SET LOCAL app.tenant_id = '…'` cannot take bind parameters, which tempts string-built SQL.
5. The audit chain's canonical JSON was not pinned, sequence gaps were not detectable, `TRUNCATE` was not blocked by the row trigger, and RLS on a partitioned parent does not protect direct access to a partition.
6. Platform operators need their own identity, roles and audit trail, separate from school users.

Amazon RDS restricts granting `BYPASSRLS`: its admin role is not a true superuser (checked September 2026; re-verify when the engine version changes). A design that relies on a `BYPASSRLS` role would therefore not deploy cleanly to the shared tier.

## Decision

### 1. Database roles (created by `infra/db/bootstrap.sql`, never by the app)

| Role | Login | RLS | Purpose and grants |
|---|---|---|---|
| `sos_owner` | NOLOGIN | — | Owns all tables in `core`, `sis`, `kb`, `audit`, `ops`, `platform` |
| `sos_migrator` | LOGIN, member of `sos_owner` | — | Alembic only; `env.py` runs `SET ROLE sos_owner` |
| `sos_app` | LOGIN | NOBYPASSRLS | Tenant API and workers. DML on `core`/`sis`/`kb`/`ops`; INSERT + SELECT only on `audit`; on `platform` only SELECT on `platform.feature_flags`; EXECUTE on allowlisted definer functions |
| `sos_platform` | LOGIN | NOBYPASSRLS | Control-plane code path. DML on schema `platform` (audit tables INSERT + SELECT only); **no privileges on any table in `core`, `sis`, `kb`, `audit`, `ops`**; EXECUTE on allowlisted definer functions. It physically cannot read student data |
| `sos_readonly` | LOGIN | NOBYPASSRLS | Reporting; tenant-scoped like `sos_app`; no privileges on `platform` |
| `sos_definer` | NOLOGIN | **NOBYPASSRLS** | Owns only the allowlisted `SECURITY DEFINER` functions. Nobody can log in as it or `SET ROLE` to it |

**No role in the system has `BYPASSRLS` or superuser.**

### 2. Allowlisted `SECURITY DEFINER` functions

Exactly these functions exist; each is owned by `sos_definer`, sets `search_path = pg_catalog, <schema>, pg_temp`, returns the minimum columns, and is audited by its caller:

| Function | Used by | Returns |
|---|---|---|
| `core.resolve_login(p_subject text)` | `sos_app` (login) | user id, tenant id, membership id of active memberships |
| `core.find_user_id_by_subject(p_subject text)` | `sos_app` | user id or NULL |
| `core.create_user_for_invite(...)` | `sos_app`, `sos_platform` | user id, membership id (invited) |
| `core.list_tenant_ids(p_status text[])` | `sos_app` (per-tenant job fan-out), `sos_platform` | tenant ids only |
| `core.provision_tenant(...)` | `sos_platform` | tenant id; creates tenant row, wrapped keys, audit chain head + first event, system roles |
| `core.set_tenant_status(p_tenant uuid, p_status text)` | `sos_platform` | new status; writes a tenant audit event |
| `core.tenant_usage_summary(p_tenant uuid)` | `sos_platform` (shared-tier usage job) | **counts only** |
| `core.current_subscription()` | `sos_app` | the current tenant's own plan, usage vs limits and invoice list from `platform` |
| `ops.claim_outbox(batch int)` | `sos_app` (dispatcher) | pending outbox rows (IDs-only payloads) |

A catalog test pins this exact list and fails if any `SECURITY DEFINER` function is missing from it, is owned by another role, or does not set `search_path`.

### 3. Cross-tenant reach without `BYPASSRLS`: per-table `definer_access` policy

Inside a `SECURITY DEFINER` function `current_user` is `sos_definer`. Tables that a definer function must touch get one extra **permissive** policy:

```sql
CREATE POLICY definer_access ON core.memberships
  USING (current_user = 'sos_definer') WITH CHECK (current_user = 'sos_definer');
```

It is added **only** to: `core.tenants`, `core.users`, `core.memberships`, `core.roles`, `core.role_permissions`, `core.membership_roles`, `core.membership_scopes`, `core.tenant_keys`, `audit.chain_heads`, `audit.events` (and each of its partitions), `ops.outbox`, and the tables counted by `core.tenant_usage_summary`. `sos_definer` receives only the table privileges those functions need (for example SELECT on the counted tables, SELECT on the `platform` billing tables read by `core.current_subscription()`). The list of tables carrying `definer_access` lives in an allowlist file next to `apps/api/tests/security/rls_allowlist.yaml`; a catalog test fails on any difference.

### 4. Platform schema and platform identity

- Control-plane data (operators, plans, subscriptions, billing accounts, invoices, payments, deployments, usage, feature flags, announcements, support tickets, platform audit, platform jobs) lives in schema **`platform`** (full DDL in [16 §7](../16-platform-admin-panel.md#7-data-model-schema-platform)).
- `platform` holds **no student data** and is not tenant-owned, so it is **exempt from RLS**. Isolation from tenant data is by grants instead: the catalog test asserts `sos_app`/`sos_readonly` have no privileges on `platform` tables except `SELECT` on `platform.feature_flags`, and `sos_platform` has no privileges on `core`/`sis`/`kb`/`audit`/`ops` tables.
- The API process holds two connection strings: `SOS_DATABASE_URL` (`sos_app`) and `SOS_PLATFORM_DATABASE_URL` (`sos_platform`). `core.db.tenant_session()` uses the first; `core.db.platform_session()` uses the second. Control-plane routes (`/api/v1/platform/*`) use only `platform_session()`.
- **Platform operators** are a separate identity: rows in `platform.operators`, roles in `platform.operator_roles` (`platform_owner`, `platform_engineer`, `support_agent`, `billing_admin`, `platform_viewer`). They sign in through a **separate OIDC client** (`SOS_PLATFORM_OIDC_ISSUER`, `SOS_PLATFORM_OIDC_AUDIENCE`, web `PLATFORM_OIDC_CLIENT_ID`; in the reference setup a separate Cognito user pool with MFA ON, ADR-0018), with **MFA mandatory for every operator** and step-up (≤ 5 min) for risky permissions. The web app serves the panel under `/[locale]/platform/*` on its own host (`admin.<domain>`) with its own session cookie.
- Platform permissions (`platform.*`, catalog `config/platform_permissions.yaml`) can **never** be granted to tenant roles: `core.permissions` has `CHECK (key NOT LIKE 'platform.%')`, and platform roles are not rows in `core.roles`. Platform routes use `require_platform("platform.<…>")`.
- `platform_support` stays the *tenant-side* temporary break-glass role (07 §6.4); it is unrelated to platform roles.

### 5. Platform audit chain

- Control-plane actions are recorded with `audit.service.record_platform(...)` in `platform.audit_events` (one chain for the platform, head in `platform.audit_chain_head`), in the same transaction as the action.
- Actions that change a school (provision, status change) also write an event into that school's own chain (via the definer function, `actor_type = 'platform'`) so the school sees them in its audit viewer.

### 6. Audit chain hardening (amends ADR-0011)

Both tenant and platform chains use:

- `seq bigint` per chain, gapless, with `chain_heads.last_seq` (tenant) and `audit_chain_head.last_seq` (platform). The locked chain head guarantees `seq = last_seq + 1`; each monthly partition has a unique index on `(tenant_id, seq)` (PostgreSQL cannot enforce uniqueness across partitions without the partition key). Verification detects missing, duplicated or reordered events.
- **RFC 8785 JSON Canonicalization Scheme** (Python package `rfc8785`) for the hashed event body.
- `hash = sha256(prev_hash || jcs(event_without_hash))`; the **genesis** `prev_hash` is 32 zero bytes.
- A `BEFORE TRUNCATE` statement-level trigger that raises, in addition to the row-level UPDATE/DELETE trigger and missing grants.
- On partitioned `audit.events`, RLS (`tenant_isolation`, and `definer_access` where listed) is enabled and forced **on every partition**, because direct access to a partition does not use the parent's policies. The partition-creation job applies them.

### 7. Composite tenant foreign keys

Every reference from one tenant-owned table to another is a composite key:

```sql
ALTER TABLE sis.students ADD CONSTRAINT students_tenant_id_id_key UNIQUE (tenant_id, id);
ALTER TABLE sis.enrollments
  ADD CONSTRAINT enrollments_student_fk FOREIGN KEY (tenant_id, student_id)
  REFERENCES sis.students (tenant_id, id);
```

Parents get `UNIQUE (tenant_id, id)`. A test inserts a child that references another tenant's parent and expects a foreign-key violation.

### 8. Tenant context with bound parameters

`tenant_session()` sets context with

```sql
SELECT set_config('app.tenant_id', :tenant_id, true), set_config('app.user_id', :user_id, true);
```

`is_local = true` gives the same transaction-local behaviour as `SET LOCAL`, and the values are bound parameters.

### 9. Job bookkeeping

`ops.job_runs.tenant_id` is `NOT NULL` (tenant jobs, RLS applies). Platform-level jobs (invoice runs, usage collection, platform audit verification) record progress in `platform.job_runs`.

## Consequences

- Good: a bug in control-plane code cannot read or change student data; the database refuses.
- Good: a bug in a definer function can only reach the tables it is allowed to reach, not the whole database.
- Good: cross-tenant references are impossible at the database level.
- Good: works on RDS (no `BYPASSRLS` needed) and on dedicated hosts alike.
- Bad: more roles, policies and grants to maintain; mitigated by `bootstrap.sql`, migration conventions and catalog tests.
- Bad: new cross-tenant needs require an ADR amendment and a change to the pinned lists.
- Bad: composite keys make some indexes wider.
- Follow-up: catalog tests (12 §4.5, §4.8, §4.9, §4.10), audit concurrency test (12 §4.11), DDL updates in 05, controls SEC-026 and SEC-027 (07 §16).

## Alternatives considered

| Option | Why not chosen |
|---|---|
| `sos_definer` with `BYPASSRLS` | RDS cannot grant `BYPASSRLS` without superuser; and it would bypass RLS on **every** table, so one faulty function could expose all data |
| Definer functions owned by `sos_owner` (v0.1) | Owner reaches every table; same blast radius as above |
| Control plane as `sos_app` with code discipline | Nothing stops a bug from reading student tables; fails BR-09 "no standing access" in depth |
| Separate database for the control plane | Provisioning and plan reads would need cross-database calls or a sync service; more to run; revisit only if the control plane outgrows the shared DB |
| Single-column FKs plus service checks | FK checks ignore RLS; a service bug could link rows across tenants |
| `SET LOCAL` with string formatting | Violates "bound parameters only" (13 §4) |

## Related requirements

FR-TEN-001..003, FR-AUD-001..004, FR-PLT-001..005, FR-PLT-028..030, FR-IAM-002, SEC-001, SEC-002, SEC-007, SEC-026, SEC-027, BR-09; 05 §3, §7; 07 §6.5–6.6, §7; 12 §4.5–4.12; 16.
