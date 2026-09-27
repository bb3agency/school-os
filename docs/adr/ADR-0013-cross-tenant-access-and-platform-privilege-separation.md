# ADR-0013: Cross-tenant access paths, platform privilege separation and platform identity

| Field | Value |
|---|---|
| Status | Accepted · Amended by ADR-0018, ADR-0019, ADR-0020 · implementation amendments 2026-09-26, 2026-09-27 (see [Amendments](#amendments-2026-09-26)) |
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

## Amendments (2026-09-26)

Recorded after the M0 build (migrations `0001_baseline` … `0007_accept_invitations`). The decision above stands. These entries record where the implementation differs in detail, so readers do not have to diff the ADR against the code. Entries marked **(deviation)** change something the ADR promised; they are listed for a product decision in [14 · M0 status](../14-roadmap.md#m0-status-2026-09-26). The authoritative sources are the migrations, `infra/db/bootstrap.sql` and `apps/api/tests/security/rls_allowlist.yaml`; [05 §3](../05-data-model.md#3-database-roles-and-tenant-context) documents them in full.

**A1 · Roles (§1).**
- `sos_migrator` is a member of **both** `sos_owner` and `sos_definer` `WITH INHERIT FALSE, SET TRUE`. It gets no implicit privileges, but a migration can `SET ROLE sos_definer` to create definer functions. The bootstrap admin is granted both roles too. `sos_app`, `sos_platform` and `sos_readonly` are members of neither (test `test_SEC_002_app_and_platform_roles_cannot_become_owner_or_definer`). "Nobody can `SET ROLE` to it" therefore holds for every runtime role, not for the migrator.
- Login roles have `search_path = pg_catalog, public`; `sos_app` and `sos_platform` have `idle_in_transaction_session_timeout = 30s`.
- `sos_readonly` reads `core`, `sis`, `kb` and `audit` (not `ops`) and has no privileges on `core.tenant_keys`.
- `sos_platform` has `USAGE` on schema `core` only so it can call definer functions; it has no table privileges there.

**A2 · Narrower `sos_app` grants than §1 states.** `0003_core_schema` narrows "DML on `core`" per table:
- `core.tenants`: `SELECT` and column-level `UPDATE (name, settings, version)`. No `INSERT` or `DELETE`: rows come from `core.provision_tenant`, status changes only from `core.set_tenant_status`.
- `core.users`: `SELECT` and column-level `UPDATE (display_name, email, phone_ciphertext, preferred_language, last_login_at, version)`. **No `INSERT` or `DELETE`**: users come from `core.create_user_for_invite` or `core.create_owner_invite`. The RLS policies `users_in_tenant` (SELECT) and `users_in_tenant_update` (UPDATE) require a membership in the current tenant.
- `core.tenant_keys`: `SELECT`, `INSERT` and column-level `UPDATE (retired_at)`. No `DELETE`: crypto-shredding is an offboarding action.
- `core.permissions`: `SELECT` only. `ops.outbox`: `SELECT` and `INSERT` only; only `ops.claim_outbox` marks rows dispatched.
- `audit.chain_heads`: `SELECT`, `INSERT` and `UPDATE`. The app creates a tenant's genesis head on the tenant's first audited action.

**A3 · Definer functions (§2).** Every function sets `search_path = pg_catalog, pg_temp` (not `pg_catalog, <schema>, pg_temp`), and its body is fully schema-qualified, including `pg_catalog.now()` and similar calls. Functions are created **as** `sos_definer`: the migration runs `GRANT CREATE ON SCHEMA core TO sos_definer`, `SET ROLE sos_definer`, `CREATE FUNCTION`, `SET ROLE sos_owner` and `REVOKE CREATE`, all inside one transaction. This is needed because `sos_owner` cannot `ALTER FUNCTION … OWNER TO` a role it is not a member of. The pinned list now has eleven functions:

| Function | EXECUTE | Returns | Differences from §2 |
|---|---|---|---|
| `core.resolve_login(p_subject text)` | `sos_app` | `TABLE (user_id, tenant_id, membership_id, tenant_status)` | Also returns the tenant status, so suspended schools are reported rather than hidden. Only active, unexpired memberships of active users |
| `core.find_user_id_by_subject(p_subject text)` | `sos_app` | `uuid` | — |
| `core.create_user_for_invite(p_subject, p_display_name, p_email citext, p_language)` | `sos_app` only (not `sos_platform`) | `uuid` (user ID only) | Needs tenant **and** user context. The inviter must hold an active, unexpired membership in a `provisioning` or `active` school. Creates or reuses the global user; the app then writes the membership, roles and scopes under RLS |
| `core.list_tenant_ids(p_status text[])` | `sos_app`, `sos_platform` | `TABLE (tenant_id uuid)` | — |
| `core.provision_tenant(p_id, p_code, p_name, p_boards, p_plan_tier, p_deployment_mode)` | `sos_platform` | `uuid` | **Creates only the tenant row** (status `provisioning`). `tenancy.initialise_tenant` writes the wrapped DEK and HMAC key in the new school's `tenant_session`; post-provision hooks clone the system roles; the genesis chain head appears with the first audit event |
| `core.set_tenant_status(p_tenant uuid, p_status text)` | `sos_platform` | `text` (the **previous** status) | Legal transitions only: `provisioning→active`, `active↔suspended`, `active→offboarding`, `suspended→offboarding`, `offboarding→deleted`. `provisioning→active` requires an unretired `core.tenant_keys` row. Writes **no** audit event; the caller does (A6) |
| `core.tenant_usage_summary(p_tenant uuid)` | `sos_platform` | `TABLE (active_memberships, users, sections, academic_years)` (int counts) | Counts only. Student, document, storage and AI counts join when those tables exist (M1/M2) |
| `core.current_subscription()` | `sos_app` | `jsonb` | The current tenant's plan, status, period, limits, latest usage row and last 24 non-draft invoices |
| `core.create_owner_invite(p_tenant, p_subject, p_display_name, p_email citext, p_language)` | `sos_platform` | `TABLE (user_id, membership_id, owner_role_assigned boolean)` | **New.** A school's first owner, only while the tenant is `provisioning` and has no members. Creates or reuses the user and adds an `invited` membership with `mfa_required = true`, a `school` scope, and the `owner` role once it has been cloned |
| `core.accept_invitations(p_subject text)` | `sos_app` | `TABLE (tenant_id, membership_id, user_id)` | **New** ([ADR-0019](ADR-0019-invitation-acceptance-on-first-sign-in.md)) |
| `ops.claim_outbox(p_batch int)` | `sos_app` | `TABLE (id, tenant_id, event_type, payload)` | Batch clamped to 1–500 (default 100); `FOR UPDATE SKIP LOCKED` |

The catalog tests check that every `SECURITY DEFINER` function in `core`, `sis`, `kb`, `audit`, `ops` and `platform` is on this list, is owned by `sos_definer` and pins `search_path`. The `0003` functions are also checked for their exact EXECUTE grantees and for `search_path=pg_catalog, pg_temp`.

**A4 · `definer_access` tables (§3).** The allowlist is the `definer_access_tables` key inside `apps/api/tests/security/rls_allowlist.yaml`, not a separate file. It lists `core.tenants`, `core.users`, `core.memberships`, `core.roles`, `core.role_permissions`, `core.membership_roles`, `core.membership_scopes`, `core.tenant_keys`, `core.academic_years`, `core.classes`, `core.sections`, `audit.chain_heads`, `audit.events` and `ops.outbox`. The policy currently exists on twelve of them; `core.role_permissions` and `core.classes` do not carry it yet.
- The catalog test fails when a table carries the policy **without** being listed. A second test asserts that the tables the `0003` functions need do carry it.
- Tables that `core.tenant_usage_summary` will count in later milestones (e.g. `sis.students`) are **not** listed yet. Adding them needs an allowlist change.
- Audit partitions do **not** carry `definer_access`. Every role except the owner has its privileges on partitions revoked, so partitions are reachable only through the parent.

`sos_definer` holds exactly these table privileges:
- `core.tenants`: `SELECT, INSERT, UPDATE`. `core.users`: `SELECT, INSERT`. `core.memberships`: `SELECT, INSERT` and `UPDATE (status, updated_at, version)`.
- `SELECT` on `core.tenant_keys`, `core.sections`, `core.academic_years` and `core.roles`. `INSERT` on `core.membership_scopes` and `core.membership_roles`.
- `SELECT, INSERT` on `audit.events` and `audit.chain_heads`. `SELECT, UPDATE (dispatched_at)` on `ops.outbox`.
- `SELECT` on `platform.plans`, `platform.subscriptions`, `platform.invoices` and `platform.usage_daily`.

**A5 · Platform permissions (§4).** The ADR planned `CHECK (key NOT LIKE 'platform.%')`. Instead, the `platform.*` keys **are** rows of `core.permissions`, with `is_platform = true`. The catalog is therefore complete, and route guards can be checked against one table.
- `CHECK (is_platform = starts_with(key, 'platform.'))` ties the flag to the prefix.
- The trigger `role_permissions_not_platform` refuses to grant any platform permission to a tenant role.
- `require()` refuses platform keys; `require_platform()` refuses non-platform keys.
- The single catalog is `apps/api/app/authz/permissions.yaml`, seeded by `0004_authz_seed`. The operator role matrix and the two-person list are in `apps/api/app/platform/roles.yaml`. There is no `config/platform_permissions.yaml`.
- The catalog also holds the implicit `session.authenticated`. Every active member has it without a role grant, and it is never stored in `core.role_permissions`.

**A6 · School-chain events for platform actions (§5) (deviation).** `platform.audit_events` and `platform.audit_chain_head` are created in `0002_audit` together with the tenant chain, not in `0005_platform`. `sos_platform` has `SELECT, INSERT` on the events and `SELECT, UPDATE` on the head. Each platform event is written in the same transaction as the control-plane change.

The school-chain copies (`tenant.provisioned`, `tenant.activated`, `tenant.suspended`, `tenant.reactivated`, `tenant.offboard_approved`; `actor_type = 'platform'`) are **not** written by a definer function:
- `audit.record()` writes them in a `tenant_session` (`sos_app`) that is opened around the platform transaction and committed **right after** it.
- If the platform transaction fails, both roll back.
- If the tenant commit fails after the platform commit, the platform change stands without its school-chain event.
- Reason: one transaction cannot span the `sos_platform` and `sos_app` connections, and a definer function that writes tenant audit events would widen the allowlist.
- Dedicated schools get no school-chain event from the control plane, because their rows live on the host.

**A7 · Audit chain (§6).** `audit.events` has `PRIMARY KEY (tenant_id, seq, occurred_at)` on the parent, and so on every partition. There is no separate `UNIQUE (tenant_id, seq)` per partition: the locked head gives contiguous sequence numbers, and the verifier detects gaps, duplicates and reordering.
- `occurred_at` is set by the application with microsecond precision, so the hashed value equals the stored one. The column default `clock_timestamp()` is only a fallback.
- Monthly partitions are named `audit.events_yYYYYmMM`. Each has RLS enabled and forced with `tenant_isolation`, a `BEFORE TRUNCATE` trigger, and all privileges revoked from `sos_app`, `sos_readonly` and `sos_definer`. There is no `DEFAULT` partition.
- Partitions are created by the owner-only, `SECURITY INVOKER` function `audit.create_month_partition`. `0002_audit` calls it, and so does `python -m app.audit.partitions --months-ahead 12` (run as the migrator) after every `alembic upgrade head`.

**A8 · Tenant context (§8).** `tenant_session()` sets `app.tenant_id`, `app.user_id` and `statement_timeout` in one `set_config(…, true)` statement. `core.db.context_free_session()` opens an `sos_app` transaction with no tenant context. It is used only to call `core.resolve_login`, `core.find_user_id_by_subject`, `core.list_tenant_ids` and `core.accept_invitations`.

**A9 · Jobs (§9).** `ops.job_runs` and `platform.job_runs` also record `created_by`.

**A10 · Control-plane code paths (§4) (deviation).** `app.platform` calls `app.tenancy.service` wrappers for `core.provision_tenant` and `core.set_tenant_status`, passing its own `platform_session`. It also calls `tenancy.initialise_tenant` to create the new school's keys. It opens `tenant_session()` (role `sos_app`, RLS applies) for three purposes only:
- the school-chain events of A6;
- the school-side routes it serves (`/tenant/billing`, `/announcements`, `/support/tickets`);
- one aggregate count per school for usage (distinct users with audit events that day).

`sos_platform` itself still has no tenant-table privileges.

**A11 · Heartbeat keys.** Per-deployment heartbeat keys are stored **wrapped** in `platform.deployments.heartbeat_key_ciphertext`, not hashed: KMS in AWS, the local-dev wrapper elsewhere. The control plane has to recompute the HMAC, so it needs the key itself.

## Amendments (2026-09-27)

Reference only (implementation facts; the decisions are in the ADR named).

**A12 · A6 and A10 settled by [ADR-0020](ADR-0020-control-plane-boundaries-and-guaranteed-audit-copies.md).** They are no longer open deviations. School-chain copies of platform actions are queued in `platform.tenant_audit_outbox` (migration `0015_platform_decisions`) inside the platform transaction and delivered exactly once, in order per school, by the task `platform.deliver_tenant_audit`; the post-commit `tenant_session` write described in A6 no longer exists. What `platform` may call in `app.tenancy.service`, which files may open `tenant_session()`, and which tenant relations its raw SQL may name are pinned by `apps/api/tests/platform/test_boundaries.py`. No definer function, `definer_access` policy or tenant-table grant was added.
