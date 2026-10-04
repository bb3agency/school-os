# Security audit 2026-10-04: data layer (tenant isolation and data protection)

Auditor 1 of 3 (wave 4). Branch `wip/sec-data-layer` from `claude/friendly-ptolemy-0tl3br`
(3d408fc). Scope: RLS and policies, grants, composite FKs, the SECURITY DEFINER allowlist, the
platform boundary, scoped repositories and BOLA, knowledge retrieval filters, conversation
memory and the answer cache, Aadhaar handling, PII in logs, audit integrity, retention and
deletion, exports.

## Method

- The live catalog was dumped after `alembic upgrade head` from a throwaway test database:
  policies with their expressions, table and column grants per role, FKs, functions with
  EXECUTE grantees and bodies, and triggers. Each was reviewed against invariants 1, 4, 5 and 7.
- Every finding below was reproduced with a test that failed before the fix.

### Checked and sound

- **RLS on every tenant table.** Every table in `core`, `sis`, `kb`, `audit` and `ops` with a
  `tenant_id` has ENABLE and FORCE RLS and the standard `tenant_isolation` policy (the policy
  expressions were read, not only their names). The only variants are the reviewed ones
  (`own_tenant`, `users_in_tenant`, `attrdef_read`).
- **Composite FKs.** Every FK between tenant tables is a composite `(tenant_id, x)` key.
- **Definer functions.** All of them pin `search_path`, and none is executable by PUBLIC.
- **Platform boundary.** `sos_platform` has no tenant privileges. The school's AI answer
  allowance cannot be changed through tenant settings (`extra="forbid"`, merged keys only), and
  the AI bundle read is pinned to the caller's `tenant_id`.
- **Knowledge.**
  - `acl_predicate` sits in SQL before ranking in every branch.
  - Verified answers are matched only when every cited document has a visible chunk.
  - The answer cache requires the same access fingerprint and re-checks the visibility of every
    cited and retrieved source.
  - Conversations and memories are filtered by `user_id`.
  - Tools go through scoped services.
- **Exports.** Formula neutralisation and Aadhaar masking cover headers, values, titles and
  watermarks. Presigned keys are checked against the tenant and export prefix.
- **Audit tables.** They are append-only for `sos_app` (no grants on partitions, triggers
  including TRUNCATE). Cross-tenant chain writes are blocked by RLS WITH CHECK.
- **Error and log hygiene.** The 422 handler never echoes input, and logging uses a field
  allowlist with `redact()`.

## Findings

| ID | Severity | Area | File:line | Exploit path | Status | Test |
|---|---|---|---|---|---|---|
| DL-01 | **High**: breaks invariant 4 (a full Aadhaar number is stored and shown); any data-entry role, and a likely accidental path (pasting from a PDF or a sheet) | Aadhaar | `app/core/redaction.py:127` (`_SEP`), `:320` (`contains_full_aadhaar`); `app/students/definitions.py:232-237` | See note DL-01 below. | Fixed in `1e58b01`. The separator now accepts any Unicode whitespace or zero-width gap of 1 to 3 characters, every dash and minus variant, dot, slash, comma and underscore. `contains_full_aadhaar` also checks the whitespace-collapsed text, so no width of gap gets through. | `tests/core/test_redaction.py::test_PRV_015_any_gap_that_collapses_or_reads_as_a_separator_is_detected`, `::test_PRV_015_input_check_refuses_what_whitespace_collapsing_would_store`; `tests/students/test_definitions.py::test_FR_STU_012_full_aadhaar_with_any_gap_is_rejected_not_stored` |
| DL-02 | **Medium**: restricted (C3) data reaches staff without `student.read_sensitive`; insider within the same school | Documents / imports | `app/documents/service.py:1000` (`get_download_url`) | See note DL-02 below. | Fixed in `b7ffbf4`. A raw import file now downloads only like a C3 document: the caller needs `student.read_sensitive` or must be the uploader (`_raw_restricted`). | `tests/documents/test_documents_api.py::test_SEC_015_raw_import_files_download_only_like_restricted_files` |
| DL-03 | **Medium**: breaks invariant 4 (full Aadhaar stored and echoed); needs `import.commit` | Students / extraction | `app/students/service.py:1120`, `:1228`; `app/extraction/api.py:109` | `POST /extraction-items/{id}/confirm` with `roll_no` set to a Verhoeff-valid 12-digit number. The route has no Aadhaar body guard, and `_enrol` never checked `roll_no`. The number was stored in `sis.enrollments.roll_no` and shown on rosters, timelines and exports. | Fixed in `4b140c0`. `_enrol` (every enrolment path) and `update_enrollment` now refuse it. | `tests/extraction/test_api.py::test_SEC_013_confirm_refuses_a_full_aadhaar_roll_number` |
| DL-04 | **Low**: availability and read of another person's upload in the same school, no cross-tenant reach | Academics / documents | `app/academics/service.py:375` (`_consume_sheet`) | See note DL-04 below. | Fixed in `3b5c6bf`. The preview now answers 404 unless the caller uploaded the file (`documents.is_own_upload`). | `tests/academics/test_service.py::test_SEC_015_only_the_uploader_may_read_and_delete_a_sheet` |
| DL-05 | **Low**: defence in depth; only for a school already in `offboarding` | DB roles | `core.tenant_purge_allowed()` / `core.tenant_audit_purge_allowed()` (0032) | See note DL-05 below. | Fixed in `ccd9e46`, migration `0044_purge_flag_role`. Both functions also require `current_user = 'sos_purger'`; the downgrade restores the 0032 bodies. | `tests/tenancy/test_offboarding_purge.py::test_ADR_0029_the_purge_flag_lets_only_sos_purger_past_the_row_guards`; heads pinned in `tests/extraction/test_migration.py` |
| DL-06 | **Medium**: business-rule bypass of the certificate block. FR-CERT-002 promises that blockers are cleared only by a change request or a step-up waiver | dq / certificates | `app/dq/service.py:887` (`resolve_finding`), `app/certificates/service.py:928` | See note DL-06 below. | **Reported, needs decision.** FR-DQ-020 (resolve with a note) and FR-CERT-002 contradict each other. | none (decision first) |
| DL-07 | **Medium-low**: a promise mismatch, not an escalation with today's roles | Admin export | `app/admin/service.py:608`, `app/documents/service.py:2297` | See note DL-07 below. | **Reported, needs decision.** Recommendation: skip C3 document files, or list them as withheld, when `include_sensitive` is false. | none |
| DL-08 | **Low**: a small content leak to task holders | Circulars / tasks | `app/circulars/service.py:761`, `:842` | A task created from a circular suggestion defaults its title and details to the AI summary of that circular. The task owner, and holders of `task.read_all`, see the text even when they cannot see the circular; only the citation is hidden. | **Reported, needs decision** (product: whether a task copied from a summary keeps the summary text). | none |
| DL-09 | **Medium-low**: needs the victim's OIDC `sub` (random UUID) | Identity (api-auth area) | `app/identity/service.py:515`; `core.create_user_for_invite`, `core.accept_invitations` | See note DL-09 below. | **Reported to the api-auth area** (not fixed here). | none |
| DL-10 | **Medium**: the approver is misled about the reach of a grant | Break-glass (api-auth area) | `app/platform/schemas.py:1012`, `app/breakglass/service.py:120-133`, `apps/web/src/features/break-glass/BreakGlassScreens.tsx:59-82` | See note DL-10 below. | **Reported to the api-auth area.** Recommendation: restrict the scope keys to `section_id`/`class_id`, or render the scope as "whole school". | none |

| W3-07 | **Low**: privacy (PRV-016). Bytes that must go within a day stay 90 days; no new reader | Documents storage | `app/documents/storage.py` (`discard`, `purge_prefix`) | Reported by the web-ai-infra auditor and verified here. The files bucket is versioned. `discard()` tagged only the current version of the key, so an earlier version of the same key kept the 90-day noncurrent window instead of `discarded-1d`. Example: an Aadhaar image re-posted with the same presigned POST. `purge_prefix()` listed only current objects, so it also missed keys left with only noncurrent versions (e.g. after a person's delete). | Fixed in `670926e`. Both now list every version (`list_object_versions`), tag each version, then delete the current object. The api/worker and dedicated-host IAM roles gain `s3:ListBucketVersions` and `s3:PutObjectVersionTagging` (terraform not validated locally: terraform is not installed). | `tests/documents/test_storage_s3.py::test_PRV_016_discard_tags_every_stored_version_of_the_key`, `::test_PRV_016_purge_prefix_tags_noncurrent_versions_under_the_prefix`. Both run against SeaweedFS with bucket versioning enabled, which models versions correctly. |

### Notes on findings

**DL-01: full Aadhaar stored through whitespace.**
- **Cause:** the Aadhaar input check ran on the raw text. `clean_text` then collapsed every
  whitespace run to one space, and that cleaned value was stored.
- **Exploit:** a Verhoeff-valid number whose groups are separated by any of these passed the
  check:
  - a tab or a line break;
  - three or more spaces;
  - an em space, thin space or narrow no-break space;
  - a zero-width space, dot, slash, em dash or minus sign.

  It was then stored as `1234 5678 9012` in `sis.attribute_values` (also through the
  `find_full_aadhaar` route guard and the imports path) and shown on the profile.
- **Same gap in masking:** the same separators also escaped `mask_aadhaar` and `redact` in OCR
  text, logs, prompts and exports.

**DL-02: raw import file download.**
- **Who:** a school-wide `document.read` holder without `student.read_sensitive` (accountant,
  `auditor_readonly`, `exam_coordinator`, other office staff).
- **Exploit:**
  1. They list documents and see an `import_file` (C2, empty ACL).
  2. They call `GET /documents/{id}/download-url`.
  3. They receive the raw spreadsheet with its restricted (C3) columns: religion, caste and
     the Aadhaar-as-printed name, DOB and gender.
- **Why it matters:** the import's own sheet and export hide those columns (FR-IMP-008/009),
  and the documents sheet view refuses import files for exactly this reason.

**DL-04: sheet preview deletes another person's upload.**
- **What the preview does:** attendance and marks sheet previews read an `import_file` and
  delete it straight away.
- **Exploit:** the preview accepted any `import_file` the caller could see. A class teacher, or
  a school-wide reader, could therefore pass someone else's upload and destroy it. That upload
  might be an import file waiting to become a batch. The caller needed no delete rights, and
  could also read the sheet's problems.

**DL-05: purge flag usable by `sos_app`.**
- **Cause:** both purge functions checked only the tenant, the status and the setting
  `app.purge_tenant`. Any role can set that setting.
- **Exploit:** `sos_app` holds DELETE on `sis.students` for import reverts. It could set the
  flag and delete students of an offboarding school past the
  `students_delete_only_by_import_revert` trigger, without switching to `sos_purger`.

**DL-06: blocker resolved with a note, then certificate issued.**
1. `office_staff` (holds `dq.findings.resolve` and `certificate.issue`; no step-up needed)
   resolves a **blocker** finding "with a note".
2. They immediately issue a certificate from the mismatched record.
3. `request_certificate` checks only `dq.open_blockers`. The finding reopens only on the next
   dq run.

- **Recommendation (pick one):**
  - resolving a blocker with a note needs `dq.findings.waive` and step-up;
  - certificates re-run the student's checks before issuing.

**DL-07: full export ignores `include_sensitive` for files.**
- **Cause:** the full tenant export puts every `ready` document version into the archive,
  including C3 evidence files, even when `include_sensitive` is false. Only record values are
  masked.
- **Why it is not an escalation today:** only the owner holds `tenant.export_all`, and the
  owner also has `student.read_sensitive`.

**DL-09: attach another school's user by OIDC subject.**
1. A school A admin invites with another school's user's `idp_subject`.
2. `create_user_for_invite` returns the existing global user without comparing name or email.
3. `users_in_tenant` RLS then shows that user's email and phone to school A while the
   membership is still only `invited`.
4. `accept_invitations` activates the invite with no consent step on the user's next sign-in.

- **Verification status:** found by code and catalog reading, not reproduced with a test, because
  it sits in the api-auth area.
- **Latent follow-up:** `accept_invitations` takes no issuer. This becomes exploitable when the
  `0027` contract step drops `UNIQUE (idp_subject)`.

**DL-10: break-glass scope looks narrow but grants the whole school.**
- **Cause:** `BreakGlassIn.scope` accepts any key (`student_id`, `batch_id`, ...), but only
  `section_id` and `class_id` narrow the grant. Every other key becomes a **school-wide**
  membership.
- **Exploit:** the approval screen shows such a scope as narrow ("Student: ..."). Emergency
  grants open without a school approver at all.

## Hardening notes (no exploit path today)

1. **Over-broad column UPDATE grants for `sos_app`.**
   - `core.memberships`: `id`, `tenant_id`, `user_id`, `created_by`.
   - `ops.break_glass_grants`: nearly every column.
   - `audit.chain_heads.tenant_id`.

   Narrow each to the workflow columns.
2. **`kb.llm_calls`.** `sos_app` holds DELETE, and no app path uses it. It is the metering
   ledger, so revoke it (the purger handles offboarding).
3. **Audit chain tail truncation.** An owner/DBA can delete the newest events and rewind
   `chain_heads` without detection, because `verify_chain` trusts the head in the same database.
   - Compare the head with the last KMS-signed archive manifest.
   - Verify chains of `provisioning` and `offboarding` schools too.
4. **Formula neutralisation (`core/spreadsheet.py:248`, `exports/tables.py:71`).** It checks
   only the first character. Check after `lstrip()`, and NFKC-fold full-width `＝＋－＠`.
   `platform/audit_view.py:73` should use `safe_cell`.
5. **Idempotency replays (`authz/http.py:200`).** These cache whole response bodies in Valkey
   for the TTL, including decrypted behaviour-note text. The cached text survives an erase.
   - Cache only status and IDs for routes that return C3 text.
   - The docstring in `ops/idempotency.py` is wrong.
6. **Crypto-shredding window.** The wrapped DEKs in PITR and cross-region backups stay
   decryptable under the shared CMK until those backups expire. Document the window, or move to
   per-tenant CMKs.
7. **Discard of derived page images.** Discard removes only `original.*`. When page renders
   (`v<n>/derived/`) get callers, discard must clear them too (PRV-016).
8. **Enrolment target scope.** `students.enrol` does not check `section_id` against a scoped
   `student.update_nonidentity` grant. This is latent: every system role holds it school-wide.
9. **Accepted risks to record.** A Verhoeff-valid 12-digit APAAR ID is accepted (ADR-0037). An
   Aadhaar number starting `91[6-9]` typed as `+91 …` passes as a phone number.
10. **Extraction review queue.** It lists extracted rows (C2) to every `import.run` holder,
    whatever the ACL of the source register scan.

## Migration

`0044_purge_flag_role` (after `0043_plan_row_version`):
- Backward compatible: it changes only the two function bodies, with the same signature,
  grants and comments.
- The downgrade restores the 0032 bodies.
- The head is pinned in `tests/extraction/test_migration.py`.
- CLAUDE.md §4 still names `0043_plan_row_version` as the last revision; the owner should update
  it.
