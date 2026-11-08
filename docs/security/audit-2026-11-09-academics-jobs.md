# Audit 2026-11-09: academics, tenancy structure, ops and the worker (YA)

Base `fad0b02c`. Scope: `app/academics` (daily attendance, register, exams and marks),
`app/tenancy` (academic years, classes, sections, the current-year switch,
`academic_defaults.yaml`) with the promotion commit and undo in `app/students`, `app/ops` (job
runs, outbox, idempotency keys), `app/core` (start-up guards, `tenant_session`, errors), and
`apps/worker` (Celery app, beat schedule, routing, retries, `acks_late`, time limits). The
reports of 2026-10-* and 2026-11-08 (round 1 and web round 2) were read first. Their fixed items
are not repeated here.

**Method.** Each fixed finding has a test that failed before the fix and passes after it. The
attendance race test uses two sessions (a thread blocked behind an uncommitted save). Data is
synthetic only. Paths are relative to `apps/api/` unless they start with `apps/`.

## Findings

| ID | Severity | Area | File | Description | Status |
|---|---|---|---|---|---|
| YA-01 | Low (web follow-up XA-06) | attendance, concurrent writers (FR-ATT-002) | `app/academics/service.py` `record_attendance`; `repository.upsert_attendance` | Two people marking the same section and day (class teacher and office) were last-write-wins per student: the second save overwrote the first person's change with the status the second person had loaded. The web client now posts only changed rows (XA-06), which narrows it to the same student, but the API had no way to notice a stale view. | **Fixed** in afdfd050. `GET /sections/{id}/attendance` returns each student's mark `version` (0 when not marked). An entry of `POST /sections/{id}/attendance` may carry `expected_version`. The write first takes transaction advisory locks per school and date (`repository.lock_attendance_days`, in date order, so writers cannot deadlock and nothing commits between the check and the upsert), then a stale version answers **412 `attendance_changed`** and nothing is saved. An entry without `expected_version` keeps last-write-wins (sheet imports, the current web client), so nothing breaks. The version moves only when the status or section changes. Tests: `tests/academics/test_service.py::test_FR_ATT_002_XA_06_a_stale_expected_version_is_412_and_saves_nothing`, `::test_FR_ATT_002_XA_06_concurrent_saves_of_the_same_unmarked_day_cannot_both_win` (also fails with the lock removed). **API schema change** (see below). |
| YA-02 | Medium | worker, retries (FR-OPS-004) | `apps/worker/sos_worker/celery_app.py`; `app/ops/service.py` `EVENT_LEASE_SECONDS` | On Valkey, a reserved message that is not acknowledged within the broker visibility timeout is delivered again. The timeout was the 1 h default. ETA/countdown messages are reserved at once and, with `acks_late`, acknowledged only after they ran. The retry countdowns of `documents.*` (scan, discard) and `fees.tasks_online` (online refund, link cancel) reach `min(..., 3600)`, so a late retry could meet the timeout and run twice: an online refund call sent twice, or a scan run twice. The ops comments assumed 1 h without pinning it. | **Fixed** in e71e6e75. `broker_transport_options={"visibility_timeout": 7200}` (`BROKER_VISIBILITY_TIMEOUT_S`, above the longest countdown plus a 15 min run), and the outbox dedupe lease `EVENT_LEASE_SECONDS` moves from 2 h to 3 h so that it still outlives the timeout. The hold countdown (600 s) stays below it. Test: `apps/worker/tests/test_celery_app.py::test_YA_02_no_countdown_reaches_the_broker_visibility_timeout`. |
| YA-03 | Low | worker, beat (FR-OPS-004) | `apps/worker/sos_worker/celery_app.py` | Beat keeps publishing while no worker consumes. After an outage, every missed run of an interval job (the outbox relay every 5 s gives 720 per hour, plus the 1-minute sweeps for break-glass, breach notices, messaging, the payment inbox and announcements) ran back to back on `maintenance`, ahead of real outbox consumers. Overlap itself is safe: the claims use `SKIP LOCKED` or leases. | **Fixed** in e71e6e75 and 1d136e78. `expiring()` gives every interval beat entry `options.expires` equal to its interval unless it sets one, so a stale run is discarded. Crontab entries are unchanged. Test: `apps/worker/tests/test_celery_app.py::test_YA_03_interval_beat_jobs_expire_instead_of_piling_up`. |
| YA-04 | Medium (needs a product decision) | marks (FR-MRK-002) | `app/academics/service.py` `record_marks` | Exams have no lock. Any `marks.record` holder can change an exam's marks at any time, also after results were shared and in a past year (the roster check limits this to current-year students). The audit `marks.recorded` holds counts only (by design, FR-MRK-002), so an old value cannot be recovered from the audit. | **Reported.** Proposed fix: `exams.locked_at`/`locked_by` (migration), `POST /exams/{id}:lock` and `:unlock` (`exam.manage`, audited `exam.locked`/`exam.unlocked`), and `record_marks` answering 409 `exam_locked`. Optionally keep the previous value in an append-only `exam_mark_history` row (C2, purged with the module). Needs the PO to decide who unlocks and whether a correction after the lock goes through maker-checker. |
| YA-05 | Low | marks, concurrent writers (FR-MRK-002) | `app/academics/service.py` `record_marks`; `repository.upsert_marks` | The same last-write-wins as YA-01 applies to two people entering one section's marks for one exam. | **Reported.** Same pattern as YA-01: `MarkOut.version` plus an optional `expected_version` per `MarkIn`, under a per-exam advisory lock, giving 412 `marks_changed`. Not done in this round to keep the schema change to one route. The web marks grid would need to send it. |
| YA-06 | Low | attendance (FR-ATT-001) | `app/academics/service.py` `record_attendance`, `sheets.read_attendance` | A date is checked only against the section's academic year and today, not against the student's enrolment. A student admitted on 1 November (or moved into the section mid-year) can be marked for July in that section. Such marks feed the early-warning rules (chronic absence) and the WhatsApp absence alerts (`absent_students`). | **Reported** (FR-ATT-001 is "proposed, PO to confirm"). Proposed fix: 422 `before_enrolment` when `on_date < started_on` of the student's active enrolment in the section, and the same issue code in the sheet preview. |
| YA-07 | Low | config guards (SEC-011) | `app/core/config.py` `_guard_production` | On the shared tier in staging/prod, a plain `redis://` URL (or the `redis://localhost` default when `SOS_REDIS_URL` is missing) passes the start-up guards. Only a `rediss://` URL without a certificate check is refused. Terraform writes `rediss://…?ssl_cert_reqs=required` (`modules/redis`), so this affects only a hand-set secret. Postgres has the matching shared-tier guard (`_guard_postgres_tls`). | **Reported.** Proposed fix: in shared staging/prod, require `rediss://` with a verified certificate (dedicated hosts keep plaintext on the compose network). `tests/core/test_config.py` `_prod()` (shared by default, default Redis URL) and `test_SEC_011_prod_accepts_a_verified_or_internal_valkey_url` would need a dedicated-mode variant. Left to the lead because it changes a fixture many config tests share. |

## API schema change (YA-01)

The lead needs to regenerate `apps/api/openapi.json` and the TypeScript client (`make openapi`):

- `AttendanceDayStudent.version: int` (default 0; 0 means not marked).
- `AttendanceEntryIn.expected_version: int | null` (optional, 0..2^31-1). This also appears in
  `AttendanceSheetOut.entries`, always `null` there.
- `POST /sections/{section_id}/attendance` can answer 412 `attendance_changed`.

Web follow-up: `DayForm.save` should send `expected_version` (the `version` it read) for each
changed row, and on 412 `attendance_changed` reload the day and say "Someone else changed this
day. Check their changes and save again."

## Verified clean (no finding)

- **Attendance and marks.**
  - Scope: every route resolves the section through `ensure_section_visible` (404 outside scope). The roster limits writes to current-year students the caller reaches. Scoped recorders cannot overwrite another section's record (A-04).
  - Marks validation: `0 < max_marks ≤ 1000` (schema and config ceiling), `0 ≤ marks ≤ max_marks`, two decimals (`max_digits=6, decimal_places=2`; NaN and Infinity are refused by Pydantic's Decimal). Absent and marks are mutually exclusive in both directions (`absent_has_no_marks`, and absent with 0 is refused). Subjects are NFC, 1..60 characters, Aadhaar-checked, case-folded duplicates refused, and at most 20 per write. The exam must be in the section's year (`exam_other_year`).
  - Exam create: the case-insensitive unique index plus the savepoint give a 422 instead of a 500 under a race (R-13).
  - Audit in the same transaction with counts and dates only. The outbox carries student ids in chunks of 100. Sheets are deleted before they are parsed, and only the uploader's own file is read.
- **Year switch** (`tenancy.set_current_academic_year`). It uses the per-school advisory lock, `If-Match`, and the `one_current_year` partial unique index. A repeat on the current year returns it unchanged, and an archived year gets 409.
- **Promotions** (`students.commit_promotion`, `undo_promotion`).
  - Both run in one transaction with the committed run locked and the enrolments locked (`FOR UPDATE`). A concurrent commit sees fewer closed rows and gets 409 `promotion_plan_changed`, or `promotion_already_committed`.
  - Target structure is locked against archive. The fingerprint check is in place, and the commit takes an `Idempotency-Key`.
  - Undo checks every version and status before it changes anything.
- **Idempotency keys** (`app/authz/http.py`).
  - A different body with the same key gives **422** `idempotency_key_reused`. This is what docs/09 §2 specifies; the 409 that this audit's brief expected is not the contract.
  - Concurrent use of the same key: the `SET NX` pending marker gives 409 `idempotency_in_progress`. A change of grants gives 409 `idempotency_access_changed`.
  - The record is written after the commit and kept for 24 h. The pending marker lasts 60 s and is released on rollback.
  - The `ops.idempotency_keys` purge runs nightly per school.
- **Worker.**
  - Every outbox route maps to a registered task whose base is `ops.service.TenantTask`. This was checked by script over `OUTBOX_ROUTES`: there is no unrouted or plain task.
  - The base class holds work of suspended schools, drops work of deleted schools, and dedupes side-effect tasks per `event_id`.
  - Global settings: `acks_late`, `task_reject_on_worker_lost`, prefetch 1, a 15 min hard and 14 min soft time limit (the full export has 60/55 min), JSON only, and no results kept.
  - Outbox relay: inside the claim with `SKIP LOCKED`, with at most 20 batches of 100 per run.
- **Config guards** (staging/prod). Each of the following refuses to start:
  - `local-dev` key wrapper, `dev-only` secrets, rate limit off;
  - Postgres without `verify-full` (shared tier), `rediss://` without a certificate check;
  - fake email, knowledge, messaging or payment providers;
  - Razorpay OAuth test mode in prod, placeholder invoice supplier, non-https public origins and control-plane URL.
- **`core.db.tenant_session`.** It sets the context transaction-locally with bound parameters, commits or rolls back as a unit, and its deferred mode sets the context on the first connection.

## Outside my area

- `app/documents/tasks.py` and `app/fees/tasks_online.py` retry countdowns go up to 3600 s. YA-02 makes this safe at the broker level. Capping them lower (for example at 1800 s) would also shorten recovery.

## Checks run

- `uv run ruff check` and `ruff format --check` on the touched files: clean.
- `uv run mypy apps/api apps/worker evals`: no issues (1,244 files), after fixing one error in the new worker test (1d136e78).
- `uv run lint-imports`: 52 contracts kept.
- `pytest tests/academics`: 48 passed. Both new YA-01 tests failed before the fix, and the race test also fails with the lock removed.
- `pytest tests/insights/test_api.py tests/insights/test_log_redaction.py tests/security/test_api_contract_guards.py`: 15 passed.
- `pytest tests/ops`: 46 passed.
- `pytest apps/worker/tests`: 54 passed (the 7 in `test_celery_app.py` were re-run after the last change). Both new tests failed before the fix.
- `apps/api/openapi.json` was not regenerated (the lead does it). The OpenAPI freshness test will fail until then.
