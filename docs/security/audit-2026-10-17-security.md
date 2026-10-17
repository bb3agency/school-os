# Security audit 2026-10-17: security, isolation and privacy after R1

Branch `wip/audit-security` from `main` (aaa6e0fd). Round four, run next to a business-logic and a
frontend/docs audit. Scope: what changed since round three (`audit-2026-10-06-api-routes.md`):
the R1 work (APAAR consent register, PEN check and transfer-in guard, board and portal readiness
with parent verification slips, boards per school and alongside mode, the import template
library, print jobs for slips and consent forms; migrations `0051` to `0054`), re-checking that
the earlier fixes still hold, and the supply chain. The earlier audit records were read first;
their findings are not repeated. Paths are relative to `apps/api/` unless they start with `apps/`,
`docs/` or `infra/`. Synthetic data only.

**Status:** four findings fixed in the first pass (each with a test that failed first, or tool
evidence). The owner then asked for every finding to be fixed (2026-10-17): SEC-045 and SEC-047
are fixed too, SEC-046 is handed to the R2-0 engineer (rate limits), and SEC-048 cannot be fixed
without breaking lint (no patched `braces` exists; see below). The rest was checked and found
sound.

## Method

- **Baseline:** `pytest tests/security` on the unchanged branch: 2904 passed (RLS catalogue,
  composite FKs, definer allowlist and ownership, column grants, route enumeration, authz matrix,
  BOLA, contract guards, rate-limit routes, tenant isolation). These re-verify the round 1-3
  fixes; all still hold.
- **Manual review** of every R1 route, service and template: `app/apaar/`, `app/dq/readiness*.py`,
  `app/dq/slip.py`, `app/exports/` (print jobs), `app/imports/` (presets, PEN/APAAR row checks),
  `app/students/` (national-id check, PEN guard), `app/tenancy/` (boards, operating mode),
  `app/core/pdf.py` (Telugu parent pages), migrations `0051`-`0054`.
- **Tools:** `pip-audit` over the locked export (no known vulnerabilities), `npm audit --omit=dev`
  (three high, fixed: SEC-041), secret grep over the tree (gitleaks is not installed here; only
  synthetic test keys found), workflow pinning and permissions review, Terraform security groups
  and endpoint policies, Caddy and dedicated compose.

## Fixed

| ID | Severity | Area | Finding and evidence | Fix | Commit | Test |
|---|---|---|---|---|---|---|
| SEC-041 | High (dependency) | Supply chain | `npm audit --omit=dev`: `next` 16.3.6 (GHSA-4jqv-mc3x-m676 and GHSA-mcj8-r9mp-w47p cache poisoning of SSG/ISR pages, GHSA-cjq9-62q9-8jv4 SSRF in image optimisation, GHSA-f87g-xv8r-7p7x metadata route disclosure, two more), `sharp` 0.35.4 (librsvg CVE), `source-map-js` 1.2.1 (event-loop DoS) | Patch releases: `next` and `eslint-config-next` 16.3.8, `sharp` 0.35.5, `source-map-js` 1.2.2. Web typecheck, lint, 1705 vitest tests and `next build` pass | 39817a51 | `npm audit --omit=dev`: 0 vulnerabilities |
| SEC-042 | Low (custom roles) | AuthZ / information leak (API1, SEC-015) | An import row whose PEN or APAAR ID is already held by another student carried that student's id (`student_id` on the `national_id_in_use` row error) whatever the importer's `student.read_basic` scope. `POST /students` names the other record only when the caller may read it; the import did not. A scoped importer could map PEN values to student ids outside their sections | The row error stays (no second record is created); the id is given only for a holder the importer reaches (`imports.service._visible_holders`, through `students.summaries`) | a3f3141a | `tests/imports/test_validation.py::test_SEC_042_a_pen_clash_names_only_a_record_the_importer_may_read` |
| SEC-043 | Low-Medium | Audit (invariant 7, PRV) | `GET /dq/readiness/{profile}/slips?section_id=` shows the Aadhaar-as-printed (C3) values of up to 300 students to a `student.read_sensitive` holder, but its audit event `dq.readiness.slips_printed` held only the section and a count (`sensitive_shown`). The school could not answer whose restricted values a person saw. The single-student view and the PDF export already name their students | The event lists `sensitive_student_ids` (ids only) whenever restricted values were shown; nothing changes when none were | ce5ae953 | `tests/dq/test_readiness_api.py::test_SEC_043_section_slips_audit_whose_restricted_values_were_shown` |
| SEC-044 | Low (custom roles) | AuthZ (SEC-015) | `POST /dq/readiness/{profile}/runs` resolved its students through `student.read_basic` (and `dq.findings.read` when held) only. A member holding `dq.readiness.manage` for one section through a custom role and `student.read_basic` school-wide through another (e.g. accountant) ran checks over the whole school and stored DQ-031 findings for students outside the grant's scope; the run's counts described them | `dq.request_run(..., also=(dq.readiness.manage,))`: the route's own permission joins the structure check (sections and classes outside it are a 422, no existence oracle), the student resolution and the queued-run freeze | 93199770 | `tests/dq/test_readiness_api.py::test_SEC_044_a_scoped_readiness_manager_runs_checks_only_in_their_scope` |

## Second pass: owner decision of 2026-10-17 ("fix all findings")

| ID | Severity | Area | Finding | Resolution | Commit | Test |
|---|---|---|---|---|---|---|
| SEC-045 | Low | Step-up, masking (docs/07 §11, ADR-0021) | The HTML slips of a whole section (and of one student) showed C3 (Aadhaar-as-printed name, date of birth, gender) unmasked by default to a `student.read_sensitive` holder, without step-up, while the PDF of the same slips masks them unless `include_sensitive` is asked for with MFA within 5 minutes; the browser can print the HTML to PDF | **Fixed.** `GET /dq/readiness/{profile}/slips` masks C3 unless `include_sensitive=true`, which needs `student.read_sensitive` reaching every student on the page (403 `sensitive_not_allowed`) and step-up (428), and is audited with `sensitive_student_ids` (SEC-043). `check=true` runs the same checks and answers 204 without a page or audit event. Web: the slip links stay masked; holders of `student.read_sensitive` get "Show Aadhaar details on slips", which checks first (the BFF client handles the 428 with the confirm-it's-you prompt), then links the unmasked view in a new tab (en, te). The student detail screen itself (one student, audited `dq.readiness.student_viewed`) is unchanged | 0be50cf4 (API, OpenAPI, client), 8966728a (web, docs) | `tests/dq/test_readiness_api.py::test_SEC_045_*`, `::test_SEC_043_*` (now asks for the values); `apps/web/src/features/readiness/readiness.test.tsx` ("slips with Aadhaar details (SEC-045)") |
| SEC-046 | Low | Rate limits (API4) | `GET /dq/readiness/{profile}` and `/students` assess every current-year student of the profile's classes on each call with no per-route budget | **Handed to the R2-0 engineer**, who owns `app/core/rate_limits.yaml` this week: add a per-person budget (like `render`) to these two routes | - | - |
| SEC-047 | Info | Privacy / invariant 4 vs PRV-020 | The slip and consent-form templates mask every Verhoeff-valid 12-digit number, so a typed APAAR ID printed there would come out masked for about one in ten students. Neither prints it today | **Guarded** (the mask stays fail-closed): a test fails if a readiness profile compares `apaar_id` (slip rows) or the consent form prints it, pointing to the `TypedDigits12` path the spreadsheet exports use (`tests/exports/test_apaar_export.py`) | 6b0a0899 | `tests/dq/test_print_apaar_guard.py` |
| SEC-048 | Low (dev only) | Supply chain | `npm audit` (dev dependencies) reports GHSA-vfj7-8cjw-p6xm in `braces` ≤ 3.0.3 through `eslint-config-next` → `@next/eslint-plugin-next` → `fast-glob` 3.3.1 → `micromatch` 4.0.8 | **Left reported.** No fixed `braces` exists (3.0.3 is the latest release and is in the affected range), so an npm `overrides` cannot help; every `@next/eslint-plugin-next` up to 16.4.2 and the 16.5 canary pins `fast-glob` 3.3.1, and npm's only offered "fix" is a downgrade to `eslint-config-next` 14.2.35, which does not support ESLint 10 or Next 16. The package runs only in lint, on the repository's own glob patterns, never in an image or on untrusted input. Revisit when `@next/eslint-plugin-next` drops `fast-glob` or `braces` ships a fix | - | `npm audit --omit=dev`: 0 |

## Checked and sound (no finding)

- **Tenant isolation, migrations 0051-0054.** `sis.apaar_consents` and `sis.apaar_consent_settings`
  have `tenant_id` first, `UNIQUE (tenant_id, id)`, RLS ENABLE + FORCE with `tenant_isolation`,
  composite FKs (student, guardian `ON DELETE SET NULL (guardian_id)`, evidence document,
  membership), the ADR-0029 `offboarding_purge` policy and `sos_purger` grants; the register is
  append-only for `sos_app` (UPDATE, DELETE, TRUNCATE revoked; settings: column UPDATE only).
  `sos_platform` has nothing on them; `sos_readonly` SELECT under RLS like other `sis` tables.
  `0052` grants `sos_app` UPDATE on `core.tenants.boards` only (policy `own_tenant` unchanged).
  `0053` is a catalogue upsert; `0054` changes CHECK constraints only (its downgrade lifts FORCE for
  one owner DELETE and restores it in the same transaction). No new definer function.
- **AuthZ and BOLA on R1 routes.** Every APAAR, readiness, print-job, preset and national-id route
  declares its guard and is in the authz matrix and the BOLA suite. Students are reached through
  `students.ensure_in_scope` / `list_students_in_scope` with the route's own permission; sections
  outside reach are 404 (print views) or 422 (run scopes), never an existence oracle; other
  schools' ids are 404. Print jobs re-check every frozen student's reach at download (AA-12) and in
  the worker; `include_sensitive` needs `student.read_sensitive` reaching every student (403).
  `POST /students/national-id-check` needs `student.create` school-wide and returns only records
  the caller reads.
- **Input handling.** Every value in the slip and consent-form templates goes through
  `html.escape` and the Aadhaar mask; the pages have no script, link, image or form, and a
  hash-pinned style CSP (`default-src 'none'`), which the BFF keeps only because it is strict.
  The PDF renderer refuses every request except the checksum-verified Telugu font. Profile source
  URLs must be https (validated); the web opens them with `noopener noreferrer`. The XLSX
  template of a preset is written by `core.spreadsheet.write_xlsx` (strings only, formulas
  neutralised). New SQL uses bound parameters only; the national-id advisory lock hashes a bound
  value. YAML is read with `safe_load`.
- **Privacy.** The consent note refuses a full Aadhaar number (422) and is never logged or
  audited; audit summaries carry ids, codes and counts; the forms print C2 only (name, admission
  number, class, date of birth, PEN). Log-redaction tests for the APAAR and readiness routes pass.
  The consent register is in the full data export and the offboarding purge; print-job files
  follow the export lifecycle (presigned GET at most 300 s).
- **Supply chain and infrastructure.** `pip-audit` clean; every third-party GitHub Action pinned
  by SHA with job-level `permissions`; no `pull_request_target`; Caddy is the first hop (no trusted
  proxies), strips the service-token header and redacts query values; dedicated containers run as
  non-root with `cap_drop: ALL`; security groups open only 80/443 inbound on the dedicated host.

## Verification

- Root: `uv run lint-imports` (40 contracts kept), `uv run mypy apps/api apps/worker evals` (no
  issues in 900 files).
- `apps/api`: `ruff check`, `ruff format --check`; `pytest tests/security tests/migrations
  tests/extraction/test_migration.py tests/imports tests/dq` (counts in the hand-back).
- Web: `npm run typecheck`, `npm run lint`, `npm run test` (1705 passed, 3 skipped),
  `npm run build` after the Next.js update.
- First pass: no API schema change. Second pass (SEC-045): the slips route gained
  `include_sensitive` and `check`; `openapi.json` and the TypeScript client were regenerated.
  No migration in either pass.
