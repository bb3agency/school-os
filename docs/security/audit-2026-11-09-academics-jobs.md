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
| YA-01 | Low (web follow-up XA-06) | attendance, concurrent writers (FR-ATT-002) | `app/academics/service.py` `record_attendance`; `repository.upsert_attendance` | Two people marking the same section and day (class teacher and office) were last-write-wins per student: the second save overwrote the first person's change with the status the second person had loaded. The web client now posts only changed rows (XA-06), which narrows it to the same student, but the API had no way to notice a stale view. | **Fixed** in afdfd050. `GET /sections/{id}/attendance` returns each student's mark `version` (0 when not marked). An entry of `POST /sections/{id}/attendance` may carry `expected_version`. The write first takes transaction advisory locks per school and date (`repository.lock_attendance_days`, in date order, so writers cannot deadlock and nothing commits between the check and the upsert), then a stale version answers **412 `attendance_changed`** and nothing is saved. An entry without `expected_version` keeps last-write-wins (sheet imports, the current web client), so nothing breaks. The version moves only when the status or section changes. Tests: `tests/academics/test_service.py::test_FR_ATT_002_XA_06_a_stale_expected_version_is_412_and_saves_nothing`, `::test_FR_ATT_002_XA_06_concurrent_saves_of_the_same_unmarked_day_cannot_both_win` (also fails with the lock removed). **API schema change** (see below); OpenAPI and client regenerated in a54c904f, web sends the versions since 32a560a3. |
| YA-02 | Medium | worker, retries (FR-OPS-004) | `apps/worker/sos_worker/celery_app.py`; `app/ops/service.py` `EVENT_LEASE_SECONDS` | On Valkey, a reserved message that is not acknowledged within the broker visibility timeout is delivered again. The timeout was the 1 h default. ETA/countdown messages are reserved at once and, with `acks_late`, acknowledged only after they ran. The retry countdowns of `documents.*` (scan, discard) and `fees.tasks_online` (online refund, link cancel) reach `min(..., 3600)`, so a late retry could meet the timeout and run twice: an online refund call sent twice, or a scan run twice. The ops comments assumed 1 h without pinning it. | **Fixed** in e71e6e75. `broker_transport_options={"visibility_timeout": 7200}` (`BROKER_VISIBILITY_TIMEOUT_S`, above the longest countdown plus a 15 min run), and the outbox dedupe lease `EVENT_LEASE_SECONDS` moves from 2 h to 3 h so that it still outlives the timeout. The hold countdown (600 s) stays below it. Test: `apps/worker/tests/test_celery_app.py::test_YA_02_no_countdown_reaches_the_broker_visibility_timeout`. |
| YA-03 | Low | worker, beat (FR-OPS-004) | `apps/worker/sos_worker/celery_app.py` | Beat keeps publishing while no worker consumes. After an outage, every missed run of an interval job (the outbox relay every 5 s gives 720 per hour, plus the 1-minute sweeps for break-glass, breach notices, messaging, the payment inbox and announcements) ran back to back on `maintenance`, ahead of real outbox consumers. Overlap itself is safe: the claims use `SKIP LOCKED` or leases. | **Fixed** in e71e6e75 and 1d136e78. `expiring()` gives every interval beat entry `options.expires` equal to its interval unless it sets one, so a stale run is discarded. Crontab entries are unchanged. Test: `apps/worker/tests/test_celery_app.py::test_YA_03_interval_beat_jobs_expire_instead_of_piling_up`. |
| YA-04 | Medium (lead decision: deferred to the roadmap) | marks (FR-MRK-002) | `app/academics/service.py` `record_marks` | Exams have no lock. Any `marks.record` holder can change an exam's marks at any time, including after results were shared and in a past year (the roster check limits this to current-year students). The audit `marks.recorded` holds counts only (by design, FR-MRK-002), so an old value cannot be recovered from the audit. | **Reported; deferred to the roadmap** (lead decision 2026-11-09: a feature with policy choices). Proposed design: `exams.locked_at`/`locked_by` (migration), `POST /exams/{id}:lock` and `:unlock` (`exam.manage`, audited `exam.locked`/`exam.unlocked`), and `record_marks` answering 409 `exam_locked`. Optionally keep the previous value in an append-only `exam_mark_history` row (C2, purged with the module). The PO decides who may unlock, and whether a correction after the lock goes through maker-checker. |
| YA-05 | Low | marks, concurrent writers (FR-MRK-002) | `app/academics/service.py` `record_marks`; `repository.upsert_marks` | Two people entering one section's marks for one exam had the same last-write-wins problem as YA-01. | **Fixed** in b6a20148 (API), a54c904f (OpenAPI and client) and 32a560a3 (web). The grid returns each mark's `version`, and a `MarkIn` may carry `expected_version` (0: no mark). Under a per-exam transaction advisory lock (`repository.lock_exam_marks`), a stale version gives **412 `marks_changed`** and nothing is saved. Subjects are matched case-insensitively, as for the upsert. Leaving the field out keeps last-write-wins (sheet imports). Web: the marks entry sends each paper's version. On 412 it reloads the grid, keeps the typed marks and says so. Tests: `tests/academics/test_service.py::test_FR_MRK_002_YA_05_a_stale_expected_version_is_412_and_saves_nothing`; vitest `insights.test.tsx` "marks carry each paper's version; 412 reloads the grid and keeps the typed marks". |
| YA-06 | Low | attendance (FR-ATT-001) | `app/academics/service.py` `record_attendance`, `sheets.read_attendance` | A date is checked only against the section's academic year and today, not against the student's enrolment. A student admitted on 1 November (or moved into the section mid-year) can be marked for July in that section. Such marks feed the early-warning rules (chronic absence) and the WhatsApp absence alerts (`absent_students`). After a student leaves, the enrolment is no longer active, so `not_in_section` already refuses dates; there is no end date on an active enrolment. | **Not fixed, back to the lead** (lead asked for 422 `before_enrolment`). Blocker: `started_on` is not a reliable admission date. Every enrolment made without an explicit date, which includes `POST /students` with a section, the spreadsheet import and register extraction, gets `started_on = today` (`students/service.py` `_enrol`: `start = started_on or _today()`). A school that onboards in November with an import of all its students would therefore be refused any backfill of June to October attendance, including the month sheet upload. Proposed: first make imports and the onboarding wizard set `started_on` to the year start (or take an admission date), or check only enrolments whose start date was given explicitly; then add the 422, with the same per-row sheet issue. |
| YA-07 | Low | config guards (SEC-011) | `app/core/config.py` `_guard_postgres_tls` | On the shared tier in staging/prod, a plain `redis://` URL passed the start-up guards. Only a `rediss://` URL without a certificate check was refused. Terraform writes `rediss://…?ssl_cert_reqs=required` (`modules/redis`), so this affected only a hand-set secret. Postgres has the matching shared-tier guard. | **Fixed** in ceca4469. In shared staging/prod, a `redis://` URL to any host other than loopback refuses to start ("SOS_REDIS_URL must be a rediss:// URL"). Dedicated hosts keep plaintext on the compose network, and docs/10 §11 is updated. The loopback default is tolerated because nothing crosses the network and it cannot connect on a Fargate task. Refusing it too would have broken 79 tests in 20 files outside my area that build prod `Settings` without a Redis URL. Tests: `tests/core/test_config.py::test_YA_07_SEC_011_shared_tier_refuses_a_plaintext_valkey_url` (staging and prod, failed before the fix) and `::test_YA_07_dedicated_hosts_keep_plaintext_valkey_on_the_compose_network`. |

## API schema change (YA-01, YA-05)

Regenerated on this branch: `apps/api/openapi.json` and `packages/api-client` (a54c904f).

- `AttendanceDayStudent.version: int` (0 means not marked) and `AttendanceEntryIn.expected_version: int | null` (optional, 0..2^31-1). The second also appears in `AttendanceSheetOut.entries`, always `null` there.
- `MarkOut.version: int` and `MarkIn.expected_version: int | null` (optional). The second also appears in `MarksSheetOut.entries`.
- `POST /sections/{section_id}/attendance` can answer 412 `attendance_changed`, and `POST /sections/{section_id}/exams/{exam_id}/marks` can answer 412 `marks_changed`.

Web follow-up done in 32a560a3:
- `DayForm` sends each changed row's `version` as `expected_version`. On 412 `attendance_changed` it reloads the day and shows `attendance.changedBySomeone`. The other person's statuses are shown, and only the statuses this person chose are kept.
- The marks entry does the same with `marks.entry.changedBySomeone`.
- en and te keys are added (te in English, as for `unsavedLeave`).
- Test: vitest `insights.test.tsx` "a save carries each changed row's version; 412 reloads the day and keeps my changes".

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
- `uv run mypy apps/api apps/worker evals`: no issues (1,244 files).
- `uv run lint-imports`: 52 contracts kept.
- `pytest tests/academics`: 49 passed. The new YA-01 and YA-05 tests failed before their fixes, and the attendance race test also fails with the lock removed.
- `pytest` on the 29 files that build staging/prod `Settings` (config, deploy, devtools, identity, knowledge, documents, extraction, fees, messaging, notifications, platform, public, tenancy, audit): 819 passed. The new YA-07 tests failed before the fix.
- `pytest tests/core/test_openapi_fresh.py`: passed.
- `pytest tests/ops`: 46 passed. `tests/insights/test_api.py`, `test_log_redaction.py` and `tests/security/test_api_contract_guards.py`: 15 passed.
- `pytest apps/worker/tests`: 54 passed. Both new tests failed before the fix.
- Web:
  - `tsc --noEmit`: clean.
  - `eslint --max-warnings=0 src/features/insights`: clean.
  - `prettier --check` on the touched files: clean.
  - vitest `src/features/insights` and `src/i18n`: 73 passed. `src/components/shell` and `src/features/school`: 109 passed. `@schoolos/api-client`: 8 passed.
  - Before the web fix, 6 insights tests failed: the 2 new ones and the 4 body expectations that now carry `expected_version`.
