# Security audit 2026-10-05: authorization and business logic (app-logic)

Auditor A of the second deep audit (wave 5), on branch `wip/sec2-app-logic` from
`claude/friendly-ptolemy-0tl3br` (0236299).

**Scope:**
- every tenant and control-plane route (323 routes, enumerated from the live app with their guards);
- per object and per function authorization (BOLA, BFLA);
- mass assignment and excessive data exposure;
- workflows: maker-checker, two-person rules, revert windows, certificate issue and cancel, invitation consent, suspension;
- races, idempotency, If-Match and cursors.

The round-one reports (`audit-2026-10-04-*.md`) were read first. Nothing they found or verified is repeated here unless a fix was incomplete.

**Method.** Each finding below has a concrete path from actor to input, code and outcome. Every fixed finding has a test that failed before the fix and passes after it. The data is synthetic only. Paths are relative to `apps/api/`.

**References used:**
- OWASP API Security Top 10 2023 (API1 BOLA, API3 BOPLA, API5 BFLA, API6 sensitive business flows);
- OWASP ASVS 4.0.3: V4 access control (L2/L3), V11 business logic, V1.4;
- CWE;
- India's DPDP Act 2023.

## Findings

| ID | Severity (CVSS 3.1, why) | Reference | File:line | Exploit path | Status | Proving test |
|---|---|---|---|---|---|---|
| A-01 | **Medium** (AV:N/AC:L/PR:L/UI:N/S:U/C:N/I:H/A:N 6.5): a certificate is printed from a record whose official DOB or name conflict was hidden, with no waiver and no step-up. It defeats the DL-06 fix by another route. | ASVS V11.1.2, V4.1.3; API6; CWE-840 | `app/students/service.py` `record_value` (non-identity C3 write); `app/dq/engine.py:395-417` (`_clear` auto-clears); `app/certificates/service.py` (`dq.open_blockers` only) | 1. `office_staff` (no MFA, no `dq.findings.waive`, no `student.read_sensitive`) faces an open DQ-002 blocker: the register DOB differs from the DOB printed on the Aadhaar card. 2. They send `POST /students/{id}/values {"attribute_key":"aadhaar_dob_as_printed","source":"aadhaar_as_printed","value":"<register DOB>"}`. This is a non-identity attribute, so it needs no evidence or change request, and it supersedes even a verified value. 3. The incremental DQ run marks the finding `resolved/auto_cleared`. 4. `POST /students/{id}/certificates` issues the certificate. | **Fixed in 6ed8570** (lead recommendation, owner decision 2026-10-07: option (b)). A blocker comparing sources that a run no longer finds because a compared value changed without evidence or verification (or was withdrawn) becomes `needs_confirmation` (migration 0049), not `resolved`. It counts as unresolved, so certificates and exports keep refusing until a `dq.findings.waive` holder with step-up confirms it through `POST /dq/findings/{id}/resolve` (audited `dq.finding.resolved`). A verified or evidence-backed correction still resolves at once; a typo fix needs one confirmation. Web: status label and an explanation on the finding page. | `tests/certificates/test_api.py::test_A_01_a_blocker_cleared_by_a_write_without_evidence_needs_confirmation`; `tests/dq/test_engine.py::test_A_01_*`; `tests/dq/test_migration.py::test_A_01_0049_*`; `findings.test.tsx` "a blocker waiting for confirmation" |
| A-02 | **Low** (PR:H, I:L): an external auditor's access never expires | ASVS V4.1.3; CWE-613 | `app/identity/service.py` `set_roles` | docs/07 §6.2 says `auditor_readonly` memberships are time-bound (14 days), and an invitation applies that limit. `PUT /users/{id}/roles {"roles":["auditor_readonly"]}` on an existing open-ended membership left it open-ended. | **Fixed in c1808b5.** Adding a role with a membership TTL caps the expiry at now + TTL, as an invitation does. An earlier expiry is never extended. Audit `membership.expiry_set`. | `tests/api/test_users_time_bound_roles.py` |
| A-03 | **Low** (needs the person's IdP subject): privacy (DPDP §8(5)). It extends DL-09. | API3 (BOPLA); ASVS V8.3.1 | `app/identity/service.py` `_user_out` | School B invites a person who already belongs to school A. Until they accept, and also after they decline, `GET /users` and `GET /users/{id}` hid the email but kept showing `last_login_at`, which is the person's sign-in activity at school A. | **Fixed in 5874c66.** `last_login_at` is null whenever `contact_hidden` is true. The OpenAPI description is updated. | `tests/api/test_invitation_consent.py::test_DL_09_inviting_school_does_not_see_when_the_existing_account_signs_in` |
| A-04 | **Medium-low** (PR:L, I:L): a teacher rewrites another section's register; scope crossing | API1 (BOLA, write); ASVS V4.2.1; CWE-639 | `app/academics/repository.py` (`upsert_attendance`, `upsert_marks` move `section_id` on conflict); `app/academics/service.py` `record_attendance`, `record_marks` | A student moves from 9C to 9A. The 9A class teacher posts attendance for a day the 9C teacher had already marked, or marks for an exam held before the move. The row was overwritten and moved to 9A, so it vanished from 9C's register. The early-warning rules re-ran on the rewritten history, so a teacher could raise or suppress a consecutive-absence flag. | **Fixed in 12621fc and 0124354.** A scoped recorder gets 422 `recorded_in_another_section`. School-wide recorders (principal) can still correct. The web shows a plain message (1a7a7e7). | `tests/academics/test_service.py::test_SEC_015_a_scoped_teacher_cannot_rewrite_another_sections_attendance`, `..._marks` |
| A-05 | **Medium** (PR:L, I:H on an official record): breaks invariant 6 | ASVS V11.1.1; API6; CWE-284 | `app/students/service.py` `_identity_guard` | An approved change request records a **verified** `dob` at `birth_certificate`, which becomes canonical. `office_staff` then sends `POST /students/{id}/values {"attribute_key":"dob","source":"birth_certificate","value":"2011-01-01"}`, or imports a sheet with that source. The guard checked only the new value's verification and the anchor source, so the approved correction was superseded without maker-checker. Canonical `dob`, which feeds certificates and exports, fell back to an unverified value. The docstring already promised a refusal. | **Fixed in c174367.** Superseding a verified identity value now answers 403 `identity_change_required`. An import row doing this fails at commit (`commit_row_failed`). | `tests/students/test_api.py::test_BR_01_a_verified_identity_value_is_not_replaced_without_a_change_request` |
| A-06 | **Medium-low** (PR:L, I:L): function-level authorization gap | API5 (BFLA); ASVS V4.1.3; CWE-285 | `app/extraction/service.py` `confirm_item` → `students.create_student` (no permission check inside) | `exam_coordinator` holds `import.commit` but not `student.create`. `POST /extraction-items/{id}/confirm` with no `student_id` created and enrolled a student. `POST /students` and imports both require `student.create`. | **Fixed in 3dc3763.** 403 `student_create_required`. One existing input-rules test now runs as `office_admin`, which holds the permission; its assertions are unchanged. | `tests/extraction/test_api.py::test_SEC_003_confirm_creates_a_student_only_with_student_create` |
| A-07 | **Low-medium** (PR:L, I:L): a cancelled TC can be handed out as valid | ASVS V11.1.3; CWE-672 | `app/certificates/service.py` `download_url`, `_page` | (a) The PDF stored at issue carries no CANCELLED mark, but `GET /certificates/{id}/download-url` kept serving it after the certificate was cancelled. (b) Duplicates of a cancelled original stayed `issued`, printed and downloaded with no mark; only the register said "Original cancelled". | **Fixed in f376095.** Download answers 409 `certificate_cancelled` for a cancelled certificate and for a duplicate of one. The print view marks such a duplicate CANCELLED. The web message is in 1a7a7e7. | `tests/certificates/test_service.py::test_SEC_015_a_cancelled_certificates_clean_pdf_is_not_handed_out`, `::test_SEC_015_a_duplicate_of_a_cancelled_original_is_marked_and_not_handed_out` |
| A-08 | **Low-medium** (operator insider, financial integrity) | ASVS V11.1.2; CWE-841 | `app/platform/tenants.py` `reactivate` | 1. An engineer suspends a school for security. 2. Its subscription goes past due and billing suspends it. The school is not `active`, so the deployment keeps the security reason. 3. The engineer reactivates the school. It goes live while the subscription stays `suspended`, which is never invoiced or marked past due. The reason was also read outside the deployment lock. | **Fixed in 8654ad9.** Reactivate refuses (409 `billing_suspension`) while the live subscription is suspended, and checks under the deployment lock. docs/16 §5 is updated. | `tests/platform/test_billing.py::test_FR_PLT_004_lifting_a_security_hold_does_not_lift_a_billing_suspension` |
| A-09 | **Low** (PR:L): false evidence claims, a deletion pin and an existence oracle | API1; ASVS V4.2.1; CWE-639 | `app/students/service.py` `_check_evidence` (was a no-op) | `POST /students/{id}/values` or `POST /students` with `evidence_document_id` set to any document of the school, even one the caller cannot see. The value then showed as evidence-backed, the FK pinned the document (owner deletes and retention failed `document_in_use`), and 422 versus 201 revealed whether an id existed. | **Fixed in e90bffd.** The document must be visible to the caller and have a usable version; otherwise 422 `evidence_document_id not_found`, the same answer as for an unknown id. | `tests/students/test_api.py::test_SEC_015_evidence_must_be_a_document_the_caller_can_see` |
| A-10 | **Medium-low** (PR:L, I:L) | API5; ASVS V4.1.3 | `app/imports/service.py` `_apply_row` → `students.record_value` (`permission=READ`); `imports/validation.py` `allowed_targets`; extraction confirm on an existing student | `exam_coordinator` (`import.commit`, without `student.update_nonidentity` or `student.read_sensitive`) imports a sheet with source `parent_form` mapped to `address`, `caste`, `religion` or `health_notes`. It overwrites C3 and other values of any student in the school. The extraction update branch also records verified register values. | **Fixed in 5bd08ff** (lead recommendation, owner decision 2026-10-07: `import.commit` deliberately includes updates, recorded in docs/07 §6.2; a C3 value written to an existing student needs `student.read_sensitive`, else the row fails `sensitive_update_not_permitted`). Register-photo confirmation records no C3 fields. | `tests/imports/test_validation.py::test_A_10_updating_a_sensitive_value_needs_read_sensitive` |
| A-11 | **Low** (insider timing) | ASVS V11.1.3 (TOCTOU); CWE-367 | `app/certificates/service.py` `approve` → `_issue` → `_build_content` | A TC's content is built from the live record at approval. If-Match covers only the certificate row. The clerk can change `nationality`, `mother_tongue` or the enrolment (admission class, class at leaving) after the principal has read the draft, and the approval freezes the edited values. | **Fixed in 0cde6ebf and 017f4e6b** (lead recommendation, owner decision 2026-10-07). The approval carries the draft's content hash (`draft_sha256`). A mismatch is 409 `certificate_draft_changed`. | `tests/certificates/test_service.py::test_A_11_*` |
| A-12 | **Medium-low** (security suspension not enforced on async work) | ASVS V4.1.1, V1.4.4; CWE-285 | `app/ops/service.py` (outbox dispatch), every `*/tasks.py`; `authz/resolver.py` enforces suspension on HTTP routes only | Jobs queued before a suspension still run after it: an import commit, register extraction (photos sent to the OCR provider), knowledge ingestion and summaries (LLM calls), PDF rendering and invitation emails. Celery retries stretch the window. Beat jobs (task reminders, the break-glass sweep) keep acting on suspended schools. | **Fixed in 2c0a2701** (lead recommendation, owner decision 2026-10-07: a check in the task base). Outbox consumers use `ops.service.TenantTask`. Work for a suspended or offboarding school is held and runs once the school is active again. Exempt: the owner's export, key re-encryption, the audit-chain check and deletions. | `tests/ops/test_suspended_hold.py` |
| A-13 | **Low-medium** (operator insider) | ASVS V11.1.6, V1.2.4; CWE-654 | `app/platform/tenants.py` (offboarding request and approve); `app/platform/breakglass.py` (emergency confirmations) | A two-person request never expires and cannot be withdrawn. The approval checks only that the operator ids differ, not the requester's current status or the request's age. Owner A requests offboarding, leaves or is deactivated, and months later owner B alone completes it. An emergency break-glass first confirmation likewise stays valid indefinitely, and `emergency=true` is accepted with `reason_code=support_request`. | **Fixed in 15e18c2e and 53d255f0** (lead recommendation, owner decision 2026-10-07). Two-person requests expire after 72 h (409 `request_expired`) and can be withdrawn. The second step re-checks the first operator (409 `requester_not_authorised`). Emergencies need `security_incident` or `legal_obligation`. | `tests/platform/test_two_person_requests.py::test_A_13_*` |
| A-14 | **Low** (needs operator-pool admin rights too) | ASVS V1.2.4 | `app/platform/operators.py` `invite_operator`, `set_roles` | One `platform_owner` invites a second operator account they control, grants it `platform_owner` and uses it as the second person. | **Fixed in 15e18c2e.** The second operator's role must be at least 7 days old, and neither operator's role may have been granted by the other (409 `approver_not_eligible`). | `tests/platform/test_two_person_requests.py::test_A_14_*` |
| A-15 | **Low** (PR:L, C:L) | API1; CWE-639 | `app/imports/service.py` `create_import` (visibility only) | `office_staff` (`import.run`, school-wide `document.read`, without `marks.read`) starts an import from a class teacher's marks or attendance sheet before the teacher previews it, then reads it through `GET /imports/{id}/sheet`. That blocks the teacher's preview (AA-11). A C3 column under an unrecognised header is shown in clear. | **Fixed in 2e34deb** (lead recommendation, owner decision 2026-10-07): without `student.read_sensitive` an import starts only from your own upload (404 otherwise), as for DL-02 and DL-04. | `tests/imports/test_api.py::test_A_15_an_import_starts_only_from_your_own_upload_unless_you_read_sensitive` |
| A-16 | **Low** (C1 content) | API3; CWE-200 | `app/circulars/service.py` `list_notices`, `get_notice` | A principal drafts a parent notice from an ACL-restricted circular. Every `notice.draft` holder (`office_staff`) reads the AI summary in `body_en` although `/circulars/{id}` answers 404 for them. This is the same pattern as DL-08. | **Fixed in 37ba92a** (lead recommendation, owner decision 2026-10-07, with DL-08: hide until approved). A notice not yet approved that was drafted from a circular is shown only to its drafter and to people who may read the circular (404 on every notice route, left out of the list). An approved notice is for parents and stays visible to every drafter. | `tests/circulars/test_api.py::test_A_16_a_draft_from_a_circular_you_cannot_see_is_not_shown_until_approved` |
| A-17 | **Low** (lost update between two admins) | ASVS V11.1.4; CWE-362 | `app/identity/api.py` `PUT /users/{id}/roles`, `PUT /users/{id}/scopes` | These replace whole sets without `If-Match`. Admin A revokes a role while admin B saves a stale list that still holds it, and the revoked role comes back. docs/09 §2 says updates require `If-Match`. | **Fixed** by round three R-03 (71b258e, 6f1b306): optional `If-Match` on `PUT /users/{id}/roles` and `/scopes`; the membership is locked and its version bumped. | `tests/api/test_users_roles_if_match.py` |
| A-18 | **Low** (needs the subject) | API3; DPDP §8(5) | `app/identity/service.py` `_user_out` | Following on from A-03: while an invitation to an existing account is open, the inviting school still sees that account's stored `display_name` (not the name it typed) and `profile_shared: true`, which says the person belongs to another school. | **Fixed in 480cf238 (lead recommendation, owner decision 2026-10-07: show the invited name).** `core.memberships.invited_display_name` (migration 0049) keeps the name the school typed. While the invitation to an account shared with another school is open, `UserOut.display_name` is that name and `profile_shared` is `false`; after acceptance the person is shown as any member. The web offers no profile edit on such an open invitation. The `invitation_needs_consent` answer on a manual activation (DL-09) still says the person accepts themselves, by design. | `tests/api/test_invitation_consent.py::test_A_18_*`; web `users.test.tsx` (DL-09 case) |
| A-19 | **Low** (insider, by design) | ASVS V4.1.3 | `app/identity/service.py` `invite_user` (scopes) versus `set_scopes` (`role.assign`) | `office_admin` (`user.manage`, without `role.assign` or any `insights.*`/`marks.*` permission) can invite a `class_teacher` with scope `school`, or every section. That gives school-wide behaviour notes and marks, and could go to a second account the admin controls. Changing scopes later needs `role.assign`; inviting does not. | **Fixed in dee2aa3** (lead recommendation, owner decision 2026-10-07): inviting a scoped role (class teacher, teacher) with a `school` scope needs `role.assign` (403 `role_not_grantable`); section and class scopes stay with `user.manage`. | `tests/api/test_users.py::test_A_19_a_school_wide_scope_on_a_scoped_role_needs_role_assign` |

## Hardening (no exploit path with today's system roles, or very low impact)

**Custom roles: scope checks that only `has()`**
- **Task status and visibility** use `ctx.has()` only, not the scope of `task.manage` / `task.read_all` (`circulars/service.py` `_visible_task`, `set_task_status`). **Fixed in 40f935a:** they reach other people's tasks only when granted school-wide (scoped grants fail closed). Test: `tests/circulars/test_api.py::test_SEC_015_scoped_task_grants_reach_only_your_own_tasks`.
- **DQ resolve and waive** reach findings through the read scope, not the scope of `resolve` / `waive` (`dq/service.py`). **Fixed in 40f935a:** 403 `out_of_scope` outside their own scope. Test: `tests/dq/test_engine.py::test_SEC_015_resolve_and_waive_follow_their_own_scope_for_custom_roles`.
- **Tally party balances** are shown to `tally.configure` holders without `finance.read`. **Fixed in 40f935a:** `closing_balance` is null without school-wide `finance.read`; the web shows a dash. Test: `tests/tally/test_api.py::test_SEC_003_party_balances_only_for_finance_readers`.
- All three affect only custom roles.

**Change requests**
- **Evidence is not pinned.** Only the document id is stored, not the version. Evidence can take new versions, and approval does not re-check that it is `ready` and visible to the approver. Pin the version at submit. **Fixed in 61dd9eaf:** submit pins the evidence version; approval answers 409 `evidence_not_ready`, `evidence_not_usable` or `evidence_not_visible`. Tests: `tests/changes/test_service.py::test_FR_CR_00*`.
- **Race before approval.** `approve` compares `old_value_id` before `record_verified_identity_value` locks the student. Lock the student first. **Fixed in 61dd9eaf:** approval locks the student before comparing `old_value_id`. Test: `tests/changes/test_service.py::test_FR_CR_002_approval_locks_the_student_before_comparing_the_old_value`.

**Certificates**
- **Pending change requests** do not block issuing a certificate that prints the field being corrected. **Fixed in 0cde6ebf:** a pending change request on a printed field is a `change_request_pending` blocker (409 on issue and approve). Test: `tests/certificates/test_service.py::test_pending_change_request_on_a_printed_field_blocks_issuing`.
- **Inactive requesters.** TC and change-request approvals do not check that the requester is still active. **Fixed in 61dd9eaf** (change requests) and 0cde6ebf (certificates): 409 `requester_inactive`. Tests: `tests/changes/test_service.py::test_FR_CR_002_requester_must_still_be_an_active_member`, `tests/certificates/test_service.py::test_tc_approval_needs_the_preparer_to_still_be_an_active_member`.

**DQ runs**
- `POST /dq/runs` is open to read-only holders (auditor, class teacher). **Fixed in a4eb8f4** (lead recommendation, owner decision 2026-10-07): it needs `dq.findings.resolve`; the web offers "Check now" only to resolvers. Test: `tests/dq/test_api.py::test_SEC_003_read_only_holders_cannot_start_a_check_run`.
- Runs are not throttled. **Fixed** by the P2-07 limiter (d83ef8d): `POST /dq/runs` spends the per-school `heavy_jobs` budget, charged after authorization. Test: `tests/security/test_rate_limit_routes.py`.
- `profile_key` and `section_ids` are query parameters, which the tenant `Idempotency-Key` hash does not cover (`authz/http.py` hashes the method, the path without the query, and the body). **Verified not applicable (2026-10-09):** `POST /dq/runs` takes both in the JSON body (`RunCreate.scope`, `RunCreate.profile_key`), which the hash covers.

**Idempotency**
- Tenant replays return the stored body for 24 h without re-checking object scope after a scope change. See also DL hardening 5. **Fixed in f9e5f22d:** a replay after the caller's access changed answers 409 `idempotency_access_changed`. Tests: `tests/authz/test_http_conventions.py::test_docs_09_replay_refused_after_the_callers_access_changed`.

**Verified answers**
- `answer_text` is free text, with no personal-number screen (notices and memories have one). **Fixed in 3cc7452:** 422 `answer_personal_data` on create and update. Test: `tests/knowledge/test_ask_api.py::test_SEC_016_a_verified_answer_refuses_phone_numbers_and_emails`.
- A creator may also review their own answer. FR-KB-030 allows this. **Fixed in 869721e0** (owner decision 2026-10-09: the drafter of a verified answer cannot review it; 409 `reviewer_must_differ`, unless no other active member holds `kb.verified_answer.manage`, when the review is audited with `self_reviewed: true`; migration `0050_verified_answer_drafter` keeps the drafter, since `verified_by` changes with every review). Tests: `tests/knowledge/test_ask_api.py::test_FR_KB_030_the_drafter_cannot_review_their_own_answer`, `::test_FR_KB_030_self_review_is_allowed_when_no_one_else_holds_the_permission`.

**Notices**
- The principal can approve their own notice draft. This matches FR-NOTICE-005; record it if separation of duties is wanted. **Fixed in 8af59990** (owner decision 2026-10-09: self-approval stays allowed by FR-NOTICE-005, and `notice.approved` carries `self_approved: true` when the approver drafted the notice, so the audit viewer shows it). Test: `tests/circulars/test_api.py::test_FR_NOTICE_005_self_approval_is_allowed_and_marked_in_the_audit_event`.

**Tally**
- Revert of an import silently drops its `ops.tally_party_links` (ON DELETE CASCADE) instead of refusing with `import_has_dependents`. **Fixed in 1fd9b1bd:** the link is NO ACTION, so the revert answers 409 `import_has_dependents`. Test: `tests/imports/test_commit_revert.py::test_FR_IMP_005_revert_refused_when_a_tally_party_is_linked_to_an_imported_student`.

**Students**
- **Past-year access.** Past-year search and past-year enrolment edits reach students who are no longer in the caller's scope. **Fixed in 0b22a08d** (owner decision 2026-10-09: section- and class-scoped grants reach a student only through an active enrolment in their sections or classes in the CURRENT academic year; a past-year-only student is 404 and left out of lists and search for any year asked; school-wide grants see every year. Reads and enrolment edits already needed current-year reach; the year filter on list and search no longer widens it). Tests: `tests/students/test_past_year_reach.py::test_SEC_015_BOLA_a_scoped_grant_does_not_reach_a_past_year_student`, `::test_SEC_015_BOLA_past_year_lists_and_search_leave_out_students_outside_current_reach`, `::test_SEC_015_BOLA_past_year_enrolment_edits_need_current_reach`.
- **Guardian edits.** A guardian shared with a sibling outside scope can be edited by a custom scoped updater. **Fixed in eb51a9e:** name, phone and address change only when every linked student is in reach (403 `guardian_shared_out_of_scope`). Test: `tests/students/test_enrollments_api.py::test_SEC_015_scoped_editor_cannot_edit_a_guardian_shared_with_a_student_outside_scope`.

**Platform**
- **Invoices for closed schools.** Offboarding and deletion leave the subscription live, so monthly drafts continue. **Fixed in 15e18c2e:** offboarding ends the subscription, and closing schools are never invoiced. Test: `tests/platform/test_two_person_requests.py::test_hardening_offboarding_ends_the_subscription_and_its_invoicing`.
- **Manual drafts.** `create_manual_draft` allows overlapping periods. `InvoiceLineIn.unit_price_inr` accepts negative values. **Fixed in 15e18c2e:** overlapping periods are refused, and negative prices are allowed only on adjustment lines. Tests: `tests/platform/test_two_person_requests.py::test_hardening_manual_draft_refuses_an_overlapping_period`, `::test_hardening_invoice_lines_refuse_negative_prices_except_adjustments`.
- **Announcements.** A single `support_agent` can post a critical banner to every school. This is a phishing channel with no second approval. **Fixed in 15e18c2e:** a critical announcement needs a second operator. Test: `tests/platform/test_two_person_requests.py::test_hardening_critical_announcement_needs_a_second_operator`.

**Knowledge**
- A legacy Ask `session_id` from another school fails with a unique violation instead of a fresh id. It is an existence signal for a known UUID. **Fixed in 05fbdae:** the insert runs in a savepoint and falls back to a fresh id. Test: `tests/knowledge/test_conversations_api.py::test_SEC_001_a_legacy_session_id_from_another_school_starts_a_fresh_conversation`.

## Verified clean

- **Mass assignment.** Every request body model (140 models, walked recursively from all 323 routes) has `extra="forbid"`.
- **Route guards:**
  - Every route has exactly one guard; pinned by `tests/security/test_route_enumeration.py`.
  - Body models and guards were re-enumerated from the live app.
- **Role and scope changes take effect on the next request.**
  - `invalidate_on_commit` drops the membership snapshot after commit.
  - Membership status and expiry are read live through `core.resolve_login` on every request, with no cache.
  - Break-glass grants are checked live.
- **Maker-checker:**
  - Change requests and certificates compare `membership_id`.
  - `UNIQUE (tenant_id, user_id)` on memberships rules out a second membership in the same school.
  - DB CHECKs back the service checks.
  - Row locks plus a required `If-Match` serialise a concurrent approve and reject.
  - Expired, cancelled and stale (`request_outdated`) requests are refused.
- **Imports:**
  - Commit and revert lock the batch and check its state.
  - A reverted batch cannot be committed or edited again.
  - The 24 h window is checked under the lock.
  - The worker re-validates in the commit transaction, and edits are refused while committing.
- **Promotions:** commit and undo lock and re-plan; the undo window is checked under the lock.
- **Extraction:** an item is locked `FOR UPDATE`, so a double confirm or a confirm after reject answers 409.
- **Certificates:**
  - One live TC per student.
  - One pending duplicate.
  - Serials come from a locked counter.
  - Approval re-checks blockers.
- **Idempotency:**
  - Tenant keys are scoped to tenant, user, method, path and key, and a different body answers 422.
  - Platform keys are per operator.
  - The tenant and platform namespaces cannot collide.
- **Invitation consent (0045):**
  - Accepting or declining matches `(issuer, subject)` and the membership.
  - Someone else's invitation answers 404.
  - A declined invitation becomes `removed`, which has no transitions, so the school cannot reactivate it. A re-invite answers `duplicate`.
  - The inviting school cannot activate an existing account's invitation (`invitation_needs_consent`).
- **Cursors:** they carry only offsets or keys. Filters and scope are re-applied on the server, and offsets are type-checked.
- **Suspended schools over HTTP:** the BR-08 allowlist holds. Async work is A-12.
- **Platform:**
  - Two-person checks hold at service and DB level.
  - Billing state machine: void, payment and reversal are locked and checked against the balance.
  - Support tickets are filtered by tenant, and internal notes are never shown to schools.
  - Platform outputs carry no student data.
- **Academics, insights, notifications and knowledge:**
  - Every object is reached through scoped services (404 outside scope).
  - Notifications, conversations, memories and feedback are keyed by user or membership.

## Checks run

- `uv run lint-imports`: 38 contracts kept.
- `uv run mypy apps/api apps/worker evals`: no issues (820 files).
- In `apps/api`: `ruff check` and `ruff format --check` are clean.
- `pytest` on tests/api, academics, insights, extraction, certificates, students, changes, imports, platform, security, migrations and core: see the final report.
- OpenAPI and the TS client are regenerated (descriptions only; no route or schema shape changed).
- Web: the en and te catalogs carry the same keys (`src/i18n` vitest); lint, typecheck, vitest and build: see the final report.
- No migration was needed.
