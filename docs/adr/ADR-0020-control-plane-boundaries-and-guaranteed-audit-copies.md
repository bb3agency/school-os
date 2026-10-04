# ADR-0020: Control-plane boundaries and guaranteed audit copies

| Field | Value |
|---|---|
| Status | Accepted · implementation amendments 2026-09-29 (ADR-0029), 2026-10-01 (ADR-0038; B2 approved by the product owner 2026-10-03), 2026-10-03 (B3, AI budget from the bundle) |
| Date | 2026-09-27 |
| Deciders | Founder (product owner decisions of 2026-09-27 on the M0 "decisions needed" list, [14 · M0 status](../14-roadmap.md#m0-status-2026-09-26)) |
| Amends / supersedes | Amends [ADR-0013](ADR-0013-cross-tenant-access-and-platform-privilege-separation.md) (settles Amendments A6 and A10) and [ADR-0017](ADR-0017-platform-admin-panel-architecture.md) (what `platform` may call on the tenant side) |

## Context

The M0 build left two deviations open (ADR-0013 Amendments A6 and A10, recorded in 14 · M0 status):

1. **School-chain copies of platform actions were best effort.** Lifecycle actions (`tenant.provisioned`, `tenant.activated`, `tenant.suspended`, `tenant.reactivated`, `tenant.offboard_approved`) must appear in the school's own audit chain (FR-AUD-001, CLAUDE.md invariant 7) as well as in `platform.audit_events`. The code wrote the school event in a `sos_app` transaction that committed right **after** the platform transaction. A failure between the two commits left the platform change without its school-chain event. One database transaction cannot span the `sos_platform` and `sos_app` connections, and a `SECURITY DEFINER` function that writes tenant audit events would widen the pinned allowlist (ADR-0013 §2).
2. **Code boundaries.** CLAUDE.md §4 and ADR-0017 said `platform` uses only `platform_session()` and never imports tenant modules. In fact `platform` calls `app.tenancy.service` (definer-function wrappers, key initialisation, usage counts) and opens `tenant_session()` for a few narrow purposes. The rule needed to say what is allowed, and a test needed to hold it.

## Decision

### 1. Transactional outbox for school-chain copies

- Table **`platform.tenant_audit_outbox`** (migration `0015_platform_decisions`), owned by `sos_owner`, schema `platform` (no RLS, no student data): `id` (UUIDv7, the **platform event ID**), `seq` (identity, delivery order), `tenant_id`, `action`, `resource_type`, `resource_id`, `summary` (jsonb object), `actor_id` (operator, or null for the system), `request_id`, `created_at`, `delivered_at`, `attempts`, `last_error` (a code).
- Grants: `sos_platform` has `SELECT, INSERT` and `UPDATE (delivered_at, attempts, last_error)` only. No `DELETE` or `TRUNCATE`, so queued copies cannot be dropped by the app. `sos_app`, `sos_readonly` and `sos_definer` get nothing.
- A control-plane action that needs a school-chain copy MUST call `app.platform.tenant_audit.enqueue(platform_session, tenant_id, actor, action, summary)` **inside the same platform transaction** as the change and its `record_platform()` event. The change, the platform event and the queued copy commit together or not at all. `enqueue` validates the summary exactly as `audit.record` does (IDs, codes and counts only; personal keys, emails, phone-like digit runs are rejected), so a bad summary fails the platform action rather than a later delivery.
- Dedicated-tier schools get no copy from the shared control plane (their rows live on their host). On the host itself, `provision_dedicated` queues copies in the host's own `platform` schema.
- **Delivery:** the Celery task `platform.deliver_tenant_audit` (queue `maintenance`, every minute, **both** deployment modes) calls `deliver_pending()`. Per row, in one `platform_session` transaction:
  1. Lock the oldest undelivered row whose school has no older undelivered row (`FOR UPDATE SKIP LOCKED`). A school's rows are therefore delivered strictly in `seq` order, while different schools proceed in parallel and concurrent workers never take the same row.
  2. Open that school's `tenant_session` (role `sos_app`, RLS applies). Take a transaction-level advisory lock on the event ID. Look for an `audit.events` row of that school whose summary carries `platform_event_id = <id>`. If none exists, write it with `audit.record(..., actor_type="platform")` (the summary includes `platform_event_id`). Commit.
  3. Mark the outbox row delivered and commit.
- **Exactly once:** a crash after step 2 and before step 3 leaves the row undelivered. The retry finds the event by its `platform_event_id` and only marks the row delivered. The advisory lock covers the case where two deliverers reach the same row (for example, a platform connection dropped while a tenant write was in flight).
- **Failures:** a failing row keeps `attempts` and a `last_error` code (`db_error`, `summary_rejected`, `no_tenant_context`, `delivery_failed`). It blocks only its own school's later rows. The worker logs `platform.tenant_audit.failed` (and `platform.tenant_audit.stuck` from the configured attempt count). It logs `platform.tenant_audit.backlog` when the oldest undelivered row is older than the configured minutes (`apps/api/app/platform/billing.yaml` → `tenant_audit`).
- **Latency:** after its platform transaction commits, the action calls `deliver_now(tenant_id)` as a best-effort delivery for that school. In the common case the school sees the event immediately. The scheduled task guarantees delivery.

### 2. What the control plane may call on the tenant side

`platform` uses `core.db.platform_session()` for its own data. It MAY call `app.tenancy.service` **only for tenant lifecycle**:
- register (`register_tenant`);
- initialise keys (`initialise_tenant`);
- activate, suspend, reactivate, offboard (`activate_tenant`, `suspend_tenant`, `reactivate_tenant`, `begin_offboarding`, `set_tenant_status`);
- usage counts (`tenant_usage`, and `list_tenant_ids` for the daily fan-out);
- the AI answer allowance of the school's subscription (`set_ai_answer_allowance`, amendment B3).

It MUST NOT read tenant data. Pinned tenant-side imports, with the names they may bring in:
- `app.tenancy.schemas.TenantProvisionIn`;
- `app.identity.service` (a side-effect import that registers the role-cloning hook, used by `provision_dedicated` only);
- `app.identity.principal` (`Principal`, `get_operator_principal`, `require_recent_auth`);
- `app.identity.service_token` (replay stores);
- `app.ops.idempotency` (KV-backed `Idempotency-Key` helpers; `ops.idempotency_keys` is never touched).

`core`, `authz` and `audit` stay free for every module (CLAUDE.md §4). `platform` never imports any module's `repository` or `models`, nor `students`, `documents`, `dq`, `changes` or `imports`.

`tenant_session()` MAY be opened only in:
- `platform/tenant_audit.py`: delivery and its dedupe lookup of one event ID;
- `platform/usage.py`: aggregate counts per school per day (active users; questions and billable AI answers, amendment B2).

The school-side routes in `platform/tenant_api.py` use the request's `TenantDB` like every tenant route. Raw SQL in `platform` may name only these tenant-side relations:
- `audit.events`, in those two files, and `kb.queries` in `usage.py` (counts only, amendment B2);
- the definer functions `core.current_subscription()` and `core.create_owner_invite()`.

`apps/api/tests/platform/test_boundaries.py` enforces all of this with an AST scan of `app/platform/**/*.py`. Changing any of its lists is a boundary change: update this ADR in the same PR, or write a new ADR if the decision changes.

## Consequences

- Good: FR-AUD-001 / invariant 7 hold for platform actions: the platform change and the school's copy are committed atomically (as a queued copy) and delivered exactly once, in order per school. No new definer function or `definer_access` policy.
- Good: the control plane's reach into tenant code is explicit, small and tested; a new call fails CI until someone decides it is lifecycle.
- Bad / costs: the school's copy can lag the platform change by up to a minute when the inline delivery fails, and it sits in `platform` until then (IDs and codes only). One more table and beat task, in both modes. A permanently failing row blocks that school's later copies until fixed (by design; it alerts).
- Follow-up: add the backlog alert to 11 §11–12 dashboards; the `tenant.deleted` event (M1 deletion job) must use `enqueue` too.

## Alternatives considered

| Option | Why not chosen |
|---|---|
| Keep the post-commit best-effort write (ADR-0013 A6) | Loses the school-chain event if the second commit fails; violates invariant 7 |
| `SECURITY DEFINER` function that writes `audit.events` from the platform transaction | Widens the pinned definer allowlist to tenant audit writes; the hash chain would then be written by two code paths |
| Write the copy into `ops.outbox` | `sos_platform` has no privileges on tenant tables (invariant 1); granting them breaks privilege separation |
| Two-phase commit across the two connections | Operational complexity (prepared transactions) for a rare, low-volume event |
| Dedupe by a unique index on `audit.events` | Needs a schema change on the partitioned, append-only audit table; the summary key plus advisory lock is enough at this volume |

## Related requirements

FR-AUD-001, FR-PLT-002, FR-PLT-004, FR-PLT-005, SEC-007, SEC-026; CLAUDE.md §4 and invariant 7; docs/16 §5.4, §16, §17; 14 · M0 status.

## Amendments (2026-09-29)

Implementation facts only; the decision stands (policy in docs/adr/README.md).

**B1 · Offboarding lifecycle calls (ADR-0029, approved by the product owner 2026-09-29).** The
"offboard" lifecycle family now includes the deletion job's calls, which `platform` makes through
`app.tenancy.service`: `tenant_data_inventory`, `purge_tenant`, `verify_tenant_purged`,
`destroy_tenant_keys` and `purge_expired_audit_chain`. Each opens the school's own
`tenant_session` inside `tenancy` and returns counts and codes only; `platform` still opens no
`tenant_session`, imports no new tenant-side module and names no new tenant relation in SQL.
`TENANCY_ALLOWED` in `apps/api/tests/platform/test_boundaries.py` lists the five names. The
`tenant.deleted` school-chain copy goes through `tenant_audit.enqueue` in the certificate
transaction (the follow-up above).

## Amendments (2026-10-01)

**B2 · Billable AI answer count (ADR-0038).** `platform/usage.py` already opens the school's own
`tenant_session` for one aggregate count per school per day (distinct active users from
`audit.events`). In the same pattern it now also counts, per IST day, the school's `kb.queries`
rows and those with a billable status (`billing.yaml` → `ai_answers.billable_statuses`) and
stores only the two numbers (`platform.usage_daily.ai_queries`, `ai_answers`). RLS applies (the
query runs as `sos_app` with the school's tenant context); no column other than `status`,
`tenant_id` and `created_at` is read; no question, answer, person or ID leaves the session. No
definer function, `definer_access` policy or grant was added, and `platform` imports no new
tenant-side module. `SQL_ALLOWED` in `apps/api/tests/platform/test_boundaries.py` gains
(`usage.py`, `kb.queries`); `TENANT_SESSION_FILES` is unchanged. A dedicated host runs the same
function locally and sends the count in its heartbeat.

*Approved by the product owner on 2026-10-03* (counts only, in the school's own
`tenant_session`); docs/16 §19 Q18 is closed. No code change.

## Amendments (2026-10-03)

**B3 · AI answer allowance from the bundle (ADR-0038 amendment 2026-10-03; owner decision
2026-10-03).** A school's monthly AI budget is derived from its AI answer bundle. The bundle lives
in `platform.ai_bundles`; the tenant side may not read it. The control plane hands over **one
number**, the bundle's included answers a month, through a new lifecycle-style call
`app.tenancy.service.set_ai_answer_allowance(tenant_id, answers | None)`. Like the B1 calls it
opens the school's own `tenant_session` inside `tenancy` (sos_app, RLS `own_tenant`), stores
the number under its own key `ai_answers_per_month` in `core.tenants.settings` (not a school
setting: the settings form can neither show nor change it, and a settings edit keeps it), bumps
`version`, audits `tenant.ai_allowance_set` (`included_answers`, system actor) in the same
transaction and returns only whether it changed. It is idempotent.
`platform/billing.py` calls it after a bundle is chosen, changed or removed (after the platform
transaction commits; a failure is logged `platform.ai_allowance.sync_failed`, ids only, and the
operator's action stands), and `platform/usage.py` calls it for every live school in the daily
collector, which repairs a missed write. Only shared-tier schools are reached; a dedicated host's
school keeps its own budget setting until the heartbeat response can carry the allowance
(docs/16 §19 Q19). No migration, definer function, `definer_access` policy or grant was added;
`platform` imports no new tenant-side module, opens no new `tenant_session` and names no new
tenant relation in SQL. `TENANCY_ALLOWED` in `apps/api/tests/platform/test_boundaries.py` gains
`set_ai_answer_allowance`. Rejected alternatives: a value in `platform.feature_flags` (the table
has no value column, so it would need a migration, and dedicated hosts' flags are written by the
deploy pipeline, docs/16 §19 Q5); extending the definer `core.current_subscription()` to the
bundle (a wider definer read, ADR-0013).
