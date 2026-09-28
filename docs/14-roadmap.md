# 14 · Roadmap & Milestones

| Field | Value |
|---|---|
| Version | 0.5 · 2026-09-27 |
| Approach | Module by module on a shared core; real school needs decide order after M1; no calendar commitments |
| Related | 01-BRD §7, §11, 02-PRD §3, 03-TRD, 12-Testing strategy, 16-Platform admin panel |
| Changes | 0.5: M1 status (built per scope item, M1 security controls, remaining work, decisions needed, pilot-gate items checkable in code) after §2 M1, verified against code and tests. 0.4: M0 decisions 1, 3, 4 and 5 settled by the product owner (ADR-0020); decision 2 stays open. 0.3: M0 status (built per task, remaining work, decisions needed, pilot-gate status) after §2 M0; Task 5 lists all definer functions. 0.2: M0 adds the platform admin panel with minimal billing (C14), school setup and user admin UI, synthetic data as tasks; M0 task order changed; M0 exit criteria and pilot gate add platform checks; promotions and invoice PDFs in M1; M7 no longer carries basic billing. 0.1: baseline |

---

## 1. Milestone overview

```mermaid
flowchart LR
  M0[M0 Foundations] --> M1[M1 Student record + pre-check]
  M1 --> G{Pilot-ready gate}
  G --> M2[M2 Knowledge base + Ask]
  M2 --> M3[M3 Certificates & registers]
  M2 --> M4[M4 Circulars → tasks, notices]
  M1 --> M5[M5 Timeline & early warning]
  M1 --> M6[M6 Tally read connector]
  M3 --> M7[M7 Multi-school readiness]
  M4 --> M7
```

Order after M2 is decided by the design partner's answer to "which task takes most of the office's time?"

## 2. Milestones

### M0 · Foundations (the core that never changes)
**Scope:** monorepo skeleton · local stack (compose: Postgres+pgvector, Valkey, SeaweedFS) · CI with all merge gates · Terraform staging env · OIDC login via BFF with MFA for privileged roles (ADR-0018) · tenancy + academic structure · RBAC with scopes + `require()` · RLS on all tables + catalog tests · database roles and privilege separation (ADR-0013) · hash-chained audit + daily verification · structured logging with redaction · OTel tracing · **platform admin panel (C14) with minimal billing: manual payments, GST invoices** · fleet heartbeat for dedicated hosts · school setup and user admin UI · synthetic data generator · security headers/CSP.
**Exit criteria**
- Cross-tenant suite, route-enumeration and authz matrix pass in CI
- Audit chain verification job green; tamper test detects modification; sequence concurrency test green
- Platform suites pass in CI: privilege separation, definer allowlist, composite FKs, admin panel authz matrix, heartbeat (12 §4.8–4.13)
- In staging, a shared-tier school is provisioned end to end from the admin panel and its owner signs in with MFA
- In staging, an invoice is generated, issued with a correct financial-year number and marked paid with a manual payment
- A staging dedicated host (`deploy/dedicated/compose.yaml`) sends accepted heartbeats; stopping it raises the `unreachable` alert
- Deploy to staging fully through the pipeline (no manual console changes)
- `make check` < 10 min on CI
- SEC-001..011 and SEC-026..028 implemented; SEC-029 for offboarding

### M0 status (2026-09-26)

The M0 code is merged on the session branch (not yet on `main`). What exists, per build task (§4), with the main requirement IDs:

| Task | Built | Requirements |
|---|---|---|
| 1 · Repo scaffold | uv + npm workspaces monorepo; Makefile (`install`, `dev`, `down`, `logs`, `migrate`, `db-shell`, `seed-synthetic`, `openapi`, `test`, `test-api`, `test-web`, `test-security`, `migration-check`, `e2e`, `lint`, `format`, `typecheck`, `security`, `eval` (stub until M2), `check`); `docker-compose.yml` (`db`, `valkey`, `s3`, `s3-init`, `migrate`, `api`, `worker`, `beat`, `web`, `oidc` in profile `dev`); `.env.example`; pre-commit; import-linter contracts | NFR-MNT-002 |
| 2 · CI pipeline | `.github/workflows/ci.yml` (jobs `lint`, `typecheck`, `test (test-api, test-web)`, `migrations`, `authz-suite`, `security`, `ci-config`, `terraform`, `images`, required check `ci-ok`), `nightly.yml`, `deploy-staging.yml`, `deploy-dedicated.yml`; CODEOWNERS; Dependabot; zizmor/actionlint | SEC-009, NFR-SEC-* |
| 3 · DB bootstrap | `infra/db/bootstrap.sql` (six roles, none with BYPASSRLS; schemas; extensions); `0001_baseline`; RLS catalog, definer allowlist and privilege-separation tests | SEC-001, SEC-002, SEC-026 |
| 4 · Tenant session | `tenant_session()`, `context_free_session()`, `platform_session()`; two-tenant isolation harness | FR-TEN-001, FR-TEN-002, SEC-001 |
| 8 · Logging/telemetry | Structured JSON logging with field allowlist and `redact()` (Verhoeff Aadhaar, phones, emails); OTel tracing with allowlisted attributes | SEC-008, NFR-OBS-001, PRV-015 |
| 7 · Audit | `0002_audit`: partitioned append-only tenant chain + platform chain; `record()`, `record_platform()`, `verify_chain()`; daily signed S3 archive and verification; partition CLI; school audit viewer and verify routes | FR-AUD-001..005 (CSV export pending), SEC-007 |
| 5 · Core schema | `0003_core_schema` with composite FKs and seven definer functions; `0007_accept_invitations` (ADR-0019) | FR-TEN-001..003, FR-TEN-010, FR-IAM-010..013 |
| 6 · AuthZ | `0004_authz_seed`; `permissions.yaml` + `roles.yaml`; `require()` with scopes, implicit `session.authenticated`, 60 s snapshot cache; route enumeration, generated matrix, BOLA | FR-IAM-010..014, SEC-003, SEC-005, SEC-015 (M0 resources) |
| 9 · Identity + BFF | API: OIDC access-token verification (JWKS), service token, MFA and step-up (`428`), `/me*` routes incl. school picker and invitation acceptance. Web: PKCE login for staff and operators, encrypted server sessions in Valkey, CSRF, refresh rotation with reuse detection, idle/absolute timeouts, step-up, session list/revoke | FR-IAM-001..006, FR-IAM-013, SEC-004..006 |
| 10 · Terraform | Modules `network`, `rds`, `rds_backup_replication`, `redis`, `s3`, `s3_bucket`, `kms`, `secrets`, `ecr`, `ecs_cluster`, `ecs_service`, `alb_waf`, `cognito` (+ pre-token Lambda), `observability`, `ci_oidc`, `shared_platform`, `dedicated_host`; roots `bootstrap`, `envs/staging`, `envs/prod`, `envs/dedicated-template`, with `terraform test` files. Validated in CI; never planned or applied against AWS | SEC-009, SEC-011, SEC-022, SEC-030 (partly) |
| 11 · Platform admin panel | 11a–11g backend (`0005_platform`, `0006_ops`, 70 control-plane routes + the fleet heartbeat, heartbeat, billing jobs, usage, flags, announcements, support, bootstrap-owner CLI); 11h web screens under `/[locale]/platform/*` (dashboard, schools, provision, plans, subscriptions, invoices, usage, flags, fleet, announcements, support, break-glass, operators, audit) | FR-PLT-001..030, SEC-026..029 |
| 12 · School setup UI | Structure (years, classes, sections), users (invite, roles, scopes, status), Plan & billing, audit log pages; EN/TE messages | US-102, US-202, US-1204, FR-AUD-005 |
| 13 · Synthetic data | `make seed-synthetic`: deterministic schools, academic structure and staff for every system role with AP name variants and overlapping names across schools; each first owner created through the production path (owner invite while provisioning → activate → invitation acceptance), audited on both chains | NFR-MNT-002, docs/12 §3 |

**Remaining before M0 exit** (exit criteria above):
- **CI on GitHub:** the workflows have never run on GitHub (pushing the branch is currently blocked); `ci-ok` must go green and `make check` must be timed (< 10 min).
- **Branch protection / rulesets** on `main` with `ci-ok` required and CODEOWNERS review (owner handle in `.github/CODEOWNERS` is a placeholder).
- **AWS:** accounts, `terraform plan/apply` for `bootstrap` and `envs/staging`, deploy pipeline secrets; then the staging exit checks (shared school provisioned end to end, owner signs in with MFA, invoice issued and paid, dedicated host heartbeat and `unreachable` alert, deploy fully through the pipeline).
- **Deploy configuration vs settings names (blocker for staging):** the Terraform `shared_platform` module and `deploy/dedicated/compose.yaml` set `SOS_KMS_KEY_ARN`, `SOS_FLEET_URL` and `SOS_FLEET_HMAC_KEY`, but the settings read `SOS_KMS_DATA_KEY_ARN`, `SOS_CONTROL_PLANE_URL`, `SOS_HEARTBEAT_KEY`/`SOS_HEARTBEAT_KEY_ID` and `SOS_DEDICATED_TENANT_ID`; `SOS_AUDIT_SIGNING_KEY_ARN` and `SOS_BILLING_SUPPLIER_*` are set nowhere; the ECS migrate task sets only `SOS_ENV`, so the staging/prod settings guard refuses it (`key_wrapper` defaults to `local-dev`).
- **Dedicated tier:** a host-side tenant provisioning command (the runbook names `python -m app.tenancy.provision_dedicated`, which does not exist); pin WAL-G in `deploy/dedicated/walg/walg.lock` (go-live precondition, 10 §15.3).
- **Identity:** Cognito Essentials has no threat protection, so breached-password screening (and adaptive login protection) must be built in the BFF/identity module or the pool moved to Plus (ADR-0018); FR-IAM-005 lockout auditing is not built.
- **Email delivery:** owner and staff invite emails, billing reminders (FR-PLT-019), usage-threshold notifications (FR-PLT-021). M0 records the events only.
- **Audit:** CSV export of the school audit log (FR-AUD-005).
- **Usage meters:** students, storage, documents and AI counts are 0 until `sis`/`kb` exist (M1+; `core.tenant_usage_summary` and the `definer_access` allowlist grow then).
- **Security testing:** ZAP baseline (nightly job skips until staging exists); axe accessibility checks in Playwright; Schemathesis contract tests; e2e beyond the signed-out smoke test.
- **School support form** in the web app (API routes exist).

**Decisions** (docs and code disagreed; product owner decisions of 2026-09-27):
1. **School-chain audit events for platform actions** — **settled:** guaranteed through a transactional outbox (`platform.tenant_audit_outbox`, queued in the platform transaction, delivered exactly once and in order per school by `platform.deliver_tenant_audit`); no definer function. [ADR-0020](adr/ADR-0020-control-plane-boundaries-and-guaranteed-audit-copies.md), 16 §16.
2. **Shared provisioning is not one transaction** (FR-PLT-002 says "in one transaction"): the first transaction is atomic and later steps resume idempotently (16 §5.4). **Still open:** amend FR-PLT-002, or change the code? (The school-chain `tenant.provisioned` copy is now queued atomically with the owner invite.)
3. **Suspended schools** — **settled:** the owner and principal keep `GET /me`, school choice, the sign-in event and Plan & billing (and the full export once FR-ADM-001 exists); every other school route answers `403 tenant_suspended` for every role. One pinned allowlist in `app/authz/resolver.py` (16 §5.5).
4. **Control plane and tenant modules** — **settled:** `platform` may call `tenancy.service` only for tenant lifecycle (register, initialise keys, activate, suspend, reactivate, offboard, usage counts) and never reads tenant data; enforced by `tests/platform/test_boundaries.py` (CLAUDE.md §4, ADR-0020).
5. **ADR process** — **settled:** dated Amendments sections may record implementation facts that do not change the decision; any change to the decision needs a new ADR that amends or supersedes it (adr/README step 5).

**Pilot-ready gate (§3) status:** not started except where M0 code covers controls: SEC-001..011 and SEC-026..029 are implemented in code and tests (SEC-029 emergency break-glass workflow is M1; SEC-011/SEC-022/SEC-023 exist only as unapplied Terraform); SEC-012..017, SEC-021 are M1; SEC-024 restore drill, incident rehearsal, DPA/DPIA, ZDR request, production accounts with two platform owners, CA confirmation of invoice format and GST, and staff training are all open.

### M1 · Student record, onboarding and pre-check
**Scope:** promotions with preview/commit/undo (FR-TEN-011) · invoice PDFs (before the first paid invoice) · students, enrolments, guardians · attribute catalog + per-source values + canonical projection · Excel/CSV/Sheets import with mapping, validation, commit, revert · register-photo extraction with verification queue (Aadhaar redaction) · DQ engine with DQ-001..012 and name matching · findings workflow · change requests (maker-checker) + correction memo · export profiles: `cisce-registration-2026` pre-check, `udise-plus` check sheet · bilingual reports (PDF/XLSX) · C3 field encryption · break-glass workflow.
**Exit criteria**
- Design partner's Class 9 and Class 11 batches imported and identity fields verified
- Pre-check report used before a real CISCE registration; post-submission corrections for pre-checked batches = 0
- DQ precision on seeded mismatches ≥ 0.95 for blocker/high rules
- SEC-012..017, SEC-021 and SEC-029 (emergency break-glass) implemented; pilot-ready gate passed before real data

### M1 status (2026-09-27)

Checked against the code and tests on the session branch (migrations `0008_sis_students` … `0019_export_access`), not against earlier doc claims. Paths are relative to `apps/api/` unless they start with `apps/`, `infra/` or `deploy/`. "In progress" means another work package is open on it today.

| Scope item | Status | Evidence (code · tests) | Missing |
|---|---|---|---|
| Promotions with preview/commit/undo (FR-TEN-011, US-202 AC2) | **Not started** | None: no route, service, table or screen. 09 §3 lists `/academic-years/{id}/promotions:preview` · `:commit` · `:undo` | Everything (see decision 1 below) |
| Invoice PDFs (before the first paid invoice) | **Done in code; template v0 pending CA review** | `app/platform/invoice_pdf.py` (template v0, Rule 46 fields), `invoice_files.py` (render on queue `pdf` via `app/core/pdf.py`, idempotent per invoice, audit), `invoice_storage.py` (control-plane prefix `platform/invoices/`), `GET /platform/invoices/{id}/download-url`, migration `0029_invoice_pdfs` (16 §5.8.1) | CA sign-off of the layout (16 §19 Q2, Q3, Q11); `SOS_BILLING_SUPPLIER_ADDRESS` wired in Terraform; panel download button; emailing to schools (Q13) |
| Students, enrolments, guardians (FR-STU-001..008, 010..012; US-301..303) | **Done** (API); web editing in progress | `0008_sis_students`; `app/students/` (routes for students, values, verify, sensitive reveal, guardians, enrolments; search in `search.py`); `tests/students/` (incl. `test_search_performance.py` for FR-STU-011, `test_log_redaction.py`); BOLA and scope rows in `tests/security/test_bola.py`; web `/students`, `/students/new`, `/students/[id]` | Guardian and enrolment editing screens (in progress) |
| Attribute catalog, per-source values, canonical projection (FR-STU-002..006) | **Done** | `app/students/attributes.yaml` seeded into `sis.attribute_definitions`; append-only values with supersession and verification; `canonical.py` resolves on read with the admission-register anchor (BR-01) · `tests/students/test_definitions.py`, `test_canonical.py`, `test_service.py`, `test_values_changed_event.py` | — |
| Excel/CSV/Sheets import with mapping, validation, commit, revert (FR-IMP-001..007; US-401) | **Done**; follow-ups in progress | `0012_imports`; `app/imports/` (sheet parsing with zip-bomb and entity guards, EN/TE header mapping and templates, row validation, atomic commit, 24 h revert, 90-day raw-file purge) · `tests/imports/` covers every FR-IMP-001..007 ID, SEC-013, SEC-015, SEC-017 and the 2,000-row timing; web `/imports` | `import.reverted` follow-ups (in progress) |
| Register-photo extraction with verification queue and Aadhaar redaction (FR-IMP-020..024, PRV-016; US-402) | **Partial** | `0016_extraction`, `0018_extraction_redaction`; `app/extraction/` (provider interface, queue, confirm/reject with the page as evidence, page-image black-out and re-read) · `tests/extraction/` (FR-IMP-020..024, PRV-016, SEC-008); web `/register-photos` | **No real provider**: only `fake` (local/ci); staging/prod default to `not-configured` (`SOS_EXTRACTION_PROVIDER`). **PDF pages refused** (`accepted_mime_types` is JPEG/PNG; no approved rasteriser) although FR-IMP-020 lists PDF |
| DQ engine, DQ-001..012, name matching (FR-DQ-001..006) | **Done**; timing test in progress | `app/dq/` (`config/rules.yaml` with 12 rules, `checks.py`, `matching.py`, `config/match_classes.yaml`, `config/variants.yaml`, EN/TE `config/explanations.yaml`; on-demand and outbox-driven incremental runs) · `tests/dq/` (`test_checks.py`, `test_matching_*.py`, `test_precision.py` ≥ 0.95 on labelled sets for DQ-001/002/003/008/010, `test_performance.py` 2,000 students, `test_engine.py`) | DQ timing test (in progress) |
| Findings workflow (FR-DQ-020; US-502) | **Done** | `/findings/{id}/resolve` · `/waive` (`dq.findings.waive`, reason) · `tests/dq/test_engine.py`, `test_api.py`; web `/findings`, `/findings/rules`, `/findings/runs/[id]` | — |
| Change requests (maker-checker) and correction memo (FR-CR-001..005, SEC-014; US-601) | **Done** | `0014_change_requests` (DB CHECK against self-approval); `app/changes/` incl. `memo.py` (print-ready bilingual HTML) and daily expiry · `tests/changes/` (`test_SEC_014_database_refuses_self_approval_*`, FR-CR-001..005); web `/change-requests` | — |
| Export profiles `cisce-registration-2026` pre-check and `udise-plus` check sheet (FR-EXP-001; US-501, US-901) | **Partial** | Profiles in `app/dq/config/profiles/*.yaml`, layouts in `app/exports/config.yaml`, `0017_exports`, `0019_export_access` (ADR-0021) · `tests/exports/test_config.py`, `test_service.py`, `test_report.py`; web `/exports/new/precheck` | Field lists are **minimal placeholders** (`TODO(board formats)`, `TODO(portal formats)`): exact council and UDISE+ field order, codes and date formats still to be taken from the official formats |
| Bilingual reports PDF/XLSX (FR-EXP-002..004, SEC-017) | **Done in code**; image and storage in progress | `app/exports/report.py`, `pdf.py` (Chromium via Playwright, JS off, network blocked, bundled Noto Sans Telugu with checksum), `tables.py` (formula neutralising, Aadhaar masking), watermark, step-up per ADR-0021 · `tests/exports/` (`test_pdf.py` renders for real and skips only without Chromium) | Chromium in the worker image, S3 lifecycle and KMS for export files (in progress, infra) |
| C3 field encryption (SEC-012, FR-STU-007) | **Done**; rotation not built | `app/core/crypto.py` (KMS key wrapper, AEAD with `tenant|table|column|row` AAD), `app/students/crypto.py` (keyring with ≤ 15 min cache, blind index) · `tests/students/test_crypto.py`, `tests/tenancy/test_key_wrapping.py`, C3 tests in imports, changes and exports | Scheduled DEK rotation with background re-encryption (07 §8); the ciphertext already carries `key_version` |
| Break-glass workflow (SEC-021, SEC-029; US-103, FR-OPS-004) | **Done** for the shared tier | `0011_breakglass`; `app/breakglass/`, `app/platform/breakglass.py`, `app/authz/breakglass_guard.py` (read-only, audited `breakglass.access`) · `tests/breakglass/` (step-up approval, deny, revoke, expiry, `test_SEC_021_database_refuses_self_approval_and_approved_emergencies`, `test_SEC_029_emergency_access_needs_two_operators_and_notifies_the_school`); web `/break-glass` and `/platform/break-glass` | Dedicated hosts: requests cannot reach them (no control plane; heartbeat channel not built). Operator sign-in to the school (ADR-0023 option C): support client of the operator pool, `0027_identity_issuer` (expand; contract step in a later release), `POST /breakglass/support-session`, web `/bff/auth/support/*` · `tests/identity/test_support_tokens.py`, `tests/breakglass/test_support_signin.py`. Cognito support app client per environment not created yet |

**M1 security controls** (07 §15):

| Control | Status | Evidence |
|---|---|---|
| SEC-012 Per-tenant DEKs; C3 encryption | Done (rotation job open) | see C3 row above |
| SEC-013 Aadhaar input rejection + Verhoeff redaction in pipelines | Done | `app/students/api.py` and `service.py` (422 `aadhaar_full_number_rejected`), `app/core/redaction.py`; `tests/imports/` (`test_SEC_013_*`), `tests/extraction/` (`test_FR_IMP_022_PRV_016_*`), `tests/exports/` (`test_invariant_4_*`), `apps/web/src/lib/aadhaar.test.ts` |
| SEC-014 Maker-checker with DB constraint | Done | `tests/changes/test_schema.py`, `test_service.py` (`test_SEC_014_*`) |
| SEC-015 Scoped repositories + BOLA per resource | Done for every M1 resource | `tests/security/test_bola.py` (`test_SEC_015_every_id_route_is_covered`, students, change requests, extraction, exports), scope tests per module |
| SEC-016 File upload controls | Done in code; deployment in progress | `app/documents/filetypes.py`, `scanning.py` (ClamAV `INSTREAM`; dev scanner refused in staging/prod), `storage.py` (presigned POST with size range, SSE-KMS condition) · `tests/documents/` (`test_SEC_016_*`, FR-DOC-001..008) |
| SEC-017 Formula-injection-safe spreadsheets | Done | `app/exports/tables.py`, `app/imports/sheet.py` · `tests/exports/test_tables.py`, `tests/imports/` (`test_SEC_017_*`) |
| SEC-021 Break-glass visible to school | Done (shared tier) | see break-glass row |
| SEC-029 Two-person rule (offboarding M0, emergency break-glass M1) | Done | `tests/breakglass/test_breakglass.py`, `tests/platform/test_provisioning.py` |

**Remaining before M1 exit** (besides the items in progress listed above):
- **Promotions** (FR-TEN-011): preview, commit and undo within 24 h, with screens.
- **Invoice PDFs**: before the first paid invoice.
- **Extraction provider**: choose a real OCR/extraction provider by evaluation (FR-IMP-024), then add it; a new sub-processor needs an ADR and a privacy review (§5). PDF register pages need an approved rasteriser.
- **Official export formats**: replace the placeholder field lists of `cisce-registration-2026` and `udise-plus` with the council's and the portal's exact formats.
- **Synthetic student data**: `make seed-synthetic` creates schools, structure and staff only; 12 §3 also requires 2,000 students, guardians, per-source values with deliberate mismatches, rendered register pages and circulars. Needed for demos, the pilot rehearsal, the "DQ precision on seeded mismatches" exit check at school scale, and the M2 eval corpus.
- **Usage meters**: students, storage and documents are still 0 (`core.tenant_usage_summary()` counts memberships, sections and years only); counting `sis`/`kb` rows means `definer_access` on those tables, which needs an ADR (CLAUDE.md §11).
- **Test gates**: coverage ≥ 80 % on critical modules (12 §9) is not enforced (no `fail_under`); e2e journeys for the M1 screens (import → DQ → change request → pre-check) are not written; e2e runs nightly only.
- **Carried from M0 and still open**: email delivery (invites, approvals), audit log CSV export (FR-AUD-005), everything that needs AWS or GitHub (M0 status above).
- **Human evidence for the exit criteria**: design-partner batches imported and verified, a real CISCE pre-check used, post-submission corrections counted. None can be ticked from code.

**Decisions needed** (owner):
1. **Promotions design:** which module owns the batch (`tenancy` owns years, `students` owns enrolments), how detained, left and repeating students are chosen in the preview, and what "undo within 24 h" does when enrolments changed afterwards (refuse, like import revert?).
2. **Extraction provider:** candidate providers, residency (ap-south-1), cost ceiling and the evaluation set; new ADR.
3. **Export formats:** who supplies the official CISCE 2026 registration and UDISE+ formats, and whether M1 exits with the placeholders.
4. **Invoice PDF:** CA confirmation of the invoice layout and GST fields (16 §19 Q2–Q3) before the template is fixed; where the PDF lives (control-plane bucket, not a school prefix).
5. **Usage meters:** ADR extending `core.tenant_usage_summary()` (and `definer_access`) to `sis.students`, `kb.document_versions` sizes and document counts, or defer to M2.
6. **Break-glass on dedicated hosts:** deliver requests with the heartbeat response, or support shared tier only in M1.
7. **Emergency break-glass for an operator never approved anywhere** (ADR-0023 item 5, recorded as a deviation in its Amendments): the emergency path finds the operator's identity but cannot create it, because `core.create_user_for_invite` requires an inviter. Relax that definer guard for operator-issuer identities (new ADR), or keep failing closed.

**Pilot-ready gate (§3), items checkable in code** (none ticked; each still needs verification in staging):
- SEC-001..017: implemented in code and tests (SEC-011 only as unapplied Terraform; SEC-016 needs ClamAV and SSE-KMS deployed).
- SEC-021: implemented (shared tier). SEC-022: WAF managed rule groups and rate-based rules in `infra/terraform/modules/alb_waf/main.tf`, never applied. **SEC-023: not started** (no GuardDuty, CloudTrail with Object Lock, Config or Security Hub resources in `infra/terraform/`). SEC-024: dedicated-host drill script `deploy/dedicated/scripts/restore.sh` exists; no RDS restore drill script; no drill run.
- SEC-026..029: implemented in code and tests.
- Operator pool MFA ON: asserted by `infra/terraform/envs/prod/tests/prod.tftest.hcl`; not applied.
- Synthetic data never in prod: `make seed-synthetic` refuses outside `local`/`ci` (`tests/devtools/test_seed_synthetic.py`); the purge of prod itself is a human check.
- Everything else in §3 (restore drill, incident rehearsal, DPA/DPIA, ZDR, two platform owners, CA, data-handling permission, training) needs human evidence.

### M2 · Knowledge base and "Ask the school"
**Scope:** document upload (presigned), AV scan, extraction/OCR, chunking, embeddings (provider chosen by eval, ADR-0006), hybrid retrieval, record tools, answer generation with search-result citations, citation validation, SSE UI with source chips, feedback, verified answers, budgets + search-only fallback, eval harness with hard gates.
**Exit criteria**
- Hard gates pass (leakage 0, injection 0, citation precision ≥ 0.95, refusal ≥ 0.95)
- Soft gates met on full suite; p95 latency targets met in staging
- Office staff use Ask for real questions weekly at the design partner; ≥ 80% rated helpful
- SEC-018..020 implemented

### M3 · Certificates and registers
**Scope:** templates for TC, bonafide, study, conduct certificates (EN/TE, school formats) · serial numbers · automatic register entries · duplicate marking · print views of registers in familiar formats · certificate PDFs indexed as documents.
**Exit:** certificates issued in production with median time < 5 minutes; register entries reconcile with paper.

### M4 · Circulars → tasks and bilingual notices
**Scope:** circular metadata/deadline extraction · task list with owners and due dates · reminders · parent notice generator (EN/TE text + printable/image) for posting in existing groups.
**Exit:** ≥ 90% of circulars in a term processed with deadlines captured; staff confirm fewer missed tasks.

### M5 · Student timeline and early warning
**Scope:** attendance and marks import · per-student timeline · ABC indicators (attendance, behaviour notes, course performance) · flags with assigned owner and intervention log · purpose limits (08 §4) · AP three-consecutive-absence follow-up support.
**Exit:** pilot with class teachers; ≥ 90% of flags actioned within 7 days; DPIA updated.

### M6 · Tally read connector
**Scope:** edge agent (Windows service) reading TallyPrime via XML over HTTP on localhost · configured ledgers only · sync to SchoolOS · `get_fee_dues` tool for accountant/management.
**Exit:** fee-due questions answered from synced Tally data; accountant confirms figures match Tally.

### M7 · Multi-school readiness
**Scope:** self-serve onboarding wizard · import templates library · online payment collection if ADR-0016 is accepted (basic billing already shipped in M0) · tenant admin improvements · support tooling beyond tickets · first dedicated-tier schools at scale (SEC-030 before the first one) · Stage 1 infrastructure (Multi-AZ, replicas, cross-account backups) · external pen test · published security overview for schools.
**Exit:** 5 schools live with < 1 week onboarding effort each; SLOs met for 2 consecutive months.

## 3. Pilot-ready gate (before any real student data)

- [ ] SEC-001..017, SEC-021..024 and SEC-026..029 implemented and verified
- [ ] Restore drill completed within RPO/RTO; results recorded
- [ ] Incident runbook rehearsed, including the CERT-In 6-hour flow; CERT-In point of contact designated
- [ ] DPA signed with the school; school's parent/staff notice issued; DPIA completed
- [ ] Sub-processor list shared; Zero Data Retention requested for the production API organization
- [ ] Production accounts: MFA on all operator access (operator pool MFA ON); break-glass tested; at least two active platform owners (two-person rules, 16 §19 Q1)
- [ ] Invoice number format and GST registration confirmed with a CA (16 §19 Q2–Q3)
- [ ] Data-handling permission for imports/photos documented; synthetic data purged from prod
- [ ] Staff training done (EN/TE); support channel and escalation path defined

## 4. First build tasks for AI-assisted development (M0)

Give these to your coding assistant one at a time, each with the referenced docs loaded. **Task numbers are stable IDs; do them in the order listed** (1, 2, 3, 4, 8, 7, 5, 6, 9, 10, 11, 12, 13).

| Order | Task | What | Docs |
|---|---|---|---|
| 1 | **Task 1 · Repo scaffold** | Monorepo layout, root Makefile (`install`, `dev`, `down`, `logs`, `migrate`, `seed-synthetic`, `test`, `test-api`, `test-web`, `e2e`, `lint`, `format`, `typecheck`, `security`, `eval`, `check`, `db-shell`), compose (`db`, `valkey`, `s3`, `s3-init`, `migrate`, `api`, `worker`, `beat`, `web`, `oidc` in profile `dev`), `.env.example`, pre-commit (ruff, mypy, eslint, gitleaks) | 13 §1, 10 §11, 04 §17, ADR-0014 |
| 2 | **Task 2 · CI pipeline** | Lint/typecheck/test/security jobs (`make install`, `make lint`, `make typecheck`, `make test`, `make security`) and required checks | 10 §7, 12 §9 |
| 3 | **Task 3 · DB bootstrap** | `infra/db/bootstrap.sql` (roles `sos_owner`, `sos_migrator`, `sos_app`, `sos_platform`, `sos_readonly`, `sos_definer`; schemas incl. `platform`; extensions), `core.current_tenant()`, Alembic `0001_baseline`, RLS catalog test + `rls_allowlist.yaml` + `definer_access` allowlist, privilege-separation catalog test | 05 §3, 12 §4.5, §4.8, ADR-0013 |
| 4 | **Task 4 · Tenant session** | `core.db.tenant_session()` with `set_config(..., true)`, `core.db.platform_session()`; cross-tenant test harness with two synthetic tenants | 05 §3.2, 12 §4.4 |
| 5 | **Task 8 · Logging/telemetry** | Structured logger with allowlist + `redact()` (incl. Verhoeff), OTel instrumentation, log redaction tests | 11 §2–4, 08 §5 |
| 6 | **Task 7 · Audit module** | `0002_audit`: partitioned append-only events with `seq`, chain heads with `last_seq`, RFC 8785 hashing, genesis, TRUNCATE trigger, RLS on partitions; `audit.record()`, `record_platform()`, `verify_chain()`, daily verification task, tamper and concurrency tests | 05 §7.1, 12 §4.7, §4.11, ADR-0013 |
| 7 | **Task 5 · Core schema** | `0003_core_schema`: tenants, keys, users, memberships, roles, permissions, scopes, academic structure with composite FKs; allowlisted definer functions (`resolve_login`, `find_user_id_by_subject`, `create_user_for_invite`, `list_tenant_ids`, `provision_tenant`, `set_tenant_status`, `tenant_usage_summary`; later `current_subscription`, `create_owner_invite` in 0005, `ops.claim_outbox` in 0006, `accept_invitations` in 0007) | 05 §3.4–3.5, §4, 12 §4.9–4.10 |
| 8 | **Task 6 · AuthZ module** | `0004_authz_seed`: permission catalog (incl. `tenant.structure.manage`, `tenant.billing.read`, split student/export permissions) and system role templates from config; `require()` dependency, scope filters, route-enumeration and matrix tests | 07 §6, 12 §4.1–4.2 |
| 9 | **Task 9 · Identity + BFF** | Two Cognito pools (staff MFA optional + `sos:mfa`; operators MFA on), dev OIDC stub locally; BFF session cookie, CSRF, `X-Service-Token`, idle/absolute timeouts, step-up with `prompt=login` | 07 §5, 09 §1, ADR-0018 |
| 10 | **Task 10 · Terraform staging** | Network, RDS (pgvector), ElastiCache for Valkey, S3 (+ audit bucket with Object Lock), KMS, ECS services, ALB+WAF, Cognito pools + Lambda, CI OIDC roles | 10 §3–5 |
| 11 | **Task 11 · Platform admin panel** | Sub-steps below | 16, ADR-0013, ADR-0015, ADR-0017 |
| 12 | **Task 12 · School setup and user admin UI** | Screens for academic years, classes, sections (`tenant.structure.manage`); invite users, assign roles and scopes (step-up); Plan & billing page (US-1204); support ticket form once `support.ticket.create` is approved | 02 US-102, US-202, US-1204; 09 §4 |
| 13 | **Task 13 · Synthetic data** | `make seed-synthetic`: two synthetic tenants (2,000 students, Telugu/English names, deliberate mismatches, circulars), synthetic operators with each platform role, plans, subscriptions, invoices, a fake dedicated deployment | 12 §3 |

**Task 11 sub-steps** (migrations `0005_platform`, `0006_ops`):

| Step | Scope | Requirements |
|---|---|---|
| 11a | Platform schema and operators: `0005_platform` tables and grants, platform audit chain, `platform.operators`/`operator_roles`, `require_platform()`, operator OIDC client, step-up, bootstrap-owner CLI | FR-PLT-028, FR-PLT-029, SEC-026, SEC-027 |
| 11b | Schools and provisioning: list/detail, shared provisioning via `core.provision_tenant()`, dedicated provisioning record + heartbeat key, suspend/reactivate, two-person offboarding; Terraform module `dedicated_host` and `deploy/dedicated/compose.yaml` | FR-PLT-001..005, SEC-029 |
| 11c | Plans, subscriptions, billing accounts, invoices and **manual payments**: versioned plans, lifecycle with 15-day grace and exam-window rule, invoice job, FY numbering, GST, payments with TDS, reversal, void | FR-PLT-010..019 |
| 11d | Feature flags (`platform.feature_flags`, % rollout, overrides) and announcements (publish to Valkey; heartbeat delivery) | FR-PLT-022, FR-PLT-026 |
| 11e | Usage and fleet: `core.tenant_usage_summary()` collector, thresholds, deployments registry, `POST /api/v1/fleet/heartbeat` (HMAC), staleness job, alerts | FR-PLT-020, FR-PLT-021, FR-PLT-023..025, SEC-028 |
| 11f | Support tickets: queue, SLA timers, redaction, 1-year purge; `0006_ops` (`ops.job_runs`, `ops.outbox` + `claim_outbox`, `ops.break_glass_grants`, `ops.idempotency_keys`) | FR-PLT-027 |
| 11g | Platform audit viewer and verification; `core.current_subscription()` for the school's Plan & billing page | FR-PLT-029, FR-PLT-030 |
| 11h | Web admin UI: route group `/[locale]/platform/*` on the admin host, all screens in 16 §5, EN/TE strings, keyboard and 1366×768 checks, Playwright journeys | 16 §5 |

Then continue with M1 stories US-301 → US-401 → US-501 → US-601 → US-402 in that order.

## 5. Scope guardrails

- New ideas go to a backlog with the question: "Which BRD objective does this move, and for which user?"
- Anything that adds a new sub-processor, a new data class, or AI write capability requires an ADR and a privacy review.
