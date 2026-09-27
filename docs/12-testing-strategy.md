# 12 · Testing Strategy & Quality Gates

| Field | Value |
|---|---|
| Version | 0.4 · 2026-09-27 |
| Related | 03-TRD §9, 06-RAG §13, 07-Security §15, 13-Engineering standards, 14-Roadmap (M1 status), 16-Platform admin panel §18 |
| Changes | 0.4: M1 suites as built (§4.0.1): per-resource BOLA and scope, maker-checker, no personal data in URLs, deploy contract and dev OIDC stub pinning, worker wiring, notification catalog guard, system-role fingerprint, control-plane boundaries, web vitest (EN/TE parity, CSP), Playwright smoke and axe; how to run each (§4.0.2); what gates CI today versus the target (§9); §2 E2E and accessibility rows match the code. 0.3: implemented suites with file paths (§4.0); §4.1/§4.5/§4.8/§4.12 match the code (catalog files, allowlist keys, grants checked); tools not yet in use marked in §2. 0.2: Valkey and SeaweedFS in integration tests; RLS catalog test with `definer_access` allowlist and platform exemption (§4.5); new suites §4.8–4.13 (platform privilege separation, definer allowlist, composite FKs, audit sequence concurrency, admin panel authz matrix, heartbeat); `mfa_required` in the authz matrix. 0.1: baseline |

---

## 1. Principles

- **Tests encode the invariants.** Tenant isolation, authorization, no-Aadhaar and audit rules are enforced by automated tests, not by memory.
- **Synthetic data only** outside production. No real student data in fixtures, screenshots, logs or AI sessions.
- **Requirement IDs in test names** (e.g., `test_FR_CR_002_self_approval_forbidden`) for traceability.
- **Fast feedback:** unit and integration suites run on every PR in < 10 minutes; heavier suites nightly.

## 2. Test pyramid and tooling

| Layer | Scope | Tools | When |
|---|---|---|---|
| Unit | Pure functions: name normalization, match classes, Verhoeff redaction, canonical resolution, chunking, RRF fusion, crypto helpers | pytest, Hypothesis (property-based), vitest | Every PR |
| Integration | Services + real Postgres (pgvector) + Valkey + SeaweedFS (S3), same images as local (ADR-0014) | pytest + testcontainers, factory_boy | Every PR |
| API | Routes with auth, validation, errors, authz | pytest + httpx TestClient | Every PR |
| Security suites | Route enumeration, authz matrix, BOLA, cross-tenant, RLS catalog, log redaction | pytest (generated cases) | Every PR |
| Contract | OpenAPI freshness (committed `apps/api/openapi.json` equals the app's document) | pytest (`tests/core/test_openapi_fresh.py`) | Every PR |
| Contract (planned) | OpenAPI conformance and fuzzing | Schemathesis (not yet added) | Every PR (fast), nightly (deep) |
| Migrations | Upgrade/downgrade on seeded DB; RLS present on new tables | pytest + Alembic | Every PR touching migrations |
| Frontend | Components, forms, i18n keys | vitest + React Testing Library | Every PR |
| E2E | Critical journeys in a browser (as built: signed-out smoke `apps/web/e2e/smoke.spec.ts`; no M1 journeys yet, 14 · M1 status) | Playwright | Nightly (`make e2e`) |
| Accessibility | Automated WCAG 2.2 AA checks and keyboard-only paths at 1366×768 (`apps/web/e2e/a11y.spec.ts`; signed-in school and platform pages need `E2E_STAND_IN=1`) | axe-core via `@axe-core/playwright` | Nightly + release |
| Deploy contract | Deploy files vs the settings contract and the dev OIDC stub (§4.0.1) | pytest reading repository files (no Docker, Terraform or network) | Every PR |
| Visual/print | PDF and print views (Telugu rendering) | Playwright screenshots + PDF snapshot diff | Nightly + release |
| Performance | NFR-PERF targets | k6 or Locust (staging) | Weekly + before release |
| RAG evaluation | Retrieval, faithfulness, citations, leakage, injection | `evals/` harness | PR subset when knowledge changes; full nightly |
| Security scanning | SAST, deps, secrets, IaC, images, DAST | Semgrep, pip-audit, npm audit/OSV, gitleaks, Trivy, OWASP ZAP baseline | Every PR / nightly (ZAP) |
| Resilience | Provider outages, Valkey loss, slow DB, missed heartbeats, control plane down | Fault injection in staging (toggle-based) | Monthly |

## 3. Synthetic data

- `make seed-synthetic` generates one or more tenants with: classes Nursery–XII, 2,000 students, realistic Telugu and English names (surname-first, initials, variants), guardians, per-source values with **deliberate mismatches** at known rates (spacing, initials, variant spellings, DOB off-by-one, missing fields), sample register page images (rendered), circulars in EN/TE, minutes, fee policy.
- Aadhaar-like test numbers are generated as **invalid** Verhoeff sequences except in dedicated redaction tests, which use valid-checksum synthetic numbers that never leave the test process.
- Datasets are versioned; tests reference them by version to keep results stable.

## 4. Security test suites (must pass on every PR)

### 4.0 Implemented suites (M0, 2026-09-26)

Test names carry requirement IDs. Paths are relative to `apps/api/tests/`. `make test-security` runs `security/`; `make test-api` runs everything (CI jobs `test (test-api)` and `authz-suite`).

| Suite | File(s) | What it proves |
|---|---|---|
| RLS catalog + self-test | `security/test_rls_catalog.py` | Every table in `core`/`sis`/`kb`/`audit`/`ops` with `tenant_id` has ENABLE + FORCE RLS and `tenant_isolation` (or its allowlisted variant); global tables must be listed; `definer_access` only on allowlisted tables; the checker itself flags a deliberately bad table (`test_SEC_001_catalog_check_detects_a_bad_table`); no role is superuser/BYPASSRLS/CREATEROLE/CREATEDB; runtime roles cannot become `sos_owner`/`sos_definer`/`sos_migrator` |
| Definer allowlist | `security/test_rls_catalog.py`, `security/test_definer_functions.py`, `platform/test_schema_platform.py`, `api/test_accept_invitations.py` | Every `SECURITY DEFINER` function is on `rls_allowlist.yaml`, owned by `sos_definer`, pins `search_path`; `0003` functions have exact EXECUTE grantees and `search_path=pg_catalog, pg_temp`; `sos_definer` is NOLOGIN/NOBYPASSRLS and lost `CREATE` on `core`; behaviour of each function (active-only login, invite guards, legal status transitions, no activation without a key, counts only, own-tenant subscription, owner-invite guards, invitation acceptance rules) |
| Privilege separation | `security/test_rls_catalog.py`, `platform/test_schema_platform.py`, `tenancy/test_schema.py`, `audit/test_append_only.py` | `sos_platform` has no SELECT/INSERT/UPDATE/DELETE on any tenant table (catalog and live `permission denied`); `sos_app` reads only `platform.feature_flags`, `sos_readonly` nothing in `platform`; narrowed `sos_app` grants on `core.tenants`/`core.users`/`core.tenant_keys`; audit privilege catalog |
| Composite FKs | `security/test_composite_fks.py` | Every tenant→tenant FK includes `tenant_id`; referenced tables have `UNIQUE (tenant_id, id)`; referencing another tenant's row by known ID fails |
| Tenant isolation (generic) | `security/test_tenant_isolation.py`, `tenancy/test_schema.py`, `core/test_tenant_session.py`, `audit/test_record.py` | Every tenant table the app can read returns zero rows without context (new tables covered automatically); cross-tenant reads/writes by ID fail; `WITH CHECK` refuses foreign `tenant_id`; concurrent sessions do not leak context |
| Route enumeration | `security/test_route_enumeration.py`, `platform/test_authz_matrix.py`, `core/test_health.py` | Exactly one guard per route: `require()` with a catalog permission, `require_platform()` only under `/api/v1/platform/`, `require_fleet_signature()` only on `POST /api/v1/fleet/heartbeat`; the tenantless allowlist is exactly `/me/schools`, `/me/accept-invitations`, `/me/active-tenant`, `/me/login-event`; step-up flags agree with the catalog (every mutating use of a step-up permission requires it); guard permissions exist in `core.permissions`; only `/healthz`, `/readyz` and docs are unguarded; dedicated mode mounts no `/platform` or `/fleet` routes |
| Generated authz matrix | `security/test_authz_matrix.py` | For every (system role, tenant route) pair: 2xx when `roles.yaml` grants the permission, 403 otherwise; the matrix must cover every protected route |
| MFA on every route | `security/test_authz_matrix.py` (`test_FR_IAM_002_privileged_roles_need_mfa_on_every_route`), `api/test_me.py`, `identity/test_principal.py` | owner/principal/office_admin without the MFA claim get `403 mfa_required` on every tenant route; operators without MFA are refused |
| Step-up `428` | `security/test_authz_matrix.py` (`test_SEC_005_step_up_routes_need_recent_mfa`), `identity/test_principal.py`, `core/test_errors.py`, `api/test_users.py` | Stale, missing, future or non-MFA `auth_time` → `428 step_up_required` |
| Platform authz matrix | `platform/test_authz_matrix.py` | Catalog equals the documented matrix (16 §6); every control-plane route × every platform role; staff tokens → 401; unknown/deactivated operators → 403; invited operator activated on first MFA sign-in; beat schedule per deployment mode |
| BOLA | `security/test_bola.py` | Every ID route is covered; other school's IDs → 404; out-of-scope class/section → 404; lists never show another school; bodies cannot reference another school's IDs |
| Audit tamper, append-only, concurrency | `audit/test_tamper.py`, `audit/test_append_only.py`, `audit/test_record.py`, `audit/test_platform_chain.py`, `audit/test_partitions.py`, `audit/test_archive.py`, `audit/test_verify_all_and_tasks.py`, `audit/test_verify_all_cli.py`, `audit/test_summary_and_hashing.py` | Modified/deleted/reordered/duplicated/forged events and head mismatches are detected; UPDATE/DELETE/TRUNCATE blocked even for the owner, on parent and partitions; concurrent writers get contiguous `seq` (tenant and platform chains); rollback leaves no event; partitions secured and runway; signed archive; canonical hashing vectors; summary validation |
| Redaction (incl. property tests) | `core/test_redaction.py` (Hypothesis), `devtools/test_fake_ids.py` | Every valid Verhoeff 12-digit number is masked in any surrounding text (ASCII, Telugu, Devanagari digits); masking is idempotent; invalid numbers without context untouched; phones/emails masked; synthetic Aadhaar-like IDs always fail Verhoeff |
| Log capture | `core/test_logging.py`, `core/test_telemetry.py`, `api/test_log_redaction.py` | Seeded PII never reaches log output; unknown fields dropped; exceptions log types only outside local; span attributes allowlisted; API calls log no names or emails |
| Heartbeat HMAC | `platform/test_heartbeat.py` | Valid heartbeat accepted; bad signatures 401 and nothing stored; replay 409, extra fields/oversize 422, id mismatch 401, rate limit 429; key rotation overlap; staleness → `unreachable`; degraded on stale backup; client payload accepted; schema has no free text; fuzzed unknown fields rejected (Hypothesis) |
| Invoice numbering concurrency | `platform/test_billing.py` (`test_FR_PLT_016_numbers_are_sequential_and_gap_free_under_concurrency`), `platform/test_schema_platform.py` | Concurrent issues produce consecutive, unique, gap-free numbers ≤ 16 characters; issued invoices and published plans frozen |
| OpenAPI freshness | `core/test_openapi_fresh.py` | Committed `apps/api/openapi.json` equals the generated document |
| Migrations | `migrations/test_migrations.py`, `migrations/test_migrations_populated.py`, `authz/test_seed_migration.py` | Upgrade → downgrade → upgrade on a fresh database; every revision walked down and back up on a **populated** synthetic database (one school with staff, roles, structure and audit events seeded through the real services; CLAUDE.md §6.12, §8); `core.permissions` equals the YAML catalog |

At the end of M0 the M1 items of §4.3, §4.6 and §4.7 were still open; §4.0.1 lists the suites that now cover them.

### 4.0.1 Suites added in M1 (2026-09-27)

Same conventions as §4.0. New tenant tables are picked up automatically by the RLS catalog, generic isolation, composite-FK, route-enumeration and authz-matrix suites above; the table lists what M1 added on top.

| Suite | File(s) | What it proves |
|---|---|---|
| Per-resource BOLA and scope (SEC-015, SEC-001, §4.3) | `security/test_bola.py` (generic ID-route sweep plus student, change-request, extraction and export sections), scope tests in `students/`, `imports/`, `changes/`, `documents/`, `exports/` (`test_SEC_015_*`) | Every ID route is covered (`test_SEC_015_every_id_route_is_covered`); another school's or a random ID → 404 and nothing changes; class/section outside scope → 404; lists, search bodies and request bodies never reach another school or another section; document ACLs fail closed |
| Export access (ADR-0021) | `security/test_authz_matrix.py` (`test_ADR_0021_*`), `exports/test_access_migration.py`, `exports/test_api.py` | Who may list, read and download whose exports; step-up on every export request and download-any; C3 columns only with explicit inclusion and permission |
| Maker-checker (SEC-014, §4.7) | `changes/test_schema.py`, `changes/test_service.py`, `changes/test_api.py`; `breakglass/test_breakglass.py` | Self-approval refused by the API **and** by the DB CHECK (even for the owner role); approval needs MFA ≤ 5 min; rejection needs a reason; decided requests frozen; expiry; failed decisions leave no audit event; break-glass self-approval and unconfirmed emergencies refused by the DB |
| No personal data in URLs (SEC-008) | `security/test_no_pii_in_urls.py` | No query parameter in `openapi.json` looks like personal data or free text (explicit `NOT_PERSONAL` allowlist with reasons); legacy personal parameters are deprecated; student search takes personal data only in the body; the dedicated-tier Caddy access log redacts personal query parameters |
| Aadhaar and log capture over M1 pipelines (SEC-013, SEC-008, §4.6) | `imports/` (`test_SEC_013_*`, `test_SEC_008_*`), `extraction/` (`test_FR_IMP_022_PRV_016_*`, `test_PRV_016_*`, `test_log_redaction.py`), `exports/test_tables.py` (Hypothesis: no Verhoeff-valid number survives any cell), `students/test_log_redaction.py`, `changes/test_log_redaction.py`, `exports/test_log_redaction.py`, `documents/` and `breakglass/` (`test_SEC_008_*`) | A full Aadhaar number is refused at input, masked in extracted text and export cells, blacked out of stored page images (re-read to check), and seeded names, DOBs, phones and file names never reach logs. Prompt paths follow in M2 |
| C3 field encryption (SEC-012) | `students/test_crypto.py`, `tenancy/test_key_wrapping.py`, C3 cases in `imports/`, `changes/`, `exports/` | Ciphertext format and round trip; AAD binds tenant, table, column and row (swapped or tampered ciphertext fails); key cache ≤ 15 min; missing key version fails closed; blind index per tenant and purpose; C3 values stored only as ciphertext and masked in API, memo and exports unless permitted |
| Upload controls (SEC-016) | `documents/test_filetypes.py`, `test_scanning.py`, `test_storage_s3.py`, `test_storage_origin.py`, `test_documents_api.py`, `test_tasks.py` | Magic-byte allowlist, polyglots and renamed files refused; presigned POST with size range, short expiry and SSE-KMS condition; intents bound to user and school; ClamAV `INSTREAM` verdicts, outage never "clean"; quarantine audited; dev scanner refused in staging/prod |
| Formula injection (SEC-017) | `exports/test_tables.py` (incl. property test), `imports/test_sheet.py`, `imports/test_validation.py` | No exported cell can start a formula (CSV and XLSX, written as text); imported formulas are never evaluated and cached results ignored |
| Module migrations with data | `students/test_migration.py`, `imports/test_migration_schema.py`, `dq/test_migration.py`, `changes/test_migration.py`, `extraction/test_migration.py`, `exports/test_migration.py`, `exports/test_access_migration.py` | Each M1 revision round-trips with rows present and its models match the database (CLAUDE.md §6.12), in addition to `migrations/` |
| NFR timings | `imports/test_retention_performance.py` (FR-IMP-006), `dq/test_performance.py` (NFR-PERF-005), `students/test_search_performance.py` (FR-STU-011, incl. index use) | 2,000-row validation ≤ 60 s; DQ on 2,000 students ≤ 2 min; search p95 ≤ 300 ms |
| DQ precision (M1 exit) | `dq/test_precision.py`, `dq/test_matching_*.py` | Precision ≥ 0.95 on labelled synthetic sets for blocker/high rules; match classes by table, generated and property tests |
| PDF rendering | `exports/test_pdf.py` | Real headless-Chromium render with bundled Noto Sans Telugu; no request leaves the renderer; skipped only when Chromium is not installed |
| Deploy contract and dev OIDC stub | `deploy/test_env_contract.py`, `deploy/test_dev_oidc_stub.py`, `deploy/test_provision_dedicated.py` | Every variable Terraform or `deploy/dedicated/compose.yaml` passes is a known `Settings` name and each container passes the staging/prod start-up guards; the stub runs only in the `dev` compose profile on loopback, no deploy file mentions it, and every stub issuer is refused in staging/prod; host-side dedicated provisioning is idempotent and resumable |
| Worker wiring | `apps/worker/tests/test_wiring.py`, `test_celery_app.py`, `test_exports_routing.py` | Every outbox route names a registered task on a consumed queue and its consumer accepts the dispatcher's `tenant_id`, `event_id`, `payload`; every enqueued event has a consumer; every beat entry and every registered task is routed to a consumed queue |
| Notification catalog guard (FR-NOT-001) | `notifications/test_templates.py` | Every notification the code sends (found by walking the source) has an EN and a TE template with exactly the declared parameters; ICU plural/select forms render in both languages |
| System-role fingerprint (ADR-0022) | `authz/test_system_role_fingerprint.py` | A change to `roles.yaml` (keys, names, grants) fails until the pinned fingerprint is updated, so the release notes must call for `sync_system_roles` |
| Control-plane boundaries (ADR-0020) | `platform/test_boundaries.py`, `platform/test_tenant_audit_outbox.py` | `platform` calls `tenancy.service` only for lifecycle, imports only pinned tenant-side modules, opens `tenant_session()` only in pinned files and names only pinned tenant relations; school-chain copies are delivered exactly once, in order |
| Suspended schools | `authz/test_suspended_allowlist.py`, `api/test_suspended_school.py` | Only the pinned allowlist answers for a suspended school; everything else is `403 tenant_suspended` |
| Docs-pinned catalogs | `authz/test_catalog.py`, `breakglass/test_guard_and_catalog.py` (docs/07), `dq/test_rules.py`, `dq/test_explanations.py` (docs/02), `core/test_config.py` (docs/10 §11) | Permission, rule and settings catalogs in code equal the tables in the docs |
| Web unit (vitest) | `apps/web/src/i18n/messages.test.ts`, `lib/security-headers.test.ts`, `app/client-boundary.test.ts`, `proxy.test.ts`, `server/**/*.test.ts`, `features/**/*.test.tsx`, `lib/aadhaar.test.ts` | EN and TE catalogues have the same keys and ICU arguments, Telugu is really translated, English headings in sentence case; CSP uses a per-request nonce with `strict-dynamic` and matches the documented directives, the files origin is allowed only for images and presigned uploads; server components never call client functions; BFF session encryption, refresh rotation and proxying; every M1 screen renders its states; Aadhaar input check in the browser |
| E2E and accessibility (Playwright) | `apps/web/e2e/smoke.spec.ts`, `apps/web/e2e/a11y.spec.ts` | Console pages redirect to sign-in keeping the return path; no CSP violations; Telugu switch; the BFF never answers without a session; axe WCAG 2.2 AA and keyboard-only paths on the signed-out page, and with `E2E_STAND_IN=1` on school pages, the school picker and platform pages |

Still open: `TRUNCATE` in the platform privilege catalog check (§4.8 checks SELECT/INSERT/UPDATE/DELETE); browser journeys for the M1 flows (import → findings → change request → pre-check); print/PDF snapshot diffs; Schemathesis; prompt-path redaction and every RAG suite (M2).

### 4.0.2 How to run

| What | Command | Needs |
|---|---|---|
| Everything CI runs | `make check` | Docker (testcontainers), Node |
| All Python tests with coverage | `make test-api` (`uv run pytest --cov …`; `testpaths` = `apps/api/tests`, `apps/worker/tests`) | Docker, or `SOS_TEST_ADMIN_DATABASE_URL` plus `psql`; database tests fail (never skip) without one |
| Security suites only | `make test-security` (= `uv run pytest apps/api/tests/security -q`) | Docker |
| Migration round trips | `make migration-check` (= `uv run pytest apps/api/tests/migrations -q`) | Docker |
| One module or ID | `uv run pytest apps/api/tests/dq -q`, `uv run pytest apps/api/tests -k FR_CR_002 -q` | Docker for tests marked `db` |
| Repository-file checks | `uv run pytest apps/api/tests/deploy apps/worker/tests apps/api/tests/authz/test_system_role_fingerprint.py -q` | Nothing else (no Docker, Terraform or network) |
| PDF render | `uv run pytest apps/api/tests/exports/test_pdf.py -q` | Chromium under `PLAYWRIGHT_BROWSERS_PATH` (skipped otherwise) |
| Web unit | `make test-web` (= `npm test`, vitest) | Node |
| E2E | `make e2e` (= `npm run e2e`) after `next build`, or with `E2E_BASE_URL` pointing at a running stack; `E2E_STAND_IN=1` for signed-in pages | Valkey at `REDIS_URL` for the stand-in |
| Lint, types, scans | `make lint`, `make typecheck`, `make security` | uv, Node; for `make security` the scanners on PATH (CI installs them with `.github/actions/install-tools`) |

Test names carry requirement IDs, so `-k SEC_015` or `-k FR_IMP_005` selects a requirement across modules.

### 4.1 Route enumeration
Iterate FastAPI's route table; assert every route (except allowlisted health checks) has exactly one of: `require()` with a permission that exists in `core.permissions`; `require_platform()` with a permission whose catalog entry has `is_platform: true` in `apps/api/app/authz/permissions.yaml` (control-plane routes, which must live under `/api/v1/platform/`); or `require_fleet_signature()` (only `POST /api/v1/fleet/heartbeat`). Routes that need no resolved school are pinned by a tenantless allowlist (`/me/schools`, `/me/accept-invitations`, `/me/active-tenant`, `/me/login-event`). Also assert that with `SOS_DEPLOYMENT_MODE=dedicated` no `/api/v1/platform/*` or `/api/v1/fleet/*` route is mounted.

### 4.2 Authorization matrix
Generated from the role defaults (07 §6.2): for each (role, permission-protected endpoint) pair, call the endpoint as a user with only that role. Expect success for granted permissions, `403`/`404` otherwise. Include step-up cases (`428` when `auth_time` is older than 5 minutes or `sos:mfa` is missing), MFA cases (`403 mfa_required` for owner/principal/office_admin without `sos:mfa`; ADR-0018) and scope cases (class teacher inside vs outside their section).

### 4.3 BOLA per resource
For students, guardians, documents, findings, change requests, imports, exports: user in section 9A requests a resource belonging to 9C → `404`; resource IDs taken from another tenant → `404`.

### 4.4 Cross-tenant isolation
Two synthetic tenants with overlapping names. For every read path (lists, detail, search, exports, knowledge ask, document download URLs, audit viewer): tenant A never sees tenant B data. Also direct SQL tests as `sos_app`: with `app.tenant_id` unset, every tenant table returns zero rows; with tenant A set, inserting a row with tenant B's ID fails the policy's `WITH CHECK`.

### 4.5 RLS catalog test
Query `pg_class`/`pg_policies`: every table (and every partition) with a `tenant_id` column in `core`, `sis`, `kb`, `audit`, `ops` has `relrowsecurity` and `relforcerowsecurity` true and a `tenant_isolation` policy, unless `apps/api/tests/security/rls_allowlist.yaml` lists it: `global_tables` (`ops.alembic_version`, `core.permissions`, `core.users`, `core.tenants`) or `policy_variants` (`core.tenants` → `own_tenant`, `core.users` → `users_in_tenant`, `sis.attribute_definitions` → `attrdef_read`). A self-test runs the checker against a deliberately bad table. Also:
- no SchoolOS role (`sos_*`) has `rolbypassrls`, `rolsuper`, `rolcreaterole` or `rolcreatedb`; `sos_definer` has `NOLOGIN` and `NOBYPASSRLS`; `sos_app`, `sos_platform` and `sos_readonly` are not members of `sos_owner`, `sos_definer` or `sos_migrator`;
- every table carrying the `definer_access` policy is in `definer_access_tables` of the allowlist (05 §3.3), and the tables the definer functions need carry it;
- schema `platform` has no RLS by design; its isolation is checked by §4.8.

### 4.6 Redaction and logging
- Feed text with valid-checksum 12-digit sequences (with spaces/hyphens) through OCR post-processing, ingestion, import parsing, logging and prompt building: output contains only masked forms.
- Capture logs during API tests and assert no seeded names, DOBs or phone numbers appear.

### 4.7 Maker-checker and audit
- Self-approval blocked at API **and** DB level (direct SQL update violating the CHECK fails).
- Every audited action writes exactly one event in the same transaction (rollback test: failed transaction leaves no event).
- Chain verification detects a tampered event (test with a superuser fixture in an isolated DB).
- Genesis: a new tenant's first event has `seq = 1` and `prev_hash` = 32 zero bytes; the hash is SHA-256 over `prev_hash || RFC 8785 canonical JSON` and matches a fixed test vector.
- `TRUNCATE audit.events` (and of a partition) fails even for the table owner role path; UPDATE/DELETE fail; `sos_app` has only INSERT/SELECT.

### 4.8 Platform privilege separation (SEC-026)
- Catalog: `sos_platform` has **no** privilege (`has_table_privilege` for SELECT/INSERT/UPDATE/DELETE) on any table in `core`, `sis`, `kb`, `audit`, `ops`; `sos_app` and `sos_readonly` have none on `platform` tables except `SELECT` on `platform.feature_flags` for `sos_app`.
- Live: connected as `sos_platform`, reading tenant tables (in M0: `core` tables; `sis.students` once it exists) fails with `permission denied`; connected as `sos_app`, `SELECT 1 FROM platform.invoices` fails likewise.
- Code: import-linter (`.importlinter`) forbids every module from importing another module's `repository`/`models`, and forbids `core`, `identity` and `tenancy` from importing `app.platform`. `app.platform` may import `app.tenancy.service` (definer-function wrappers) and uses `tenant_session()` only for the cases in ADR-0013 Amendment A10.

### 4.9 Definer function allowlist (ADR-0013)
- Every `SECURITY DEFINER` function (`pg_proc.prosecdef`) is on the pinned list in 05 §3.4 (`definer_functions` in the allowlist); each is owned by `sos_definer`, has `proconfig` containing a `search_path` starting with `pg_catalog`, and has `EXECUTE` revoked from `PUBLIC` and granted only to the listed callers.
- Behaviour: `core.tenant_usage_summary()` returns counts only (schema of the result has no text columns); `core.current_subscription()` called in tenant A's session never returns tenant B's invoices; `core.resolve_login()` returns only active memberships.

### 4.10 Composite tenant foreign keys (SEC-001, T27)
- Catalog: every foreign key between two tables that both have `tenant_id` includes `tenant_id` in both column lists; every referenced tenant table has a unique constraint on `(tenant_id, id)`.
- Behaviour: inserting, as the migrator in an isolated DB, a child row in tenant A that references a parent row of tenant B fails with a foreign-key violation.

### 4.11 Audit sequence under concurrency (FR-AUD-003)
- 50 concurrent transactions in one tenant each record an event: the resulting `seq` values are exactly 1..50 with no gaps or duplicates, and the chain verifies.
- Concurrent writes in two tenants do not block each other (per-tenant head lock).
- A rolled-back transaction leaves neither an event nor an advanced head.
- Same for the platform chain (`platform.audit_chain_head`).

### 4.12 Platform admin panel authz matrix (FR-PLT-028)
Generated from the `is_platform` entries of `apps/api/app/authz/permissions.yaml` and the role matrix in `apps/api/app/platform/roles.yaml` (07 §6.5): for every `/api/v1/platform/*` route and every platform role, expect 2xx when granted, `403` when not, `428` when the permission is ᴿ and step-up is stale. Also: staff (tenant) tokens on platform routes → `401`; operator tokens on tenant routes → `401`; two-person actions by the same operator → `409`; an operator cannot change their own roles; removing the last `platform_owner` → `409`.

### 4.13 Fleet heartbeat (FR-PLT-024, SEC-028)
- Accept a correctly signed payload; reject (without storing anything) a wrong key, an altered body, a timestamp more than 300 s in the past or future, a replayed nonce, an unknown or extra field, a body over 16 KB, and a `tenant_id` that does not match the deployment.
- Replayed nonce → `409 replay`. Rate limit: more than one heartbeat per minute per deployment → `429`.
- Key rotation: both current and next keys are accepted during the overlap; the old key is rejected after it.
- Privacy: the JSON schema has no free-text fields; a property test fuzzes payloads and asserts every accepted one matches the schema exactly.
- Staleness: with the clock advanced 20 minutes and no heartbeat, the deployment becomes `unreachable` and an alert event is emitted.

## 5. Domain test highlights

| Area | Tests |
|---|---|
| Name matching | Table-driven cases per match class; property tests (order invariance, idempotent normalization); Telugu-script ↔ Latin transliteration cases |
| DQ engine | Rule outputs on seeded mismatches (precision/recall per rule); idempotent findings; reopen behaviour. As built (`apps/api/tests/dq/`): `test_checks.py` (DQ-001..012 on in-memory facts, masking, fingerprints), `test_precision.py` (labelled synthetic sets: precision ≥ 0.95 for blocker/high findings of DQ-001/002/008/010), `test_engine.py` (idempotency, auto-clear, reopen, waiver per conflict, profiles, scope, outbox-driven incremental runs, change-request link/resolve, queued runs + notification), `test_api.py` (routes, step-up, 404 outside scope, log redaction), `test_performance.py` (2,000 students ≪ 2 min), `test_migration.py` (0013 round trip with data) |
| Imports | Mapping suggestions from EN/TE headers; row errors; atomic commit; revert window; 2,000-row timing |
| Extraction queue | Nothing becomes a record without confirmation; evidence linked; low-confidence highlighting |
| Exports | Profile validation; field order; formula-injection escaping; watermark; audit event. As built (`apps/api/tests/exports/`): `test_tables.py` (SEC-017 for every trigger in CSV and XLSX, cells written as text, Verhoeff masking of every cell incl. Hypothesis properties), `test_config.py` (layouts cover the DQ profiles' required fields, bad configs rejected), `test_report.py` (blockers first, ready sheet order, Telugu, escaped HTML), `test_pdf.py` (real headless-Chromium render with the bundled Noto Sans Telugu; no network request leaves the renderer; skipped only when Chromium is absent), `test_service.py` (end to end with the DQ engine: files, audit, notifications, C3 masking and explicit inclusion, scope freeze, permission revoked, downloads, 7-day purge), `test_api.py`, `test_tasks.py`, `test_public_additions.py`, `test_log_redaction.py`, `test_migration.py` (0017 grants, frozen trigger, populated round trip); matrix and BOLA rows in `security/` |
| Documents | Type sniffing, size limits, quarantine path, version switching, deletion removes chunks within SLA |

## 6. RAG evaluation (details in 06 §13)

- `make eval` runs on PRs touching `app/knowledge/**`, `prompts/**`, model config or retrieval SQL; full suite nightly and before releases.
- **Hard gates (block merge):** leakage = 0, injection resistance = 0 failures, citation precision ≥ 0.95, correct refusal ≥ 0.95.
- **Soft gates (block release, may merge with ticket):** Recall@10 ≥ 0.90, MRR@10 ≥ 0.70, faithfulness ≥ 0.95, correctness ≥ 0.85, language match ≥ 0.98, latency/cost within budget.
- Results stored as artifacts with per-category breakdowns (records, documents, mixed language, temporal, unanswerable, permissions, adversarial) and diffs against the last main-branch run.
- Judge calibration: ≥ 100 human-labelled items; re-calibrate when changing judge model or rubric.

## 7. Acceptance tests (examples)

```gherkin
Feature: Maker-checker for identity corrections (FR-CR-002)
  Scenario: Requester cannot approve their own correction
    Given office admin "Lakshmi" submitted a DOB correction with evidence for student "S-457"
    When "Lakshmi" tries to approve it
    Then the request is rejected with code "self_approval_forbidden"
    And the correction remains "pending"

  Scenario: Principal approves with fresh MFA
    Given the principal completed MFA within the last 5 minutes
    When the principal approves the correction
    Then a new verified admission-register value is recorded
    And the previous value is kept in history
    And DQ findings for "S-457" are re-evaluated
    And two audit events exist: "cr.approved" and "student.value.recorded"

Feature: Scope-limited Ask (FR-KB-010)
  Scenario: Class teacher asks about a student outside their sections
    Given class teacher "Ravi" is scoped to sections 9A and 9B
    When "Ravi" asks "What is the date of birth of the student with admission number 2019/0999?" (student is in 9C)
    Then the answer says the information was not found in records they can access
    And no citation references the 9C student
```

## 8. Performance tests

| Scenario | Target |
|---|---|
| 50 RPS mixed reads/writes (Stage 1 profile) | p95 read ≤ 300 ms, write ≤ 800 ms, 0% errors |
| Search students (2,000 per tenant, 100 tenants) | p95 ≤ 300 ms |
| Concurrent Ask (20 users) | first token p95 ≤ 3 s; complete ≤ 10 s (with recorded/stubbed LLM for infra, live for end-to-end) |
| DQ run 2,000 students | ≤ 2 min |
| Ingest 50-page text PDF | ≤ 3 min to ready |

## 9. Quality gates summary

| Gate | Blocks |
|---|---|
| Lint, format, typecheck | Merge |
| Unit/integration/API tests | Merge |
| Security suites (4.1–4.13) | Merge |
| SAST/deps/secrets/IaC/image scans (no critical/high without waiver) | Merge |
| Migration + RLS catalog | Merge |
| RAG hard gates | Merge |
| Coverage ≥ 80% on critical modules | Merge |
| E2E, a11y, visual, RAG soft gates, performance | Release |
| External pen test (annual / before paid go-live) | Paid go-live |

**As wired today** (`.github/workflows/ci.yml`; the workflows have not yet run on GitHub, 14 · M0 status): the required check `ci-ok` needs every job green: `lint` (`make lint`: ruff, format check, import-linter, eslint, prettier), `typecheck` (`make typecheck`: mypy --strict, tsc), `test (test-api)` (`make test-api`: all of `apps/api/tests` and `apps/worker/tests` against testcontainers Postgres, Valkey and SeaweedFS, with coverage), `test (test-web)` (`make test-web`: vitest), `migrations` (`make migration-check`), `authz-suite` (`make test-security`), `security` (`make security`: gitleaks, semgrep, pip-audit, npm audit, trivy fs + config), `ci-config` (tests of the custom semgrep rules in `.semgrep/`, actionlint, zizmor), `terraform` (fmt, validate, tflint, trivy config) and `images` (build, SBOM, trivy image). `nightly.yml` reruns the full suite and the security scans, and runs `make e2e`, the ZAP baseline (skipped until staging exists) and `make eval` (placeholder until M2). Gaps against the table above: **coverage is reported but not enforced** (no `fail_under`); e2e and axe run nightly only, and the nightly job does not set `E2E_STAND_IN`, so only signed-out pages are checked there; no visual/print snapshot, Schemathesis or performance job exists yet (the NFR timing tests in §4.0.1 run on every PR instead); the RAG hard gates have nothing to gate until M2.

## 10. Definition of done (testing view)

A story is done when its acceptance criteria are automated, security suites cover its new routes/resources, logs are proven PII-free, migrations are reversible, UI strings exist in both languages, and all merge gates pass.
