# Security audit 2026-10-06: every API route (api-routes)

Branch `wip/sec3-api-routes` from `claude/friendly-ptolemy-0tl3br` (b2ac233). Round three,
route by route, against the OWASP API Security Top 10 (2023) and ASVS V4/V5/V13. The round-one
(`audit-2026-10-04-*.md`) and round-two (`audit-2026-10-05-*.md`) findings were read first and
are not repeated. Paths are relative to `apps/api/`. Synthetic data only.

**Status: complete.** Every verified finding is fixed with a test that failed first, or is listed
under "Needs owner decision" with a recommendation. The route inventory is in the appendix.

## Method

- **Inventory:** 323 routes, generated from the live app (`app.routes`); the table is the
  appendix below.
- **Automated guards:** `tests/security/test_api_contract_guards.py` runs over every route:
  - `extra="forbid"`;
  - bounded strings, lists and maps;
  - `limit` at most 200;
  - UUID path parameters;
  - no secret or storage-named response fields;
  - Idempotency-Key on creating POSTs;
  - If-Match on versioned PUT/PATCH.

  Each exception is allowlisted with its reason.
- **Manual review:** six read-only reviewers, one per module group. Every candidate they raised
  is verified here before it is reported or fixed.

## Fixed (each with a test that failed first)

| ID | Severity | Ref | Fix | Commit | Test |
|---|---|---|---|---|---|
| R-02 | Low | API4 | Bounds on retention rule keys, export columns, insights rule keys (an unknown key of any length was echoed in the 422 field) and PlanPatch.features | ea0bf07 | `tests/security/test_api_contract_guards.py` |
| R-03 | Low (lost update; round-2 A-17) | API6, ASVS V11.1.4 | `PUT /users/{id}/roles` and `/scopes` take an optional If-Match. The membership is locked and its version bumped, so a stale list cannot restore a revoked role | 71b258e, 6f1b306 | `tests/api/test_users_roles_if_match.py` |
| R-04 | Low (round-1 AA-13 class) | API6 | Optional If-Match on price-override and ai-bundle PUT/DELETE and on flag PUTs (a stale form could re-enable a switched-off flag). GET /subscriptions/{id} now sends an ETag | e85a77e | `tests/platform/test_route_audit_concurrency.py` |
| R-05 | Low | API6, docs/09 §2 | Optional Idempotency-Key on platform break-glass requests, operator and school ticket replies, and knowledge memory items | e85a77e, 80c4e31, 57250d0 | same file; `tests/knowledge/test_memory_api.py::test_R_05_*` |
| R-06 | Medium-low | API1/API3 (BOPLA), SEC-015 | Import mapping: a column ever mapped to a C3 field stays restricted. Without `read_sensitive` it may only go to ignore or another C3 field (before: unmap it, then read or edit C3 values in the sheet) | 3365dc6, 57a819d | `tests/imports/test_sheet_editor.py::test_SEC_015_a_column_once_mapped_to_a_restricted_field_stays_hidden` |
| R-08 | Medium (invariant 4) | API10, invariant 4 | Tally agent text: Aadhaar-like numbers are masked, and control and bidi characters removed, from ledger, group and company names (before: stored and shown in clear) | a489477 | `tests/tally/test_agent_text.py` |
| R-09 | Low-medium (invariant 4) | invariant 4, PRV-015 | Document title, issuer and upload filename refuse a full Aadhaar number | e006850 | `tests/documents/test_title_aadhaar.py` |
| R-12 | Low | API8 | A NUL in any text gives 422 `invalid_characters` instead of a 500 (central handler). Control-plane free text (tickets, announcements, notes, flag description) refuses control characters | 9c0462d, 80c4e31 | `tests/core/test_errors.py`, `tests/platform/test_text_inputs.py` |
| R-07 | Medium-low | API5 (BFLA), SEC-003 | `PATCH /users/{id}` profile edits (email, display name) of another member follow the AA-02 status reach rule (403 `role_not_grantable`). Before, an office admin could change an owner's or principal's email. Own profile edits are unchanged | fbf7db2 | `tests/api/test_users_privileged_status.py::test_R_07_office_admin_cannot_edit_the_profile_of_an_owner_or_principal` |
| R-10 | Low (custom roles) | API1 (BOLA), SEC-015 | Enrolment PATCH and end check the enrolment's own section in its own year; before, reaching the student through the current enrolment reached every older one | f193b8d | `tests/students/test_enrollments_api.py::test_R_10_scoped_editor_cannot_change_an_enrolment_outside_scope` |
| R-11 | Low (custom roles) | API1 (BOLA), FR-TALLY-006 | `DELETE /tally/parties/{id}/links/{student_id}` needs a student the caller reads, as linking does (404 otherwise). Before, a `tally.configure` holder without `student.read_basic` could drop any student's ledger link and so hide their dues | 5885bfd | `tests/tally/test_api.py::test_R_11_unlinking_needs_the_student_to_be_visible` |
| R-13 | Low | API8, CWE-20, CWE-190 | 500s now clean 422s: PlanPatch refuses explicit nulls for NOT NULL fields; `included_students` and plan limits bounded to int4; invoice period start and trial end in the years 2000–2999 (a 9999 date overflowed the period maths); a computed amount past Numeric(14, 2) maps to 422 `value_out_of_range` (platform translator, plus a central safety net for SQLSTATE 22003/22008); a concurrent duplicate exam create answers 422 `exam_name_taken` (savepoint) | 70e37c6 | `tests/platform/test_billing.py::test_R_13_*`, `tests/core/test_errors.py::test_R_13_*`, `tests/academics/test_service.py::test_R_13_*` |
| R-16 | Low (latent, Telugu hidden) | API6, ASVS V11.1, ADR-0036 | Approving a notice while Telugu is hidden replaces the Telugu title and body with the English text. Before, Telugu text planted by a drafter (hidden from the approver by the API) was approved unseen and would reach parents once Telugu is switched on | 9234b19 | `tests/circulars/test_api.py::test_R_16_telugu_text_nobody_reviewed_is_not_kept_on_approval` |

## Needs owner decision (reported, no code)

| ID | Ref | Finding | Recommendation |
|---|---|---|---|
| R-14 | API4 | Operator lists of deployments (one per school) are unpaged; announcements and break-glass lists stop at 200 with `next_cursor: null`, so older rows are silently missing | Add cursor paging to the three lists (same `Page` shape as other platform lists) together with the operator UI change |
| R-15 | API6, ASVS V2.10 | Rotating a dedicated host's heartbeat key twice (double click, retry) drops the key the host is still using; the answer is a one-time secret, so idempotency cannot replay it | Keep the previous key valid for a short overlap (e.g. 24 h) until the host's first heartbeat with the new key, and refuse a second rotation while one is pending (409) |
| R-17 | API3, DPDP purpose limit | Every staff role can read every school support ticket (subjects and messages may name students) | Limit school ticket reading to the raiser plus `support.manage` holders (owner, principal, office admin) |
| R-18 | API6 | A school suspended for billing cannot also be given a security hold (the reverse of round 2 A-08), so paying the invoice could lift a block the operator wanted to keep | Allow a security hold on any non-offboarded school and keep it independent of billing state (lifted only by its own release) |
| R-19 | API4, CWE-400 | `GET /audit/verify` (tenant and platform) re-hashes the whole chain synchronously on every call, without limit | Run verification as a job (or verify only since the last verified checkpoint) and rate-limit the route |
| R-20 | LLM10, API4 | Notice drafts call the LLM with no per-user admission, so one user can spend the school's AI budget | Put notice drafting under the same per-user AI admission as "Ask the school" (rate-limit work, branch wip/rate-limits) |

## Hardening (no exploit path; not fixed here)

- Naive datetimes on the audit filters are read as UTC without saying so.
- A client `X-Request-Id` is stored in audit records (bounded and pattern-checked, but caller-chosen).
- Enrolment and academic-year dates are not bounded.
- DQ resolve accepts a change request in any state.
- Tally device-cap check races (two enrolments at once can exceed the cap by one).
- Bidi control characters are accepted in tenant free text.
- Exam names: the unique constraint is case-sensitive while the check is not, so two concurrent creates differing only in case both succeed (needs a `lower(name)` unique index, a migration).
- The web has no message for the new `value_out_of_range` code (it shows the generic problem detail).

## Appendix: route inventory

323 routes (method x path), generated from the live app (`app.routes`).

| Method | Path | Guard | Permission | Scope | Step-up | Idempotency-Key | If-Match |
|---|---|---|---|---|---|---|---|
| GET | `/api/v1/academic-years` | Requirement | student.read_basic |  |  |  |  |
| POST | `/api/v1/academic-years` | Requirement | tenant.structure.manage | school |  | yes |  |
| GET | `/api/v1/academic-years/{year_id}` | Requirement | student.read_basic |  |  |  |  |
| PATCH | `/api/v1/academic-years/{year_id}` | Requirement | tenant.structure.manage | school |  |  | yes |
| POST | `/api/v1/academic-years/{year_id}/archive` | Requirement | tenant.structure.manage | school |  |  | yes |
| POST | `/api/v1/academic-years/{year_id}/make-current` | Requirement | tenant.structure.manage | school |  |  | yes |
| GET | `/api/v1/academic-years/{year_id}/promotions` | Requirement | tenant.structure.manage | school |  |  |  |
| POST | `/api/v1/academic-years/{year_id}/promotions:commit` | Requirement | tenant.structure.manage | school |  | yes |  |
| POST | `/api/v1/academic-years/{year_id}/promotions:preview` | Requirement | tenant.structure.manage | school |  |  |  |
| POST | `/api/v1/academic-years/{year_id}/promotions:undo` | Requirement | tenant.structure.manage | school |  |  |  |
| POST | `/api/v1/academic-years/{year_id}/unarchive` | Requirement | tenant.structure.manage | school |  |  | yes |
| GET | `/api/v1/admin/retention` | Requirement | tenant.settings.manage | school |  |  |  |
| PUT | `/api/v1/admin/retention` | Requirement | tenant.settings.manage | school | yes |  | yes |
| GET | `/api/v1/admin/tenant-export` | Requirement | tenant.export_all | school |  |  |  |
| POST | `/api/v1/admin/tenant-export` | Requirement | tenant.export_all | school | yes | yes |  |
| GET | `/api/v1/admin/tenant-export/{tenant_export_id}` | Requirement | tenant.export_all | school |  |  |  |
| GET | `/api/v1/admin/tenant-export/{tenant_export_id}/download-url` | Requirement | tenant.export_all | school | yes |  |  |
| GET | `/api/v1/announcements` | Requirement | session.authenticated |  |  |  |  |
| GET | `/api/v1/attributes` | Requirement | student.read_basic |  |  |  |  |
| GET | `/api/v1/audit/events` | Requirement | audit.read |  |  |  |  |
| GET | `/api/v1/audit/export` | Requirement | audit.read |  |  |  |  |
| GET | `/api/v1/audit/verify` | Requirement | audit.read |  |  |  |  |
| POST | `/api/v1/behaviour-notes/{note_id}/erase` | Requirement | insights.manage | school | yes |  |  |
| POST | `/api/v1/breakglass/grants/{grant_id}/revoke` | Requirement | breakglass.approve |  | yes |  |  |
| GET | `/api/v1/breakglass/requests` | Requirement | breakglass.approve |  |  |  |  |
| GET | `/api/v1/breakglass/requests/{request_id}` | Requirement | breakglass.approve |  |  |  |  |
| POST | `/api/v1/breakglass/requests/{request_id}/approve` | Requirement | breakglass.approve |  | yes |  |  |
| POST | `/api/v1/breakglass/requests/{request_id}/deny` | Requirement | breakglass.approve |  | yes |  |  |
| POST | `/api/v1/breakglass/support-session` | Requirement | session.authenticated |  |  |  |  |
| GET | `/api/v1/certificates` | AnyOfRequirement | certificate.issue / certificate.approve |  |  |  |  |
| GET | `/api/v1/certificates/types` | AnyOfRequirement | certificate.issue / certificate.approve |  |  |  |  |
| GET | `/api/v1/certificates/{certificate_id}` | AnyOfRequirement | certificate.issue / certificate.approve |  |  |  |  |
| POST | `/api/v1/certificates/{certificate_id}/approve` | Requirement | certificate.approve |  | yes |  | yes |
| POST | `/api/v1/certificates/{certificate_id}/cancel` | Requirement | certificate.approve |  | yes |  | yes |
| GET | `/api/v1/certificates/{certificate_id}/download-url` | Requirement | certificate.read |  |  |  |  |
| POST | `/api/v1/certificates/{certificate_id}/duplicates` | Requirement | certificate.issue |  |  | yes |  |
| GET | `/api/v1/certificates/{certificate_id}/print` | AnyOfRequirement | certificate.issue / certificate.approve |  |  |  |  |
| POST | `/api/v1/certificates/{certificate_id}/reject` | Requirement | certificate.approve |  | yes |  | yes |
| POST | `/api/v1/certificates/{certificate_id}/render` | Requirement | certificate.issue |  |  |  |  |
| POST | `/api/v1/certificates/{certificate_id}/withdraw` | Requirement | certificate.issue |  |  |  | yes |
| GET | `/api/v1/change-requests` | AnyOfRequirement | student.identity_change.approve |  |  |  |  |
| POST | `/api/v1/change-requests` | Requirement | student.identity_change.request |  |  | yes |  |
| GET | `/api/v1/change-requests/{change_request_id}` | AnyOfRequirement | student.identity_change.approve |  |  |  |  |
| POST | `/api/v1/change-requests/{change_request_id}/approve` | Requirement | student.identity_change.approve |  | yes |  | yes |
| POST | `/api/v1/change-requests/{change_request_id}/cancel` | Requirement | student.identity_change.request |  |  |  | yes |
| GET | `/api/v1/change-requests/{change_request_id}/memo` | AnyOfRequirement | student.identity_change.approve |  |  |  |  |
| POST | `/api/v1/change-requests/{change_request_id}/reject` | Requirement | student.identity_change.approve |  | yes |  | yes |
| POST | `/api/v1/circular-suggestions/{suggestion_id}/confirm` | Requirement | circular.review | school |  |  | yes |
| POST | `/api/v1/circular-suggestions/{suggestion_id}/dismiss` | Requirement | circular.review | school |  |  | yes |
| GET | `/api/v1/circulars` | Requirement | document.read |  |  |  |  |
| GET | `/api/v1/circulars/{document_id}` | Requirement | document.read |  |  |  |  |
| POST | `/api/v1/circulars/{document_id}/read` | Requirement | circular.review | school |  |  |  |
| POST | `/api/v1/circulars/{document_id}/review` | Requirement | circular.review | school |  |  | yes |
| GET | `/api/v1/classes` | Requirement | student.read_basic |  |  |  |  |
| POST | `/api/v1/classes` | Requirement | tenant.structure.manage | school |  | yes |  |
| POST | `/api/v1/classes/defaults` | Requirement | tenant.structure.manage | school |  |  |  |
| GET | `/api/v1/classes/{class_id}` | Requirement | student.read_basic |  |  |  |  |
| PATCH | `/api/v1/classes/{class_id}` | Requirement | tenant.structure.manage | school |  |  | yes |
| POST | `/api/v1/classes/{class_id}/archive` | Requirement | tenant.structure.manage | school |  |  | yes |
| POST | `/api/v1/classes/{class_id}/unarchive` | Requirement | tenant.structure.manage | school |  |  | yes |
| GET | `/api/v1/documents` | Requirement | document.read |  |  |  |  |
| POST | `/api/v1/documents` | Requirement | document.upload |  |  | yes |  |
| POST | `/api/v1/documents/uploads` | Requirement | document.upload |  |  | yes |  |
| DELETE | `/api/v1/documents/{document_id}` | Requirement | document.manage_acl | school |  |  |  |
| GET | `/api/v1/documents/{document_id}` | Requirement | document.read |  |  |  |  |
| PATCH | `/api/v1/documents/{document_id}` | Requirement | document.upload |  |  |  | yes |
| PUT | `/api/v1/documents/{document_id}/acl` | Requirement | document.manage_acl | school |  |  | yes |
| POST | `/api/v1/documents/{document_id}/archive` | Requirement | document.manage_acl | school |  |  | yes |
| GET | `/api/v1/documents/{document_id}/download-url` | Requirement | document.read |  |  |  |  |
| GET | `/api/v1/documents/{document_id}/sheet` | Requirement | document.read |  |  |  |  |
| POST | `/api/v1/documents/{document_id}/sheet/export` | Requirement | document.read |  |  |  |  |
| POST | `/api/v1/documents/{document_id}/sheet/versions` | Requirement | document.upload |  |  | yes | yes |
| POST | `/api/v1/documents/{document_id}/unarchive` | Requirement | document.manage_acl | school |  |  | yes |
| POST | `/api/v1/documents/{document_id}/versions` | Requirement | document.upload |  |  | yes |  |
| GET | `/api/v1/dq/findings` | Requirement | dq.findings.read |  |  |  |  |
| GET | `/api/v1/dq/findings/{finding_id}` | Requirement | dq.findings.read |  |  |  |  |
| POST | `/api/v1/dq/findings/{finding_id}/resolve` | Requirement | dq.findings.resolve |  |  |  |  |
| POST | `/api/v1/dq/findings/{finding_id}/waive` | Requirement | dq.findings.waive |  | yes |  |  |
| GET | `/api/v1/dq/profiles` | Requirement | dq.findings.read |  |  |  |  |
| GET | `/api/v1/dq/rules` | Requirement | dq.findings.read |  |  |  |  |
| POST | `/api/v1/dq/runs` | Requirement | dq.findings.read |  |  | yes |  |
| GET | `/api/v1/dq/runs/{run_id}` | Requirement | dq.findings.read |  |  |  |  |
| GET | `/api/v1/dq/summary` | Requirement | dq.findings.read |  |  |  |  |
| PUT | `/api/v1/edge/tally/catalog` | edge_signature | tally.agent |  |  |  |  |
| GET | `/api/v1/edge/tally/config` | edge_signature | tally.agent |  |  |  |  |
| POST | `/api/v1/edge/tally/enrol` | edge_enrolment | tally.agent |  |  |  |  |
| POST | `/api/v1/edge/tally/key-rotation` | edge_signature | tally.agent |  |  |  |  |
| POST | `/api/v1/edge/tally/syncs` | edge_signature | tally.agent |  |  |  |  |
| GET | `/api/v1/exams` | AnyOfRequirement | marks.record / exam.manage |  |  |  |  |
| POST | `/api/v1/exams` | Requirement | exam.manage | school |  | yes |  |
| GET | `/api/v1/export-profiles` | AnyOfRequirement | export.portal |  |  |  |  |
| GET | `/api/v1/exports` | AnyOfRequirement | export.portal / student.export / export.read_all |  |  |  |  |
| POST | `/api/v1/exports` | AnyOfRequirement | export.portal |  | yes | yes |  |
| POST | `/api/v1/exports/student-list` | Requirement | student.export |  | yes | yes |  |
| GET | `/api/v1/exports/{export_id}` | AnyOfRequirement | export.portal / student.export / export.read_all |  |  |  |  |
| GET | `/api/v1/exports/{export_id}/download-url` | AnyOfRequirement | export.portal / student.export / export.download_any |  |  |  |  |
| GET | `/api/v1/extraction-batches` | Requirement | import.run | school |  |  |  |
| POST | `/api/v1/extraction-batches` | Requirement | import.run | school |  | yes |  |
| GET | `/api/v1/extraction-batches/{batch_id}` | Requirement | import.run | school |  |  |  |
| GET | `/api/v1/extraction-items` | Requirement | import.run | school |  |  |  |
| GET | `/api/v1/extraction-items/{item_id}` | Requirement | import.run | school |  |  |  |
| POST | `/api/v1/extraction-items/{item_id}/confirm` | Requirement | import.commit | school |  | yes |  |
| POST | `/api/v1/extraction-items/{item_id}/reject` | Requirement | import.commit | school |  |  |  |
| POST | `/api/v1/fleet/heartbeat` | fleet_signature | fleet.heartbeat |  |  |  |  |
| GET | `/api/v1/import-templates` | Requirement | import.run |  |  |  |  |
| POST | `/api/v1/import-templates` | Requirement | import.run |  |  | yes |  |
| GET | `/api/v1/imports` | Requirement | import.run |  |  |  |  |
| POST | `/api/v1/imports` | Requirement | import.run |  |  | yes |  |
| GET | `/api/v1/imports/{import_id}` | Requirement | import.run |  |  |  |  |
| POST | `/api/v1/imports/{import_id}/commit` | Requirement | import.commit |  |  | yes |  |
| PUT | `/api/v1/imports/{import_id}/mapping` | Requirement | import.run |  |  |  | yes |
| POST | `/api/v1/imports/{import_id}/revert` | Requirement | import.commit |  |  |  |  |
| GET | `/api/v1/imports/{import_id}/rows` | Requirement | import.run |  |  |  |  |
| GET | `/api/v1/imports/{import_id}/sheet` | Requirement | import.run |  |  |  |  |
| GET | `/api/v1/imports/{import_id}/sheet/export` | Requirement | import.run |  |  |  |  |
| PATCH | `/api/v1/imports/{import_id}/sheet/rows/{row_no}` | Requirement | import.run |  |  |  | yes |
| POST | `/api/v1/imports/{import_id}/validate` | Requirement | import.run |  |  | yes |  |
| GET | `/api/v1/insights/flags` | Requirement | insights.read |  |  |  |  |
| GET | `/api/v1/insights/flags/{flag_id}` | Requirement | insights.read |  |  |  |  |
| POST | `/api/v1/insights/flags/{flag_id}/actions` | Requirement | insights.act |  |  |  |  |
| POST | `/api/v1/insights/flags/{flag_id}/assign` | Requirement | insights.manage | school | yes |  | yes |
| POST | `/api/v1/insights/flags/{flag_id}/close` | Requirement | insights.act |  |  |  | yes |
| POST | `/api/v1/insights/flags/{flag_id}/erase` | Requirement | insights.manage | school | yes |  |  |
| GET | `/api/v1/insights/flags/{flag_id}/owners` | Requirement | insights.manage | school |  |  |  |
| GET | `/api/v1/insights/settings` | Requirement | insights.read |  |  |  |  |
| PUT | `/api/v1/insights/settings` | Requirement | insights.manage | school | yes |  | yes |
| GET | `/api/v1/insights/summary` | Requirement | insights.read |  |  |  |  |
| POST | `/api/v1/knowledge/ask` | Requirement | kb.ask |  |  |  |  |
| GET | `/api/v1/knowledge/conversations` | Requirement | kb.ask |  |  |  |  |
| DELETE | `/api/v1/knowledge/conversations/{conversation_id}` | Requirement | kb.ask |  |  |  |  |
| GET | `/api/v1/knowledge/conversations/{conversation_id}` | Requirement | kb.ask |  |  |  |  |
| PATCH | `/api/v1/knowledge/conversations/{conversation_id}` | Requirement | kb.ask |  |  |  | yes |
| DELETE | `/api/v1/knowledge/memories` | Requirement | kb.ask |  |  |  |  |
| GET | `/api/v1/knowledge/memories` | Requirement | kb.ask |  |  |  |  |
| POST | `/api/v1/knowledge/memories` | Requirement | kb.ask |  |  | yes |  |
| DELETE | `/api/v1/knowledge/memories/{memory_id}` | Requirement | kb.ask |  |  |  |  |
| PATCH | `/api/v1/knowledge/memories/{memory_id}` | Requirement | kb.ask |  |  |  | yes |
| POST | `/api/v1/knowledge/memories/{memory_id}/confirm` | Requirement | kb.ask |  |  |  |  |
| GET | `/api/v1/knowledge/memory-settings` | Requirement | kb.ask |  |  |  |  |
| PUT | `/api/v1/knowledge/memory-settings` | Requirement | kb.ask |  |  |  |  |
| POST | `/api/v1/knowledge/queries/{query_id}/feedback` | Requirement | kb.ask |  |  |  |  |
| POST | `/api/v1/knowledge/search` | Requirement | document.read |  |  |  |  |
| GET | `/api/v1/knowledge/verified-answers` | Requirement | kb.ask |  |  |  |  |
| POST | `/api/v1/knowledge/verified-answers` | Requirement | kb.verified_answer.manage |  |  | yes |  |
| POST | `/api/v1/knowledge/verified-answers/{answer_id}/retire` | Requirement | kb.verified_answer.manage |  |  |  | yes |
| POST | `/api/v1/knowledge/verified-answers/{answer_id}/review` | Requirement | kb.verified_answer.manage |  |  |  | yes |
| GET | `/api/v1/me` | Requirement | session.authenticated |  |  |  |  |
| POST | `/api/v1/me/accept-invitations` | PrincipalRequirement | session.authenticated |  |  |  |  |
| POST | `/api/v1/me/active-tenant` | PrincipalRequirement | session.authenticated |  |  |  |  |
| GET | `/api/v1/me/invitations` | PrincipalRequirement | session.authenticated |  |  |  |  |
| POST | `/api/v1/me/invitations/{membership_id}/accept` | PrincipalRequirement | session.authenticated |  |  |  |  |
| POST | `/api/v1/me/invitations/{membership_id}/decline` | PrincipalRequirement | session.authenticated |  |  |  |  |
| POST | `/api/v1/me/login-event` | PrincipalRequirement | session.authenticated |  |  |  |  |
| GET | `/api/v1/me/schools` | PrincipalRequirement | session.authenticated |  |  |  |  |
| GET | `/api/v1/notices` | Requirement | notice.draft | school |  |  |  |
| POST | `/api/v1/notices` | Requirement | notice.draft | school |  | yes |  |
| GET | `/api/v1/notices/{notice_id}` | Requirement | notice.draft | school |  |  |  |
| PATCH | `/api/v1/notices/{notice_id}` | Requirement | notice.draft | school |  |  | yes |
| POST | `/api/v1/notices/{notice_id}/approve` | Requirement | notice.approve | school |  |  | yes |
| GET | `/api/v1/notices/{notice_id}/download-url` | Requirement | notice.draft | school |  |  |  |
| POST | `/api/v1/notices/{notice_id}/draft` | Requirement | notice.draft | school |  |  | yes |
| POST | `/api/v1/notices/{notice_id}/render` | Requirement | notice.draft | school |  |  | yes |
| GET | `/api/v1/notifications` | Requirement | session.authenticated |  |  |  |  |
| POST | `/api/v1/notifications/read-all` | Requirement | session.authenticated |  |  |  |  |
| GET | `/api/v1/notifications/unread-count` | Requirement | session.authenticated |  |  |  |  |
| POST | `/api/v1/notifications/{notification_id}/read` | Requirement | session.authenticated |  |  |  |  |
| GET | `/api/v1/permissions` | Requirement | user.manage |  |  |  |  |
| GET | `/api/v1/platform/ai-bundles` | RequirePlatform | platform.plans.manage |  |  |  |  |
| GET | `/api/v1/platform/announcements` | RequirePlatform | platform.tenants.read |  |  |  |  |
| POST | `/api/v1/platform/announcements` | RequirePlatform | platform.announcements.manage |  |  |  |  |
| PATCH | `/api/v1/platform/announcements/{announcement_id}` | RequirePlatform | platform.announcements.manage |  |  |  | yes |
| POST | `/api/v1/platform/announcements/{announcement_id}/cancel` | RequirePlatform | platform.announcements.manage |  |  |  |  |
| GET | `/api/v1/platform/audit/events` | RequirePlatform | platform.audit.read |  |  |  |  |
| POST | `/api/v1/platform/audit/verify` | RequirePlatform | platform.audit.read |  |  |  |  |
| GET | `/api/v1/platform/break-glass-requests` | RequirePlatform | platform.tenants.read |  |  |  |  |
| POST | `/api/v1/platform/break-glass-requests` | RequirePlatform | platform.breakglass.request |  |  |  |  |
| POST | `/api/v1/platform/break-glass-requests/{request_id}/emergency-confirm` | RequirePlatform | platform.breakglass.emergency |  | yes |  |  |
| GET | `/api/v1/platform/dashboard` | RequirePlatform | platform.tenants.read |  |  |  |  |
| GET | `/api/v1/platform/deployments` | RequirePlatform | platform.fleet.read |  |  |  |  |
| GET | `/api/v1/platform/deployments/{deployment_id}` | RequirePlatform | platform.fleet.read |  |  |  |  |
| PATCH | `/api/v1/platform/deployments/{deployment_id}` | RequirePlatform | platform.fleet.manage |  | yes |  | yes |
| POST | `/api/v1/platform/deployments/{deployment_id}/decommission` | RequirePlatform | platform.fleet.manage |  | yes |  |  |
| POST | `/api/v1/platform/deployments/{deployment_id}/heartbeat-key:rotate` | RequirePlatform | platform.fleet.manage |  | yes |  |  |
| GET | `/api/v1/platform/flags` | RequirePlatform | platform.flags.read |  |  |  |  |
| PUT | `/api/v1/platform/flags/{key}` | RequirePlatform | platform.flags.manage |  | yes |  | yes |
| DELETE | `/api/v1/platform/flags/{key}/tenants/{tenant_id}` | RequirePlatform | platform.flags.manage |  | yes |  |  |
| PUT | `/api/v1/platform/flags/{key}/tenants/{tenant_id}` | RequirePlatform | platform.flags.manage |  | yes |  | yes |
| GET | `/api/v1/platform/fleet/versions` | RequirePlatform | platform.fleet.read |  |  |  |  |
| POST | `/api/v1/platform/invoice-runs` | RequirePlatform | platform.invoices.manage |  |  |  |  |
| GET | `/api/v1/platform/invoices` | RequirePlatform | platform.invoices.read |  |  |  |  |
| POST | `/api/v1/platform/invoices` | RequirePlatform | platform.invoices.manage |  |  |  |  |
| DELETE | `/api/v1/platform/invoices/{invoice_id}` | RequirePlatform | platform.invoices.manage |  |  |  |  |
| GET | `/api/v1/platform/invoices/{invoice_id}` | RequirePlatform | platform.invoices.read |  |  |  |  |
| PATCH | `/api/v1/platform/invoices/{invoice_id}` | RequirePlatform | platform.invoices.manage |  |  |  | yes |
| GET | `/api/v1/platform/invoices/{invoice_id}/download-url` | RequirePlatform | platform.invoices.read |  |  |  |  |
| POST | `/api/v1/platform/invoices/{invoice_id}/issue` | RequirePlatform | platform.invoices.manage |  |  |  |  |
| GET | `/api/v1/platform/invoices/{invoice_id}/payments` | RequirePlatform | platform.invoices.read |  |  |  |  |
| POST | `/api/v1/platform/invoices/{invoice_id}/payments` | RequirePlatform | platform.invoices.manage |  |  |  |  |
| POST | `/api/v1/platform/invoices/{invoice_id}/void` | RequirePlatform | platform.invoices.manage |  |  |  |  |
| GET | `/api/v1/platform/jobs/{job_id}` | RequirePlatform | platform.tenants.read |  |  |  |  |
| GET | `/api/v1/platform/me` | RequirePlatform | platform.tenants.read |  |  |  |  |
| GET | `/api/v1/platform/operators` | RequirePlatform | platform.operators.manage |  |  |  |  |
| POST | `/api/v1/platform/operators` | RequirePlatform | platform.operators.manage |  | yes |  |  |
| POST | `/api/v1/platform/operators/{operator_id}/deactivate` | RequirePlatform | platform.operators.manage |  | yes |  |  |
| PUT | `/api/v1/platform/operators/{operator_id}/roles` | RequirePlatform | platform.operators.manage |  | yes |  |  |
| POST | `/api/v1/platform/payments/{payment_id}/reverse` | RequirePlatform | platform.invoices.manage |  |  |  |  |
| GET | `/api/v1/platform/plans` | RequirePlatform | platform.plans.manage |  |  |  |  |
| POST | `/api/v1/platform/plans` | RequirePlatform | platform.plans.manage |  | yes |  |  |
| GET | `/api/v1/platform/plans/{plan_id}` | RequirePlatform | platform.plans.manage |  |  |  |  |
| PATCH | `/api/v1/platform/plans/{plan_id}` | RequirePlatform | platform.plans.manage |  | yes |  | yes |
| POST | `/api/v1/platform/plans/{plan_id}/publish` | RequirePlatform | platform.plans.manage |  | yes |  |  |
| POST | `/api/v1/platform/plans/{plan_id}/retire` | RequirePlatform | platform.plans.manage |  | yes |  |  |
| GET | `/api/v1/platform/subscriptions` | RequirePlatform | platform.subscriptions.read |  |  |  |  |
| GET | `/api/v1/platform/subscriptions/{sub_id}` | RequirePlatform | platform.subscriptions.read |  |  |  |  |
| POST | `/api/v1/platform/subscriptions/{sub_id}/activate` | RequirePlatform | platform.subscriptions.manage |  | yes |  |  |
| DELETE | `/api/v1/platform/subscriptions/{sub_id}/ai-bundle` | RequirePlatform | platform.subscriptions.manage |  | yes |  | yes |
| PUT | `/api/v1/platform/subscriptions/{sub_id}/ai-bundle` | RequirePlatform | platform.subscriptions.manage |  | yes |  | yes |
| POST | `/api/v1/platform/subscriptions/{sub_id}/cancel` | RequirePlatform | platform.subscriptions.manage |  | yes |  |  |
| POST | `/api/v1/platform/subscriptions/{sub_id}/change-plan` | RequirePlatform | platform.subscriptions.manage |  | yes |  |  |
| POST | `/api/v1/platform/subscriptions/{sub_id}/extend-trial` | RequirePlatform | platform.subscriptions.manage |  | yes |  |  |
| DELETE | `/api/v1/platform/subscriptions/{sub_id}/price-override` | RequirePlatform | platform.subscriptions.manage |  | yes |  | yes |
| PUT | `/api/v1/platform/subscriptions/{sub_id}/price-override` | RequirePlatform | platform.subscriptions.manage |  | yes |  | yes |
| POST | `/api/v1/platform/subscriptions/{sub_id}/reactivate` | RequirePlatform | platform.subscriptions.manage |  | yes |  |  |
| POST | `/api/v1/platform/subscriptions/{sub_id}/suspend` | RequirePlatform | platform.subscriptions.manage |  | yes |  |  |
| GET | `/api/v1/platform/support/tickets` | RequirePlatform | platform.support.read |  |  |  |  |
| POST | `/api/v1/platform/support/tickets` | RequirePlatform | platform.support.manage |  |  |  |  |
| GET | `/api/v1/platform/support/tickets/{ticket_id}` | RequirePlatform | platform.support.read |  |  |  |  |
| PATCH | `/api/v1/platform/support/tickets/{ticket_id}` | RequirePlatform | platform.support.manage |  |  |  | yes |
| POST | `/api/v1/platform/support/tickets/{ticket_id}/messages` | RequirePlatform | platform.support.manage |  |  |  |  |
| GET | `/api/v1/platform/tenants` | RequirePlatform | platform.tenants.read |  |  |  |  |
| POST | `/api/v1/platform/tenants` | RequirePlatform | platform.tenants.provision |  | yes |  |  |
| GET | `/api/v1/platform/tenants/{tenant_id}` | RequirePlatform | platform.tenants.read |  |  |  |  |
| POST | `/api/v1/platform/tenants/{tenant_id}/activate` | RequirePlatform | platform.tenants.provision |  | yes |  |  |
| GET | `/api/v1/platform/tenants/{tenant_id}/billing-account` | RequirePlatform | platform.invoices.read |  |  |  |  |
| PUT | `/api/v1/platform/tenants/{tenant_id}/billing-account` | RequirePlatform | platform.subscriptions.manage |  | yes |  | yes |
| GET | `/api/v1/platform/tenants/{tenant_id}/deletion-certificate/download-url` | RequirePlatform | platform.tenants.read |  |  |  |  |
| GET | `/api/v1/platform/tenants/{tenant_id}/offboarding` | RequirePlatform | platform.tenants.read |  |  |  |  |
| POST | `/api/v1/platform/tenants/{tenant_id}/offboarding` | RequirePlatform | platform.tenants.offboard |  | yes |  |  |
| POST | `/api/v1/platform/tenants/{tenant_id}/offboarding:approve` | RequirePlatform | platform.tenants.offboard |  | yes |  |  |
| POST | `/api/v1/platform/tenants/{tenant_id}/offboarding:confirm-export` | RequirePlatform | platform.tenants.offboard |  | yes |  |  |
| POST | `/api/v1/platform/tenants/{tenant_id}/offboarding:confirm-teardown` | RequirePlatform | platform.tenants.offboard |  | yes |  |  |
| POST | `/api/v1/platform/tenants/{tenant_id}/owner-invite:resend` | RequirePlatform | platform.tenants.provision |  | yes |  |  |
| POST | `/api/v1/platform/tenants/{tenant_id}/provisioning:resume` | RequirePlatform | platform.tenants.provision |  | yes |  |  |
| POST | `/api/v1/platform/tenants/{tenant_id}/reactivate` | RequirePlatform | platform.tenants.suspend |  | yes |  |  |
| POST | `/api/v1/platform/tenants/{tenant_id}/suspend` | RequirePlatform | platform.tenants.suspend |  | yes |  |  |
| GET | `/api/v1/platform/tenants/{tenant_id}/usage` | RequirePlatform | platform.usage.read |  |  |  |  |
| GET | `/api/v1/platform/usage` | RequirePlatform | platform.usage.read |  |  |  |  |
| GET | `/api/v1/registers/admission-withdrawal` | Requirement | register.read | school | yes |  |  |
| GET | `/api/v1/registers/certificates` | Requirement | register.read | school | yes |  |  |
| GET | `/api/v1/registers/transfer-certificates` | Requirement | register.read | school | yes |  |  |
| GET | `/api/v1/roles` | Requirement | user.manage |  |  |  |  |
| GET | `/api/v1/sections` | Requirement | student.read_basic |  |  |  |  |
| POST | `/api/v1/sections` | Requirement | tenant.structure.manage | school |  | yes |  |
| GET | `/api/v1/sections/{section_id}` | Requirement | student.read_basic |  |  |  |  |
| PATCH | `/api/v1/sections/{section_id}` | Requirement | tenant.structure.manage | school |  |  | yes |
| POST | `/api/v1/sections/{section_id}/archive` | Requirement | tenant.structure.manage | school |  |  | yes |
| GET | `/api/v1/sections/{section_id}/attendance` | Requirement | attendance.read |  |  |  |  |
| POST | `/api/v1/sections/{section_id}/attendance` | Requirement | attendance.record |  |  |  |  |
| GET | `/api/v1/sections/{section_id}/attendance/month` | Requirement | attendance.read |  |  |  |  |
| POST | `/api/v1/sections/{section_id}/attendance/sheet` | Requirement | attendance.record |  |  |  |  |
| GET | `/api/v1/sections/{section_id}/exams/{exam_id}/marks` | Requirement | marks.read |  |  |  |  |
| POST | `/api/v1/sections/{section_id}/exams/{exam_id}/marks` | Requirement | marks.record |  |  |  |  |
| POST | `/api/v1/sections/{section_id}/exams/{exam_id}/marks/sheet` | Requirement | marks.record |  |  |  |  |
| POST | `/api/v1/sections/{section_id}/unarchive` | Requirement | tenant.structure.manage | school |  |  | yes |
| GET | `/api/v1/staff` | AnyOfRequirement | user.manage |  |  |  |  |
| GET | `/api/v1/students` | Requirement | student.read_basic |  |  |  |  |
| POST | `/api/v1/students` | Requirement | student.create | school |  | yes |  |
| POST | `/api/v1/students/search` | Requirement | student.read_basic |  |  |  |  |
| GET | `/api/v1/students/{student_id}` | Requirement | student.read_basic |  |  |  |  |
| PATCH | `/api/v1/students/{student_id}` | Requirement | student.update_nonidentity |  |  |  | yes |
| GET | `/api/v1/students/{student_id}/behaviour-notes` | Requirement | insights.read |  |  |  |  |
| POST | `/api/v1/students/{student_id}/behaviour-notes` | Requirement | insights.note |  |  | yes |  |
| POST | `/api/v1/students/{student_id}/certificates` | Requirement | certificate.issue |  |  | yes |  |
| GET | `/api/v1/students/{student_id}/certificates/preview` | Requirement | certificate.issue |  |  |  |  |
| GET | `/api/v1/students/{student_id}/enrollments` | Requirement | student.read_basic |  |  |  |  |
| POST | `/api/v1/students/{student_id}/enrollments` | Requirement | student.update_nonidentity |  |  | yes |  |
| PATCH | `/api/v1/students/{student_id}/enrollments/{enrollment_id}` | Requirement | student.update_nonidentity |  |  |  | yes |
| POST | `/api/v1/students/{student_id}/enrollments/{enrollment_id}/end` | Requirement | student.update_nonidentity |  |  |  | yes |
| POST | `/api/v1/students/{student_id}/flags` | Requirement | insights.act |  |  | yes |  |
| GET | `/api/v1/students/{student_id}/guardians` | Requirement | student.read_basic |  |  |  |  |
| POST | `/api/v1/students/{student_id}/guardians` | Requirement | student.update_nonidentity |  |  | yes |  |
| DELETE | `/api/v1/students/{student_id}/guardians/{guardian_id}` | Requirement | student.update_nonidentity |  |  |  | yes |
| PATCH | `/api/v1/students/{student_id}/guardians/{guardian_id}` | Requirement | student.update_nonidentity |  |  |  | yes |
| POST | `/api/v1/students/{student_id}/sensitive-reveal` | Requirement | student.read_sensitive |  |  |  |  |
| GET | `/api/v1/students/{student_id}/timeline` | Requirement | insights.read |  |  |  |  |
| GET | `/api/v1/students/{student_id}/values` | Requirement | student.read_basic |  |  |  |  |
| POST | `/api/v1/students/{student_id}/values` | Requirement | student.update_nonidentity |  |  | yes |  |
| POST | `/api/v1/students/{student_id}/values/{value_id}/verify` | Requirement | student.update_nonidentity |  |  |  |  |
| GET | `/api/v1/support/tickets` | Requirement | support.ticket.create |  |  |  |  |
| POST | `/api/v1/support/tickets` | Requirement | support.ticket.create |  |  | yes |  |
| GET | `/api/v1/support/tickets/{ticket_id}` | Requirement | support.ticket.create |  |  |  |  |
| POST | `/api/v1/support/tickets/{ticket_id}/messages` | Requirement | support.ticket.create |  |  | yes |  |
| GET | `/api/v1/tally/devices` | Requirement | tally.device.manage | school |  |  |  |
| POST | `/api/v1/tally/devices/{device_id}/revoke` | Requirement | tally.device.manage | school | yes |  | yes |
| GET | `/api/v1/tally/dues` | Requirement | finance.read | school |  |  |  |
| POST | `/api/v1/tally/enrolment-codes` | Requirement | tally.device.manage | school | yes |  |  |
| GET | `/api/v1/tally/groups` | Requirement | tally.configure | school |  |  |  |
| PUT | `/api/v1/tally/groups/selection` | Requirement | tally.configure | school |  |  |  |
| GET | `/api/v1/tally/parties` | Requirement | tally.configure | school |  |  |  |
| POST | `/api/v1/tally/parties/search` | Requirement | tally.configure | school |  |  |  |
| GET | `/api/v1/tally/parties/{party_id}` | Requirement | tally.configure | school |  |  |  |
| POST | `/api/v1/tally/parties/{party_id}/links` | Requirement | tally.configure | school |  |  |  |
| DELETE | `/api/v1/tally/parties/{party_id}/links/{student_id}` | Requirement | tally.configure | school |  |  |  |
| GET | `/api/v1/tally/status` | AnyOfRequirement | tally.device.manage / tally.configure |  |  |  |  |
| GET | `/api/v1/task-assignees` | AnyOfRequirement | circular.review |  |  |  |  |
| GET | `/api/v1/tasks` | Requirement | task.read |  |  |  |  |
| POST | `/api/v1/tasks` | Requirement | task.manage | school |  | yes |  |
| GET | `/api/v1/tasks/{task_id}` | Requirement | task.read |  |  |  |  |
| PATCH | `/api/v1/tasks/{task_id}` | Requirement | task.manage | school |  |  | yes |
| POST | `/api/v1/tasks/{task_id}/status` | Requirement | task.read |  |  |  | yes |
| GET | `/api/v1/tenant` | Requirement | session.authenticated |  |  |  |  |
| PATCH | `/api/v1/tenant` | Requirement | tenant.settings.manage |  | yes |  | yes |
| GET | `/api/v1/tenant/billing` | Requirement | tenant.billing.read |  |  |  |  |
| GET | `/api/v1/tenant/billing/invoices` | Requirement | tenant.billing.read |  |  |  |  |
| GET | `/api/v1/users` | Requirement | user.manage |  |  |  |  |
| POST | `/api/v1/users` | Requirement | user.manage |  | yes | yes |  |
| GET | `/api/v1/users/{user_id}` | Requirement | user.manage |  |  |  |  |
| PATCH | `/api/v1/users/{user_id}` | Requirement | user.manage |  | yes |  | yes |
| POST | `/api/v1/users/{user_id}/invitation-email` | Requirement | user.manage | school | yes |  |  |
| PUT | `/api/v1/users/{user_id}/roles` | Requirement | role.assign |  | yes |  |  |
| PUT | `/api/v1/users/{user_id}/scopes` | Requirement | role.assign |  | yes |  |  |
| GET | `/healthz` | public |  |  |  |  |  |
| GET | `/readyz` | public |  |  |  |  |  |
