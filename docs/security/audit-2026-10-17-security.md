# Security audit 2026-10-17: security, isolation and privacy after R1

Branch `wip/audit-security` from `main` (aaa6e0fd). Round four, run next to a business-logic and a
frontend/docs audit. Scope: what changed since round three (`audit-2026-10-06-api-routes.md`):
the R1 work (APAAR consent register, PEN check and transfer-in guard, board and portal readiness
with parent verification slips, boards per school and alongside mode, the import template
library, print jobs for slips and consent forms; migrations `0051` to `0054`), re-checking that
the earlier fixes still hold, and the supply chain. The earlier audit records were read first;
their findings are not repeated. Paths are relative to `apps/api/` unless they start with `apps/`,
`docs/` or `infra/`. Synthetic data only.

**Status:** four findings fixed (each with a test that failed first, or tool evidence), four
reported for an owner decision or for the R2-0 engineer, the rest checked and found sound.

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

## Reported (owner decision, or owned by another engineer)

| ID | Severity | Area | Finding | Recommendation |
|---|---|---|---|---|
| SEC-045 | Low (owner decision) | Step-up, masking (docs/07 §11, ADR-0021) | The HTML slips of a whole section show C3 (Aadhaar-as-printed name, date of birth, gender) unmasked by default to a `student.read_sensitive` holder, without step-up, while the PDF of the same slips masks them unless `include_sensitive` is asked for and needs MFA within 5 minutes. docs/07 §11 says C3 values are masked in the UI by default and included in exports only on request with step-up. The browser can print the HTML page to PDF, so the PDF rule is easy to step around. The audit now names the students (SEC-043) | Mask C3 on the section print unless `include_sensitive=true`, and then require step-up (the single-student detail can stay as it is, audited). Needs a web change (a "show restricted details" choice on the print button) |
| SEC-046 | Low (R2-0 owner) | Rate limits (API4) | `GET /dq/readiness/{profile}` (summary) and `/students` assess every current-year student of the profile's classes on each call, with no per-route budget (only the general per-person and per-school layers). One person can keep the database busy for the whole school | Add a `render`-like per-person budget for these two routes in `app/core/rate_limits.yaml` (owned by the R2-0 engineer this week; not edited here) |
| SEC-047 | Info (owner decision) | Privacy / invariant 4 vs PRV-020 | The slip and consent-form templates pass every value through the Aadhaar mask (`mask_aadhaar`), so an APAAR ID (12 digits) that happens to pass the Verhoeff check (about one in ten) would be printed masked if a profile ever shows it. Today neither page prints the APAAR ID, so nothing is wrong yet. PRV-020 says only the typed `apaar_id` field is shown unmasked | Keep it this way (fail closed); if a slip ever needs the APAAR ID, print the typed field outside the mask, as the student record does |
| SEC-048 | Low (dev only) | Supply chain | `npm audit` (with dev dependencies) still reports `braces` through `eslint-config-next` → `@next/eslint-plugin-next` → `fast-glob` → `micromatch`; the fix needs a major upgrade (`npm audit fix --force`). Lint tooling only, never in the image | Take it with the next `eslint-config-next` major; nothing runs it on untrusted input |

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
- No API schema change: OpenAPI and the TypeScript client are unchanged. No migration.
