# Audit 2026-11-09: student records, imports, extraction, data quality, change requests (YS)

Base `fad0b02c`. This is round 2, a pass over each module in turn. Scope: `app/students`,
`app/imports` (spreadsheet onboarding, cell edits, sheet export, 24-hour revert),
`app/extraction` (register-photo batches, review queue, Aadhaar masking), `app/dq` (rules
engine, findings, matching, portal calendar and readiness reminders), `app/changes`
(maker-checker), the user-management routes of `app/identity`, and the tasks of these modules.
Before this pass we read the earlier reports: 2026-10-* (DL, A, W3), 2026-11-08 round 1 (AA,
BL, DPA, TI) and web round 2 (XS-01..XS-04). We do not repeat items they fixed. Paths are
relative to `apps/api/`. All data is synthetic.

## Findings

| ID | Severity | Area | File | Description | Status |
|---|---|---|---|---|---|
| YS-01 | **Medium-low** (I:L on a record someone checked by hand; needs `import.commit`) | Import revert after later edits (FR-IMP-005, invariant 6) | `app/students/service.py` `plan_import_revert` | The revert refused only when a batch value had been *superseded*, or when a student the batch *created* had changed. Suppose a clerk verifies or rejects a value that the batch added to an **existing** student (`POST /students/{id}/values/{value_id}/verify`). The value stays current, and the existing student's version is not compared, so the revert saw nothing recorded on top of the batch. It then marked the value `rejected`, restored the value it had replaced, and so silently undid the person's later decision. The audit trail only showed `import_reverted`. | Fixed in `4c879b64` (test typing in `eb8a82dc`). Imports always record values `unverified`, so the planner now also refuses with 409 `import_has_dependents` when a batch value's `verification_status` is not `unverified`. Test: `tests/imports/test_commit_revert.py::test_YS_01_FR_IMP_005_revert_refused_when_a_batch_value_was_checked_since[verified\|rejected]` |
| YS-02 | Low | Import revert, overlapping batches (FR-IMP-005) | `app/students/service.py` `record_value` (the no-op for an identical value), `plan_import_revert` | Batch B2 records a value identical to a current value from batch A on the same source. `record_value` returns A's row unchanged, and B2's row is shown as `committed`. If A is then reverted, that value is withdrawn, even though B2 (still inside its own window) "added" it. The data is not lost (it is in B2's file and A's history), but B2's summary no longer matches the record. | Reported. Proposed fix: in `_commit`, keep the ids of the values each row confirmed (for example `import_rows.value_ids`). Then `plan_import_revert` refuses when another committed and not-reverted batch confirmed one of the batch's values. A small schema change, so it is left for the lead. |
| YS-03 | Low | Portal calendar, concurrent link (FR-RDY-010) | `app/dq/portal_tasks.py` `link_task` | `confirm` puts the first insert in a savepoint and turns the `portal_calendar_dates_once` race into 412 `version_mismatch`. `link_task` inserts without one, so two people who link a circular task to the same unconfirmed event at the same moment get a 500. `link_task` also replaces an existing confirmation without `If-Match`, unlike `PUT /dq/calendar/{key}`. | Reported. Proposed fix: reuse `confirm`'s savepoint and constraint check, and take an optional `If-Match` on the link route. |
| YS-04 | Low | Extraction batches, double submit (FR-IMP-020) | `app/extraction/service.py` `create_batch` | `documents_in_open_batches` is a read without a lock. Two clerks who submit the same register photo at the same moment can both pass it. The result is two batches, the OCR run twice (provider cost), and two review rows for every register line. The second confirm of the same line is then refused only as a duplicate admission number, or it adds the values twice to an existing student. | Reported. Proposed fix: lock the documents (`FOR SHARE` on their `kb.documents` rows, or a transaction advisory lock per document id) before the check. Another option is a partial unique index on `extraction_pages (tenant_id, document_id)` for open batches. |

No schema change and no OpenAPI change: YS-01 only adds one more case of an existing 409 code.

## Verified clean

- **Maker-checker (FR-CR-001..005, SEC-014).**
  - `approve` and `reject` compare memberships, and `core.memberships` is unique per `(tenant_id, user_id)`. A re-invited maker gets a new membership, and the old one fails `is_active_member`.
  - The rest of the approve path:
    - the route needs step-up and the service checks MFA again (5 minutes);
    - `If-Match` is required (412);
    - the request row is locked;
    - the student is locked before `old_value_id` is compared (`request_outdated`);
    - the pinned evidence version must be scanned clean and visible to the approver;
    - an expired request is refused.
  - On submit, evidence of purpose `evidence` that the maker can see is required. Reason and note refuse a full Aadhaar number. C3 values are encrypted and masked in lists. The memo escapes every value and shows C3 only with `read_sensitive` in scope.
- **Invariant 6.**
  - `_identity_guard` refuses decided identity writes, replacing an admission-register value, and superseding a verified value. All 7 identity attributes anchor on `admission_register`.
  - `verify_value` refuses identity attributes.
  - The DQ resolve of a blocker needs a school-wide waive grant plus step-up (DL-06). Resolving through a change request requires that request to be approved and to cover the same student and field.
- **Invariant 4.**
  - `validate_value` refuses a full Aadhaar number before anything else (DPA-03 separators). The typed APAAR and PEN paths are exact digits only.
  - The student body guards cover JSON bodies, and search has its own guard.
  - The import sheet masks headers and cells, cell edits refuse a full Aadhaar number, and the old value in the edit history is masked.
  - Sheet export masks every cell in `core.spreadsheet.safe_cell`. Template names and calendar labels are checked.
  - Extraction masks provider output in memory and redacts or discards flagged pages. Confirm refuses masked placeholders, and the reject reason is an enum.
  - Logs carry ids and counts only.
- **Class-teacher scope.**
  - `search`, `list_students_in_scope`, `summaries`, `get_profile`, `value_history` and `check_national_ids` (only matches the caller can read) all limit scoped holders to their current-year sections.
  - C3 values are shown only with `read_sensitive` in scope (`_can_reveal`).
  - Change-request reach intersects the request or approve grant with `read_basic`.
- **Import revert.**
  - The 24-hour window is checked against database time.
  - Students are locked first (BL-02). Created students' versions are compared.
  - Later records that point at a created student (attendance, change requests, guardians, Tally links) refuse the delete with 409.
  - A replaced verified or register identity value refuses the revert. Restores go through `record_value`, so the national-ID guard applies.
- **Celery tasks.**
  - The import commit runs only from `committing`, in one transaction with `acks_late`, so a redelivery after commit is a no-op.
  - Change-request expiry runs in bounded, locked batches.
  - Portal reminders are idempotent per (year, event, lead day) through `portal_reminders_once` in a savepoint.
- **DQ determinism.** The checks read no wall clock and sort duplicate lists explicitly (`student_label`, id).

## Outside my area

None found.

## Checks run

- `uv run pytest tests/imports/test_commit_revert.py tests/students/test_service.py`: 35 passed. The new test failed before the fix: 2 failed, with the batch reverted.
- `uv run ruff check` and `ruff format --check` on the touched files: clean.
- `uv run mypy apps/api apps/worker evals`: no issues (1244 files).
- `uv run lint-imports`: 52 kept, 0 broken.
