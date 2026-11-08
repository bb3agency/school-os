# Security audit 2026-11-08: database and tenant isolation (TI-01..TI-05)

Auditor for the database and tenant-isolation area (finding prefix TI-). Worktree branch from
`987ded97`. Scope: RLS on every tenant table (ENABLE + FORCE, standard policy, restrictive delete
policies), composite FKs, role grants per role (`sos_app`, `sos_platform`, `sos_definer`,
`sos_purger`, `sos_migrator`) with extra attention to migrations 0060-0079, SECURITY DEFINER
functions against the pinned allowlist (ADR-0013, ADR-0023, ADR-0056), triggers (append-only,
set-once, freeze guards) and whether an app role can get past them, `tenant_session()` usage,
raw SQL, and the platform boundary (D78 board read, D79 heartbeat apply). Code added since
2026-11-01 was read in full where it touches the database.

## Method

- The live catalog was dumped after `alembic upgrade head` (testcontainers, production role
  model): every policy with its expression, command, roles and permissive/restrictive flag;
  table and column grants per role; role memberships and their options; schema ACLs and default
  privileges; every function in the tenant and platform schemas with owner, `proconfig`,
  EXECUTE grantees and body; every trigger; every FK; every unique index on a tenant table.
- A script cross-checked each `ON DELETE CASCADE` / `SET NULL` FK against the child table's
  grants and `BEFORE UPDATE/DELETE` triggers (referential actions run as the owner and bypass
  grants and RLS, but they do fire row triggers).
- The confirmed finding was reproduced with a test that failed before the fix.
- Baseline: `test_rls_catalog`, `test_definer_functions`, `test_composite_fks`,
  `test_app_column_grants` (87 tests) passed before any change.

## Findings

| ID | Severity | Area | File:line | Description | Status |
|---|---|---|---|---|---|
| TI-01 | **Medium**: data minimisation and a broken documented flow (docs/08, FR-DPDP-021); no cross-tenant reach | Triggers / privacy (consents) | `migrations/versions/0067_consents_breach.py:130-131` (`consents_guardian_fk ... ON DELETE SET NULL (guardian_id)`), `:159-169` (`tg_consents_append_only`) | The guardian FK's `ON DELETE SET NULL (guardian_id)` is an UPDATE of the decision row, which the append-only trigger refused for every role. A guardian who had once decided a consent (refused, given, withdrawn) could never be deleted: `DELETE /students/{id}/guardians/{gid}` on the last link answered **422 `consents_append_only`** and the guardian's encrypted phone and address stayed (`students.remove_guardian` deletes an unlinked guardian by design). The offboarding purge would hit the same refusal if guardians were deleted before consents. | Fixed in `fb0d3b3d`, migration `0080_audit_ti_fixes`: the trigger lets through exactly that referential action: an UPDATE fired by another trigger (`pg_trigger_depth() > 1`), only `guardian_id` from a value to NULL, every other column equal (`to_jsonb(NEW) - 'guardian_id' = to_jsonb(OLD) - 'guardian_id'`). A direct `SET guardian_id = NULL`, any other change and any DELETE outside the purge are still refused, also for the owner role. Test: `tests/privacy/test_consents_api.py::test_TI_01_FR_DPDP_021_a_guardian_who_decided_can_still_be_unlinked_and_deleted` (failed before with 422). |
| TI-02 | **Low**: consistency / defence in depth (both bodies use qualified names; `pg_temp` is never searched for functions or operators) | Trigger functions | `0067_consents_breach.py:159`, `:172` | `sis.tg_consents_append_only()` and `sis.tg_consent_purposes_frozen()` were the only functions in the tenant and platform schemas without a pinned `search_path` (every other trigger, guard and definer function has `search_path = pg_catalog, pg_temp`). Nothing tested this for non-definer functions. | Fixed in `fb0d3b3d` (same migration) and pinned by `b0b42ff6`: `tests/security/test_rls_catalog.py::test_TI_02_SEC_001_every_plpgsql_or_sql_function_pins_search_path` (reviewed exceptions: `core.current_tenant()` and `core.current_user_id()`, one-line SQL functions that RLS inlines). |
| TI-03 | **Low**: test gap, not exploitable today (the live catalog is clean) | RLS catalog test | `tests/security/test_rls_catalog.py` (`catalog_violations`) | The catalog test only checked that a policy **named** `tenant_isolation` exists. Permissive policies are OR-ed, so a future `CREATE POLICY x ON t FOR SELECT USING (true)`, or a `tenant_isolation` with a weakened expression, a narrower command or a role list, would have passed every security suite while opening the table to every school. | Fixed in `b0b42ff6`: `test_TI_03_SEC_001_permissive_policies_are_exactly_the_reviewed_ones` requires every permissive policy in the tenant schemas to be the exact standard `tenant_isolation` (FOR ALL, TO PUBLIC, `USING` and `WITH CHECK` = `tenant_id = core.current_tenant()`), the exact `definer_access`, or one of the five reviewed variants; `test_TI_03_policy_check_detects_a_widening_policy` is its self-test. Restrictive policies only narrow access and are not limited. |

| TI-04 | **Low**: defence in depth for a legal register (PRV-036, D58); needs an application bug | Triggers / privacy (breach register) | `migrations/versions/0075_breach_retention.py` (`breach_records_delete_expired`), column grant on `closed_at` (0067) | The 3-year delete floor trusts `ops.breach_records.closed_at`, which `sos_app` may update. An UPDATE that back-dated `closed_at` (on a closed breach, or when closing) made a recent breach deletable by the next nightly purge. | Fixed in `d7bc875a` (migration `0080_audit_ti_fixes`, lead decision): `BEFORE UPDATE` trigger `breach_records_closed_at`, every role, SQLSTATE 23514: `closed_at` never changes while the breach stays closed (`breach_records_closed_at_fixed`); closing must stamp the time of closing, between transaction start minus 5 s and `clock_timestamp()` plus 5 s (`breach_records_closed_at_now`); only leaving `closed` (the D49 reopen path) clears it. The service now writes `func.now()` in the UPDATE (`breach_repository.closing_time`), so the incident drill's pinned application clock no longer stamps a past close (drill docstring updated). Test: `tests/privacy/test_breach_retention.py::test_TI_04_D58_closed_at_cannot_be_back_dated_into_the_purge`. |
| TI-05 | **Low**: defence in depth; the service already refused it (ADR-0028, DL-09) | RLS (`core.users`) | `migrations/versions/0003_core_schema.py:382` (`users_in_tenant_update`) | The policy let a school update a person's `core.users` row through any membership row, including an open invitation (an invited person is not yet the school's member, DL-09) or a removal, even when the account belongs to another school too. | Fixed in `8fd74808` (migration `0080_audit_ti_fixes`; lead decision, ADR-0028 amendment of 2026-11-08): only through an `active` or `suspended` membership, or when this school's membership is the person's only one (`core.user_membership_count(id) = 1`; no new function). Paths checked: own and staff profile edits (active member), `record_login` (the school signed in to, active), the invite flows (definer functions; an existing account's invite never writes `core.users`, A-18), the offboarding clean-up `clear_sole_profiles` (sole memberships, any status). The TI-03 check now pins every reviewed variant's command and expression. Test: `tests/api/test_invitation_consent.py::test_TI_05_DL_09_an_open_invitation_does_not_let_the_school_edit_an_existing_account`. |

Schema changes, all in `0080_audit_ti_fixes`: the two consent trigger functions, the new trigger
`breach_records_closed_at` with its function, and the `users_in_tenant_update` policy. No API
schema change.

## Verified clean

- **RLS.** Every table with `tenant_id` in `core`, `sis`, `kb`, `audit`, `ops`, `fin`, `msg` has
  ENABLE + FORCE and the standard `tenant_isolation` expression (read from `pg_get_expr`, not
  only the name); the only other permissive policies are the reviewed variants and
  `definer_access` on exactly the 13 allowlisted tables. No unique index on a tenant table omits
  `tenant_id` (no cross-school existence oracle) except the global random `public_handles
  (kind, key_hash)` and `attrdef_key` (which coalesces `tenant_id`).
- **Restrictive delete policies (0072, 0075).** `outbox_delete_dispatched`,
  `breach_records_delete_expired`, `breach_reminders_delete_expired`,
  `breach_affected_delete_open_or_expired` and `purge_expired_only` are `AS RESTRICTIVE FOR
  DELETE TO sos_app`; `sos_app` cannot set `ops.outbox.dispatched_at` (only `sos_definer` has
  that column), so it cannot make an undelivered event deletable.
- **Offboarding role.** `sos_app` is a member of `sos_purger` WITH INHERIT FALSE, SET TRUE (gains
  no privilege without `SET LOCAL ROLE`). All 118 tables `sos_purger` can touch carry the
  restrictive `offboarding_purge` policy (or the audit variant); `core.tenant_purge_allowed()`
  requires `current_user = 'sos_purger'`, the transaction-local flag and status `offboarding`.
  `purge_role` uses `SET LOCAL` and `RESET ROLE`.
- **Grants (0060-0079).** No app role has TRUNCATE, TRIGGER or REFERENCES anywhere; append-only
  tables in `fin`, `ops` (privacy, portal reminders, disclosure snapshots) and `sis`
  (certificates, consents) give `sos_app` INSERT/SELECT plus column-level UPDATE of state
  columns only, each behind a `BEFORE UPDATE/DELETE` trigger. `ops.tally_bills` (0077): RLS,
  composite FKs, `sos_app` SELECT/INSERT/DELETE only, purge policy and grants. Default
  privileges give app roles nothing in `platform` or `audit` beyond the reviewed grants.
- **Cascades.** Every `ON DELETE CASCADE` / `SET NULL` into an append-only child was checked:
  apart from TI-01, the guarded children (`core.public_handles`, `msg.messages`) already allow
  `guardian_id`/`public_handle_id` to become NULL, and `ops.exports`/`ops.parent_notices` guards
  do not cover the nulled column. All FKs are composite, so no cascade crosses schools.
- **Audit chain.** `audit.events`: append guard, UPDATE/DELETE/TRUNCATE blocked, no grants on
  partitions (a partition TRUNCATE would skip the parent's statement trigger, but nobody but the
  owner may truncate). `audit.chain_heads` advance guard. `platform.tenant_audit_outbox`
  (0074, 0079): DELETE only of delivered rows, `delivered_at` set once, copy immutable, for every
  role; the D68 purge confirms `platform_event_id` in the school chain in the school's own
  `tenant_session` before deleting.
- **SECURITY DEFINER.** The 18 definer functions are exactly the allowlist, owned by
  `sos_definer` (NOBYPASSRLS), `search_path = pg_catalog, pg_temp`, EXECUTE only for their
  listed role. 0078's new status condition reads only `core.membership_roles`/`core.roles`, which
  already carry `definer_access`; the grant re-application keeps PUBLIC revoked.
- **`tenant_session()`.** Context set with bound `set_config(..., true)` (transaction-local) at
  the start of every transaction (and in `after_begin` for `deferred=True`), so a pooled
  connection never carries a tenant; no session-level `SET`/`set_config(..., false)`, no
  session-level advisory locks, no `Session(`/`engine.connect()` outside `core.db` except
  migrator tooling and the health check. Background jobs (outbox, breach purge, tenant audit,
  usage, heartbeat apply) open `tenant_session(tenant_id)` before any tenant query.
- **Raw SQL.** No SQL built from input: `text()` statements are constants with bound
  parameters; `SET CONSTRAINTS` is composed with `psycopg.sql.Identifier` after a regex check;
  the only f-string SQL is in `devtools/restore_drill.py` (catalog names).
- **Platform boundary.** `sos_platform` has no privilege on any tenant table and no USAGE on
  `fin`/`msg`; `sos_app` reads only `platform.feature_flags`. D78 `tenancy.tenant_board_codes`
  returns board codes only from the school's own session; D79 `heartbeat_apply` writes flags as
  `sos_platform` in the host's `platform.feature_flags`, the AI allowance through
  `tenancy.set_ai_answer_allowance`, and the billing summary to Valkey (no tenant table).

## Hardening notes

The two hardening notes of the first version of this report were fixed on the lead's decision
(owner: every finding fixed) as TI-04 and TI-05 above.

## Outside my area

None found.

## Checks run

- `pytest tests/privacy/test_consents_api.py tests/security/test_rls_catalog.py`: 31 passed
  (TI-01 failed before the migration with 422 `consents_append_only`).
- `pytest tests/extraction/test_migration.py tests/tenancy/test_offboarding_purge.py
  tests/students -k "migration or purge or guardian"`: 43 passed (includes the full migration
  up/down walk with `0080_audit_ti_fixes` and the offboarding purge).
- TI-04 and TI-05 tests failed before the migration change (no refusal; 1 row updated).
- After TI-04/TI-05: breach suites, consents, `tests/identity`,
  `tests/api/test_accept_invitation_suspended.py`, `tests/api/test_invitation_consent.py`,
  `test_rls_catalog`, `test_definer_functions`, `tests/extraction/test_migration.py`: 403 passed;
  `tests/privacy`: 196 passed; `tests/tenancy -k "offboard or purge or user"`: 18 passed;
  `tests/tenancy/test_offboarding_purge.py`: 11 passed; `tests/platform -k "reopen or incident or
  invit"`: 83 passed, 1 skipped, and the incident drill (failed on the first TI-04 version
  because it pinned the breach clock a week back) passes after the service change.
- `ruff check` and `ruff format --check` on touched files, `mypy apps/api apps/worker evals`
  (no issues, 1242 files), `lint-imports` (52 kept, 0 broken).
