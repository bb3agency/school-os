# Audit 2026-11-09: privacy, audit chain, break-glass, disclosure, insights, admin (YP)

Base `fad0b02c`. Round 2, deep per-module pass. Scope: `app/privacy` (DPDP requests and
grievances, access reports, lawful erasure, notices, record classes, the `subjects.yaml`
contributors, `consents_*`, the breach register and `breach_sync`), `app/apaar` (append-only
consent register, ADR-0039), `app/disclosure` (CBSE Appendix IX, frozen snapshots, public
handle), `app/insights` (invariant 14), `app/admin` (retention settings, purge, full export),
`app/audit` (hash chain, verification job, append-only, `record_platform`) and
`app/breakglass`, with their tasks. The earlier reports (2026-10-*, 2026-11-08 round 1 and the
web round-2 reports) were read first; AZ-01, TI-01 and TI-04 are fixed and not repeated.

**Method.** Each fixed finding has a test that failed before the fix and passes after it. The
race tests commit a second transaction at the exact point of the race (a wrapped repository
call, or a row lock held by a second connection). Data is synthetic only. Paths are relative
to `apps/api/`.

## Findings

| ID | Severity | Area | File | Description | Status |
|---|---|---|---|---|---|
| YP-01 | **Medium** (integrity monitoring: false P1 alarms on busy schools, and a checkpoint past unverified events) | Audit chain verification (SEC-007, FR-AUD-004) | `app/audit/service.py` `verify_chain`, `verify_platform_chain`; `app/audit/verification.py` `run`, `_verify_from_checkpoint` | Verification read the chain head and then streamed the events in a second READ COMMITTED statement. An audit event committed in between (any staff action during the 20:45 UTC job, or during an on-demand run) made the walk one event longer than the head: the run stored `ok=false, reason=head_mismatch` and the daily job logged `audit.chain.broken` (P1) for an intact chain. A passing run also re-read the head afterwards and moved the checkpoint to it, so an event appended after the walk became the checkpoint without being verified (later incremental runs re-hash the checkpoint event but not its link to the previous one). The platform chain had the same race. | Fixed in `6b049f99`: the head is read after the walk (`service.verify_live`); a head ahead of the walk is accepted only when every event up to it exists now (one-statement count; a deleted tail is still a break), and the checkpoint is the last event the run verified. Tamper tests unchanged and green. Tests `test_SEC_007_YP_01_full_run_is_not_broken_by_a_concurrent_append[iter_events,get_head]`, `test_SEC_007_YP_01_incremental_run_is_not_broken_by_a_concurrent_append[...]`, `test_SEC_007_YP_01_deleted_tail_behind_the_head_is_still_a_break` |
| YP-02 | **Medium** (support access reopened after the school revoked it; narrow window) | Break-glass emergency access (SEC-029, FR-OPS-004, docs/07 §6.4) | `app/breakglass/repository.py` `list_emergency_without_membership`; `service._retry_emergency_memberships` | The 60 s sync retries the membership of an emergency grant whose first opening failed (operator without school sign-in at that time). It read those grants without a lock. If the school revoked the grant between that read and the retry's update, the revoke committed `revoked`, then the retry opened a new temporary membership and overwrote the status with `active`: the operator got access the school had just closed, and the school's list showed it active again. | Fixed in `e7dcd25b`: the rows are read `FOR UPDATE SKIP LOCKED` (as `list_due` and `list_stale_requests` already were), so a grant being revoked or expired is skipped and a revoke waits for an in-flight retry and then closes its membership. Test `test_SEC_029_YP_02_emergency_retry_skips_a_grant_being_revoked` |
| YP-03 | Low (process; audited) | Privacy grievances (FR-DPDP-016, DPDP s.13) | `app/privacy/service.py` `update_request`; `schemas.PrivacyRequestPatch.status` | `PATCH /privacy/requests/{id} {"status": "withdrawn"}` needs only `privacy.request.manage` (no step-up, no second person, no written answer) for every kind, including a grievance and an access or erasure request that is not yet decided. Closing the same grievance needs `privacy.request.decide`, step-up, someone other than the logger and a written response. One office user can therefore end a complaint against the school without an answer, recorded only as "withdrawn" (event and audit `privacy.request.updated` exist). | Reported (needs a product decision). Proposed: allow `withdrawn` for grievances, access and erasure requests only through the decide path (or require a recorded requester confirmation, e.g. a response text "withdrawn at the requester's request", with `privacy.request.decide` and not the logger). |
| YP-04 | Low | CBSE disclosure files (FR-DISC-008, FR-DISC-011) | `app/disclosure/service.py` `render_files`, `rotate_link` | The HTML file and A4 PDF embed the document links built from the link key current at render time. "Change the link" revokes the old key but does not re-render the snapshot's files, so files downloaded after a rotation (until `files_keep_days` expires them) still point to the dead link: the school posts a page whose document links answer 404. | Reported. Proposed: in `rotate_link`, reset the live snapshot's file columns to `queued` and enqueue `RENDER_EVENT` (the same reset `download_url` does for expired files); about 15 lines plus a test. |

No schema change; no OpenAPI change.

## Verified clean

- **Erasure vs. records kept by law (BR-01).** `execute_erasure` re-plans under the request lock, refuses any class whose outcome is not `erasable` (409 `class_not_erasable`), runs only owners with `eraser: true`, and records counts and refused classes in one audit event. The admission register, certificates, fees, attendance and marks, change requests, APAAR and the audit log are `eraser: false` or not erasable in `record_classes.yaml`; documents erasure never deletes (manual review step; the DB refuses deleting evidence). Double execution is blocked (`decided_by`), maker-checker and step-up apply, and an unverified request is refused.
- **Access report.** Every `subjects.yaml` contributor is resolved (fail closed), sections outside the contributor's classes are dropped, breach rows show counts only, and the privacy module's own section omits free texts that may name other people. Build and release are maker-checker; release once; download within 24 h, step-up.
- **Retention purges.** `admin` retention is bounded per category (non-configurable categories, including `audit_events`, cannot change; `enforced_by: null` for the audit log). Insights purge deletes only notes past 365 days and flags closed more than 365 days ago (open flags kept). Breach purge (D58, TI-04) unchanged. No purge path touches `audit.events` or the admission register.
- **Audit append path.** `record` and `record_platform` serialise on the head row (`FOR UPDATE`) in the caller's transaction; the summary validator rejects personal values.
- **Disclosure snapshots.** Content, digest, format and publisher are frozen by trigger and column grants; withdrawal is final; view counts use one atomic `UPDATE`; published documents are protected by a delete guard and served only for the exact published version.
- **Break-glass windows.** Approval opens a membership with the same expiry (DB CHECK 8 h); sign-in resolution refuses expired memberships; the sweep locks due grants (`SKIP LOCKED`); revoke locks the grant and closes the membership in the same transaction; every transition is audited and the school's approvers are notified; `platform_support` holds no `audit.read`, C3 or write permission.
- **APAAR register.** Append-only with `seq` per student under the student lock and `If-Match`; transitions (`withdrawn` only after `given`), dates, signed form for `given`, Aadhaar refusal in notes.
- **Insights (invariant 14).** No other module reads insights data except the academics event feed into insights and the admin full export / key rotation; every flag and note route checks `insights.read` + `student.read_sensitive` in scope (404 outside), manage routes are school-wide with step-up.

## Outside my area

- None confirmed beyond the items above.

## Checks run

- `ruff check` / `ruff format --check` on touched files: clean.
- `uv run mypy apps/api apps/worker evals`: no issues (1244 files).
- `uv run lint-imports`: 52 contracts kept.
- `pytest apps/api/tests/audit` (176 with `tests/security/test_app_column_grants.py`), `apps/api/tests/platform -k "audit or chain or verify"` (213 with audit), `apps/api/tests/breakglass` (52): all pass.
