# ADR-0022: System-role sync for existing schools

| Field | Value |
|---|---|
| Status | Accepted · Amended by [ADR-0026](ADR-0026-dedicated-upgrades-apply-system-role-sync.md) |
| Date | 2026-09-27 |
| Deciders | Product owner (decision of 2026-09-27 on ADR-0021 follow-up (a)) |
| Amends / supersedes | none (settles [ADR-0021](ADR-0021-export-access-and-step-up.md) follow-up (a); adds no definer function, policy, role or RLS change, so ADR-0013 is unchanged) |

## Context

The nine FR-IAM-010 system roles and their grants are defined in `apps/api/app/authz/roles.yaml` and copied into a school only at provisioning (`identity.service.clone_system_roles`, a post-provision hook). `core.roles` and `core.role_permissions` are tenant tables under FORCE RLS, so a migration running as `sos_migrator`/`sos_owner` can neither see nor write them, and no allowlisted `SECURITY DEFINER` function grants permissions (docs/05 §3.3-3.4). When a release adds or changes a system-role grant, existing schools never receive it. ADR-0021 is the first case: migration `0019_export_access` added `export.read_all` (owner, principal, office_admin) and `export.download_any` (owner) to the catalog, but schools provisioned earlier do not have the grants.

Facts checked on 2026-09-27:

- Grants carry no per-grant metadata in the database. `core.role_permissions` is `(tenant_id, role_id, permission_key)`. Whether a system-role grant is school-wide or scoped is read from roles.yaml by the resolver (`authz.resolver.build_snapshot`), and step-up comes from `core.permissions.step_up` (seeded by migrations). Scope and step-up changes therefore reach every school at release time. Only the **set of grants** (and a role's display names) can drift.
- No API lets a school change a system role's grants. Only `clone_system_roles` and the break-glass role helper write role grants. Drift in system roles therefore comes only from releases.
- The break-glass role `platform_support` is also stored with `is_system = true`. It is not an FR-IAM-010 system role: it is defined in roles.yaml `breakglass_role` and brought up to date by its own helper each time support access is opened.
- Tenant statuses are `provisioning`, `active`, `suspended`, `offboarding` and `deleted`.

## Decision

1. **An operator-run command** `python -m app.identity.sync_system_roles` (module `app/identity`, which owns roles; logic in `identity.service.sync_system_roles`). It is not part of `platform`, because the control plane must not write tenant tables (CLAUDE.md §4, ADR-0020).
2. **Isolation.** Schools come from the allowlisted definer function `core.list_tenant_ids` (via `tenancy.service.list_tenant_ids` in a `context_free_session`), exactly as the per-tenant worker jobs do. Each school is handled in **its own** `tenant_session` as `sos_app`: one transaction per school, RLS applies, and a school's run cannot see or change another school's rows. The service refuses a session whose tenant context is not the school it was asked to sync. There is no new definer function, `definer_access` policy, role or BYPASSRLS.
3. **What it changes (only FR-IAM-010 system roles).** For each role key in roles.yaml:
   - missing role: created with `is_system = true` and its grants;
   - missing grants on the school's system role: added;
   - display names (`name_en`, `name_te`) that differ from roles.yaml: updated;
   - grants roles.yaml no longer lists: **kept and reported by default**. They are removed only with the explicit `--prune` flag, because removing access can lock people out mid-term. Operators run a dry run with `--prune` first.
   It MUST NOT change custom roles (`is_system = false`), `platform_support`, or system roles whose key roles.yaml no longer defines (reported as `unknown_system_roles`, never deleted, because memberships may still hold them). A roles.yaml key held by a custom role in that school is a **conflict**: reported, not changed, and the run exits 4. Scope and step-up metadata need no reconciliation (see Context).
4. **Dry run by default.** Without `--apply` the per-school transaction is made read-only (`SET TRANSACTION READ ONLY`), so nothing can be written. `--apply` writes. `--tenant <id>` limits the run to one school. `--prune` works with both modes.
5. **Which schools.** `provisioning`, `active` and `suspended` schools are included. A suspended school keeps its data and will be reactivated with correct roles, and a provisioning school gets exactly what provisioning would give it. `offboarding` and `deleted` schools are skipped. `--tenant` naming a skipped or unknown school is refused (exit 1).
6. **Audit (invariant 7, SEC-007).** Every change is audited with `audit.record()` in the same transaction, `actor_type = system`, keys and counts only (no names, e-mails or other personal data), and `via = "system_role_sync"` in the summary: `role.created` (role key, permissions), `role.permission_granted` / `role.permission_revoked` (role key, permission), `role.updated` (role key, `fields`), then one `role.system_sync_applied` per school (resource the tenant; counts of roles created, grants added and removed, roles updated, and `prune`). A school already in line gets **no** events, so a second `--apply` changes nothing (idempotent). The permission cache of the school is invalidated after commit (FR-IAM-014; 60 s TTL otherwise).
7. **Safety.**
   - It refuses (exit 1) unless the connection's role is `sos_app` and is neither a superuser nor BYPASSRLS. It also refuses until the migrations have put every roles.yaml permission into `core.permissions` (`catalog_not_migrated`), so it cannot run before the release's migrations.
   - On a dedicated host (`SOS_DEPLOYMENT_MODE=dedicated`) it handles only `SOS_DEDICATED_TENANT_ID` (refused if unset or if `--tenant` names another school).
   - Concurrent runs for one school are serialised by a transaction-level advisory lock.
   - A failing school is logged and reported, and the run continues with the next school.
8. **Output and exit codes.** One line per school (`tenant=<id> result=in_line|pending|applied|failed` with counts), one line per change (role and permission keys only) and a summary line. Logs carry tenant IDs and counts only. Exit codes: `0` in line or applied · `1` refused, nothing done · `2` invalid arguments · `3` dry run found changes to make · `4` at least one school failed or has a conflict.
9. **Release procedure.** When a release changes the system roles in roles.yaml, operators run the command after the release's migrations: a dry run, then `--apply` (docs/10 §8 and §15.5). A pinned fingerprint test (`tests/authz/test_system_role_fingerprint.py`) fails when the system roles change, so the PR must update the pin and flag the release for the sync.

## Consequences

- Good: grants added by a release (starting with ADR-0021's) reach existing schools without widening any cross-tenant path. Every change is visible in each school's own audit chain.
- Good: safe to re-run. A dry run shows exactly what would change, as keys and counts.
- Bad / costs: it is a manual post-release step. Until an operator runs it, existing schools lack new grants (fail closed: people get less access, never more). Removals need a deliberate `--prune`.
- Bad / costs: on the shared tier the run is recorded only in the schools' chains and the task logs, not in the platform audit chain, because the command runs as `sos_app`, not as an operator through the panel.
- Follow-up work: run it once in every environment after this change ships, so existing schools get the ADR-0021 grants. Decide whether `deploy/dedicated/scripts/upgrade.sh` (fleet waves) should run it automatically after `migrate`; it is manual for now.

## Alternatives considered

| Option | Why not chosen |
|---|---|
| Grant in a migration | The migrator cannot see or write tenant role rows (FORCE RLS). Making it able to needs a definer function or policy and an ADR-0013 change for a routine release task. |
| A new `SECURITY DEFINER` function that grants permissions in every school | It widens the allowlisted cross-tenant write paths. The per-school `tenant_session` path already exists and keeps RLS in force. |
| Run it from the control plane (`platform`) | `platform` must not write tenant tables (CLAUDE.md §4, ADR-0020). |
| Re-run `clone_system_roles` in every school | It only adds missing grants, has no dry run, prune or conflict handling, and would treat a custom role with a system key as a system role. |
| Remove stale grants by default | Removing access mid-term can lock staff out. Removal must be a deliberate, reviewed step (`--prune`). |
| Apply automatically at API start-up or in a beat task | Changes to access should be a visible operator action with a dry run, not a side effect of a deploy. |
| Store scope/step-up per grant and reconcile it | Not needed: the resolver reads both from versioned config at request time, so they cannot drift per school. |

## Related requirements

FR-IAM-010, FR-IAM-011, FR-IAM-014, SEC-003, SEC-007, ADR-0013, ADR-0020, ADR-0021; docs/05 §4 (permission catalog), docs/07 §6.2, docs/10 §8 and §15.5, `deploy/dedicated/README.md`.

## Amendments (2026-09-27)

Reference only (implementation facts; the decision is in the ADR named).

**A1 · Follow-up "run it from upgrade.sh" settled by [ADR-0026](ADR-0026-dedicated-upgrades-apply-system-role-sync.md).** `deploy/dedicated/scripts/upgrade.sh` calls `upgrade_sync_system_roles` (`scripts/lib.sh`), which runs the activated release's `scripts/sync-system-roles.sh --apply` (never `--prune`) after `migrate` and before the services restart; any exit other than `0` fails the upgrade and the ERR trap rolls the host back. Tests: `apps/api/tests/deploy/test_upgrade_role_sync.py`. The shared tier is unchanged: the one-off ECS task uses the worker task definition's container `app` (docs/10 §8 item 7).

**A2 · Lockout guard for `--prune`** (owner decision of 2026-09-27). A roles.yaml edit followed by `--prune` could remove the grants a school needs to administer itself, leaving nobody who can invite staff or assign roles; only a platform-side repair could undo that. The system-role grants listed in the versioned file `apps/api/app/authz/protected_grants.yaml` (next to roles.yaml; invariant 13) are therefore **never removed**, not even with `--prune`. Initially: the owner's `user.manage`, `role.assign` and `tenant.settings.manage`. Such a removal is reported as `  ! <role> <permission> protected (lockout guard); not removed`, counted as a conflict (exit 4) in dry runs and with `--apply`, and not applied; the school's other changes still apply. The per-school line gains `grants_protected=<n>` and the `role.system_sync_applied` summary gains `grants_protected` (count only). The file is validated at load (version 1, system role keys from roles.yaml, tenant permissions only), and `tests/identity/test_sync_system_roles.py` checks that roles.yaml grants every protected pair today. To really take one of these grants away, first amend this ADR and remove the entry from the file. No definer function, policy, role or RLS change; the roles fingerprint is unchanged. Decisions 3, 6 and 8 above are otherwise unchanged.
