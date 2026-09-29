# ADR-0029: Deleting a school's data at offboarding (purge role, crypto-shredding, certificate)

| Field | Value |
|---|---|
| Status | Proposed |
| Date | 2026-09-29 |
| Deciders | Product owner (security review required: new database role) |
| Amends / supersedes | Would amend [ADR-0013](ADR-0013-cross-tenant-access-and-platform-privilege-separation.md) (role set §1, A1/A2 grants) and [ADR-0020](ADR-0020-control-plane-boundaries-and-guaranteed-audit-copies.md) (the lifecycle calls `platform` may make into `tenancy.service`) |

## Context

FR-PLT-005 / US-1303 AC3: after two operators approve offboarding, SchoolOS MUST delete the
school's data within 30 days, destroy its keys (crypto-shredding) and record a certificate of
deletion. The sequence is fixed by docs/16 §5.5, 08 §7, 11 R8: status `offboarding` → the school
confirms it has its export (or we deliver it, R8) → access disabled → deletion job → wrapped keys
destroyed (dedicated: host KMS key scheduled for deletion, host destroyed) → certificate →
status `deleted`. Invoices and the billing account stay in `platform` (08 §14). The two-person
request/approval is built (SEC-029); the rest is not.

Facts checked on 2026-09-29 against a database migrated to `0030_import_cell_edits`:

1. **The control plane cannot delete school data, by design.** `sos_platform` has no privileges on
   `core`, `sis`, `kb`, `audit`, `ops` (ADR-0013, invariant 1), and cross-tenant access exists only
   through the pinned `SECURITY DEFINER` allowlist. The deletion must therefore run on the
   school side, in the school's own `tenant_session`, and the control plane may only start it
   through `tenancy.service` (ADR-0020).
2. **`sos_app` cannot delete every tenant table either, also by design.** It has no `DELETE` on
   `core.tenant_keys` ("crypto-shredding is an offboarding action", ADR-0013 A2),
   `sis.change_requests` (FR-CR-001), `sis.dq_runs`, `sis.dq_findings`, `sis.extraction_batches`,
   `sis.extraction_pages`, `sis.extraction_items`, `sis.promotion_runs`, `sis.promotion_items`,
   `sis.attribute_values`, `sis.import_cell_edits`, `ops.exports`, `ops.export_files`
   (FR-EXP-003), `ops.break_glass_grants`, `ops.outbox`, `core.users`, `core.tenants`, and only
   `SELECT, INSERT` on `audit.events`. These absences are **pinned by tests** that expect
   `permission denied` or `has_table_privilege('sos_app', …, 'DELETE') = false`:
   `tests/tenancy/test_schema.py` (`DENIED_FOR_APP`), `tests/changes/test_schema.py`,
   `tests/exports/test_migration.py`, `tests/extraction/test_migration.py`,
   `tests/extraction/test_pipeline.py`, `tests/dq/test_migration.py`,
   `tests/breakglass/test_breakglass.py`. Granting `sos_app` `DELETE` would weaken all of them.
3. Some of those rows go with a parent the app may delete, because the referential action runs as
   the table owner: `attribute_values`, `student_profiles` and `dq_findings` cascade from
   `sis.students`; `export_files` from `ops.exports`; `import_rows`/`import_cell_edits` from
   `sis.import_batches`; `promotion_items` from `sis.promotion_runs`; `document_versions`,
   `document_acl`, `document_chunks` from `kb.documents`. The others (`change_requests`,
   `dq_runs`, `extraction_*`, `promotion_runs`, `exports`, `break_glass_grants`, `outbox`,
   `tenant_keys`) have no cascading parent the app can delete.
4. `sis.students` has the trigger `students_delete_only_by_import_revert`: the app may delete a
   student only while reverting the import that created it (FR-IMP-005).
5. `sis.attribute_values` and `sis.change_requests` reference each other with `NO ACTION` keys
   (`attribute_values_change_request_fk`, `change_requests_old_value_fk`,
   `change_requests_applied_value_fk`), so they can only be removed in one transaction with one
   of those keys deferred.
6. There is a precedent for a role reachable only by `SET ROLE`: `sos_migrator` is a member of
   `sos_owner` and `sos_definer` `WITH INHERIT FALSE, SET TRUE` (ADR-0013 A1): no implicit
   privileges, `has_table_privilege` stays false, the role applies only after an explicit
   `SET ROLE`.
7. Adding a database role needs an ADR (docs/adr/README.md "When to write an ADR").

A spike on the scratch database confirmed the mechanism below: with `sos_purger` granted to
`sos_app` `WITH INHERIT FALSE, SET TRUE`, `has_table_privilege('sos_app','sis.dq_runs','DELETE')`
stays `false`; a `DELETE` as `sos_app` still fails with `permission denied`; after
`SET LOCAL ROLE sos_purger` a `DELETE` removes nothing for an `active` school or without the
purge flag, removes only the current school's rows when the school is `offboarding` and the flag
names it, and nothing of another school (the flag and tenant context must match).

## Decision (proposed)

### 1. Where the deletion runs

- The deletion job MUST run in the worker as `sos_app` inside `tenant_session(tenant_id)` of the
  school being deleted, one school per transaction, never as `sos_platform`, never with
  `BYPASSRLS`, and with no new `SECURITY DEFINER` function or `definer_access` policy.
- The control plane (`app.platform`) starts each step through new **lifecycle** functions of
  `app.tenancy.service` (ADR-0020 "offboard" family): `tenant_data_inventory(tenant_id)` (counts
  per category), `purge_tenant_data(tenant_id)`, `verify_tenant_purged(tenant_id)` (remaining
  counts) and `destroy_tenant_keys(tenant_id)`. They return counts and codes only. The four names
  are added to `TENANCY_ALLOWED` in `tests/platform/test_boundaries.py` with this ADR as the
  reference; `platform` gets no new tenant-side import and opens no `tenant_session`.
- Each tenant module owns the deletion of its own tables through an additive public
  `purge_tenant_data(session) -> dict[str, int]` in its `service.py` (children before parents),
  registered with `tenancy` at import time. `tenancy` runs them in the fixed order of a versioned
  file `app/tenancy/offboarding.yaml` and **fails closed** when any listed owner is not
  registered. Order: knowledge, extraction, dq, changes, students, imports, documents, exports,
  notifications, breakglass, ops, identity, tenancy.

### 2. New role `sos_purger` for the protected tables

- `infra/db/bootstrap.sql` creates `sos_purger NOLOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB
  NOCREATEROLE NOREPLICATION` and grants it to `sos_app` **`WITH INHERIT FALSE, SET TRUE`**.
  `sos_app` therefore keeps exactly its current privileges (every pinned test above still
  passes unchanged) and gains only the ability to `SET LOCAL ROLE sos_purger` inside a
  transaction.
- Migration `0032_offboarding` grants `sos_purger` `SELECT, DELETE` on the tenant tables the purge
  deletes (and `EXECUTE` on `core.current_tenant()` and a new plain, non-definer function
  `core.tenant_purge_allowed()`), never on `audit.*`, `core.tenants`, `core.users` or
  `core.permissions`, and adds to each of those tables a **restrictive** policy
  `offboarding_purge ... TO sos_purger USING (core.tenant_purge_allowed())`. The standard
  `tenant_isolation` policy still applies to `sos_purger` (it is not the owner; `FORCE` RLS).
- `core.tenant_purge_allowed()` (`LANGUAGE sql STABLE`, `SECURITY INVOKER`, `search_path`
  pinned) is true only when the tenant context is set, the transaction-local flag
  `app.purge_tenant` equals it, and the current school's `core.tenants.status` is `offboarding`.
  The status only changes through `core.set_tenant_status` after the two-person approval, so the
  database itself refuses a purge of a school that two operators did not approve, even to code
  that sets the flag and the role.
- The purge transaction: `set_config('app.purge_tenant', :t, true)`, `SET CONSTRAINTS
  sis.attribute_values_change_request_fk DEFERRED` (the migration makes that key `DEFERRABLE
  INITIALLY IMMEDIATE`; normal writes are unchanged), `SET LOCAL ROLE sos_purger`, the module
  deletes, `RESET ROLE`, then `audit.record('tenant.data_purged', counts)` as `sos_app` in the
  same transaction. One transaction for all rows: either every row goes or none does (fail
  closed; a retry starts from the same state).
- `students_delete_only_by_import_revert` additionally allows a delete when
  `core.tenant_purge_allowed()` is true (the function is replaced; downgrade restores it).
- Identity data outside the school's tables: before the memberships go, the identity module
  clears the profile (`display_name` to a neutral placeholder, `email`, `phone_ciphertext`,
  `last_login_at`) of each person whose **only** membership is this school
  (`core.user_membership_count`, ADR-0028), using the update `sos_app` already has. A person
  who also works at another school keeps the profile (ADR-0028). The `core.users` row and its
  opaque OIDC subject stay (no `DELETE` on `core.users`; see open question 3). The school's
  `core.tenants.settings` is reset to `{}`; the row stays with status `deleted` (code and
  registered name are business records).

### 3. What is retained, and why

| Data | Retained? | Reason |
|---|---|---|
| `audit.events`, `audit.chain_heads` of the school | **Yes**, until the normal audit retention removes them with their monthly partitions | Append-only by DB grant and trigger (invariant 7); summaries hold IDs, codes and counts only (no personal data by validation); DPDP ≥ 1 year and CERT-In 180 days of logs (08 §6–7, 05 §13); the signed daily archives are under S3 Object Lock and cannot be deleted anyway. Listed as "retained" on the certificate. See open question 1 |
| `core.tenants` row (status `deleted`, settings `{}`) | Yes | Status, code and registered name; needed so the code is never reused and the certificate refers to it |
| `platform.*` (invoices, billing account, subscription, deployment, platform audit) | Yes | Business records, 8 years (08 §14) |
| Everything else in `core`, `sis`, `kb`, `ops` | **No** | Deleted by the purge; verified by a catalog-driven count of every table with `tenant_id` |

### 4. Files

- The documents module deletes every object under `t/<tenant_id>/` (list, tag
  `sos-lifecycle=discarded`, delete; idempotent and resumable), so the versioned bucket expires
  the remaining noncurrent versions after one day (`discarded-1d`) instead of the 90-day recovery
  window. Verification lists the prefix again (zero objects). Versions deleted **before**
  offboarding keep their 90-day expiry: see open question 2.

### 5. Crypto-shredding

- After the rows and objects are verified gone, `destroy_tenant_keys` deletes every
  `core.tenant_keys` row of the school (as `sos_purger`, same guard) and drops the in-process
  key cache for it; the tenant chain gets `tenant.keys_destroyed` (versions and key id, never
  key material). Verification: zero key rows, and decrypting a value written before fails.
- **Dedicated tier:** the control plane cannot reach the host. The run records two external
  actions, "host KMS key scheduled for deletion" and "host destroyed" (Terraform, docs/16 §13.4),
  which an operator with `platform.tenants.offboard` confirms with a short reference; no AWS
  call is made from the app.

### 6. Control plane: run, deadline, certificate

- `platform.offboarding_runs` (one row per school): state `awaiting_export` → `scheduled`
  (export confirmed by an operator: `school_confirmed` or `delivered_by_us`, with a reference)
  → `deleting` → `keys_destroyed` → `completed`; `deadline_at` = approval + 30 days; the
  inventory counts (taken once, before deleting, so a crash does not lose them), remaining
  counts, object counts, `failed_step` and an error **code**, attempts and a lease (the
  provisioning pattern, ADR-0024). Codes, counts and IDs only.
- Beat task `offboarding.process` (queue `maintenance`, every 10 minutes, shared mode only)
  advances every scheduled run under its lease; a crash leaves the lease to expire and the next
  run resumes from the recorded state; each step is idempotent. `offboarding.certify` (queue
  `pdf`) issues certificates. A run not `completed` 7 days before its deadline logs
  `platform.offboarding.due_soon`; after it, `platform.offboarding.overdue` (alert, docs/16 §17)
  and records `tenant.deletion_overdue` once.
- `platform.deletion_certificates` (append-only, like `platform.invoice_pdfs`): tenant id, code,
  school name as registered, tier, requesting/approving/export-confirming operator ids, request,
  approval, deletion start/end and key-destruction dates, counts per category, retained
  categories, object key, SHA-256 of the canonical content and of the PDF, template version.
  No student data, no personal data of school staff.
- The PDF is rendered by `app.core.pdf` on the `pdf` queue, bilingual labels (English and
  Telugu; the recipient is the school's management), stored under the control-plane prefix
  `platform/deletion-certificates/` (never under `t/`; the Terraform policy of the invoice prefix
  gains this prefix), and downloaded through a presigned GET (≤ 5 minutes) by operators with
  `platform.tenants.read`, audited as `tenant.deletion_certificate_downloaded`.
- The same platform transaction that stores the certificate sets the school `deleted`
  (`core.set_tenant_status`, `offboarding→deleted` is already allowed) and queues the school-chain
  copy (ADR-0020).
- Platform audit events: `tenant.export_confirmed`, `tenant.deletion_started`,
  `tenant.data_deleted`, `tenant.keys_destroyed`, `tenant.teardown_confirmed` (dedicated),
  `tenant.deletion_certified`, `tenant.deleted`, `tenant.deletion_overdue`,
  `tenant.deletion_certificate_downloaded`.
- Routes (all `require_platform`): `GET /platform/tenants/{id}/offboarding`
  (`platform.tenants.read`), `POST …/offboarding:confirm-export` and
  `POST …/offboarding:confirm-teardown` (`platform.tenants.offboard` ᴿ),
  `GET …/deletion-certificate/download-url` (`platform.tenants.read`).

## Consequences

- Good: no `BYPASSRLS`, no new definer function, no `definer_access` widening, no new privilege
  for `sos_platform`; every existing narrowing of `sos_app` stays pinned. The destructive
  privilege is held by a role that nothing uses implicitly, that RLS still scopes to one school,
  and that the database refuses to use for a school two operators did not approve.
- Good: new tenant tables are covered automatically: the verification counts every catalog table
  with `tenant_id`, so keys are never destroyed while an unregistered table still holds rows.
- Bad / costs: one more database role; `infra/db/bootstrap.sql` must be re-run in every
  environment (staging, prod, each dedicated host) **before** `0032` (the migration fails with a
  clear error if the role is missing). `sos_app` can now become `sos_purger`, so a compromised
  API process could attempt a purge, but only of a school already in `offboarding` (two-person
  approved), and it would be visible in the audit chain.
- Follow-up: ADR-0013 status line "Amended by ADR-0029"; docs 05 §3 (roles, grants, policies),
  07 §8 (crypto-shredding), 08 §7 (retention at offboarding), 10 (bootstrap order), 16 §5.5, §7,
  §8, §16, §17; 14 status row FR-PLT-005.

## Alternatives considered

| Option | Why not chosen |
|---|---|
| Grant `DELETE` on the protected tables to `sos_app`, guarded by a restrictive policy | Weakens pinned controls (FR-CR-001, FR-EXP-003, ADR-0013 A2) and seven existing tests; the app role could delete append-only records at any time the guard is satisfied without an explicit role switch |
| New `SECURITY DEFINER` function(s) owned by `sos_definer` that delete per table | Needs `definer_access` (a policy that sees **every** school) plus `DELETE` for `sos_definer` on about 15 tables, so a bug deletes other schools' data; widens the allowlist far beyond anything today |
| Deletion by cascading from `core.tenants` | Changes every tenant foreign key to `ON DELETE CASCADE` (import revert and other `NO ACTION` backstops would silently cascade) and needs `DELETE` on `core.tenants` (definer) |
| Run the purge as the migrator/owner (operator command) | A superuser-like path outside the audited worker; RLS does not apply to the owner's cascades and FORCE RLS alone would not scope a manual command; no per-school guard |
| Crypto-shred only (leave rows, destroy keys) | Only C3 fields are encrypted with the school key; names, admission numbers, documents and change requests are plaintext or SSE with the bucket key |

## Open questions for the product owner

1. **School audit chain at offboarding.** Proposed: keep it until the normal audit retention
   (IDs, codes and counts only; DPDP/CERT-In log retention; Object Lock archives cannot be deleted
   before 3 years anyway) and name it as retained on the certificate. Alternative: delete the
   school's rows early, which needs an exception to the append-only triggers (invariant 7).
2. **Old object versions.** The files bucket keeps noncurrent versions for 90 days. Objects the
   school deleted in the 90 days before offboarding keep that expiry. Removing them within 30
   days needs `s3:ListBucketVersions` and `s3:DeleteObjectVersion` on `t/*` for the worker role
   (Terraform `shared_platform`), or a lifecycle rule change. Which one?
3. **Staff identities.** Profiles used only by this school are cleared, but the `core.users` row
   (opaque OIDC subject) and the account in the staff user pool (Cognito) remain. Delete the
   Cognito accounts as a runbook step (R8)? Delete the rows (needs a definer function, another
   ADR)?
4. **Backups in the shared tier.** The school's DEK is wrapped by the shared CMK, and database
   backups (PITR ≥ 14 days, snapshots) still contain the wrapped DEK, so until they age out a
   restored backup is readable. "Crypto-shredding makes backups unreadable" (08 §7) holds only
   for dedicated hosts (own CMK). Accept "backups age out on their normal schedule" (08 §7) and
   state the maximum age on the certificate, or add per-school KMS deny rules (encryption
   context) as a Terraform action?
5. **Export gate.** The full data export (FR-ADM-001) is not built. Proposed: an operator records
   `school_confirmed` or `delivered_by_us` with a reference before deletion may start; the
   deadline alert fires if that has not happened in time. Deletion never starts without it.
6. **Certificate language.** Proposed: English and Telugu labels; Telugu wording needs review.
7. Status `deleting`: not added. The school stays `offboarding` until the certificate exists;
   progress is on the run. Confirm.

## Related requirements

FR-PLT-005, US-1303 AC3, SEC-029, SEC-001/002, SEC-012, SEC-026, PRV (08 §7, §9 item 8, §14),
docs/16 §5.5, 05 §3 and §13, 07 §8, 11 R8.
