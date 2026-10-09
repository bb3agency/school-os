# CLAUDE.md: Operating contract for AI coding assistants

Read this file completely before writing or changing code. If a request conflicts with this file, stop and say so.

## 1. What SchoolOS is (60 seconds)

A multi-tenant SaaS for Indian private schools (starting in Andhra Pradesh). The admin office enters student details once; SchoolOS checks them against other sources (admission register, Aadhaar-as-printed, UDISE+, board registration), flags mismatches before portal submissions, generates certificates/registers, and answers questions from the school's own records and documents with citations ("Ask the school").

Users are office clerks, principals, management, accountants, exam coordinators and teachers. They are busy, often not technical, and work on shared office PCs with patchy internet. **English first** (ADR-0036, product owner 2026-09-30): every screen, document, notice, message and AI answer is in English. Telugu (the bilingual design: Telugu UI, parent-facing Telugu, Telugu AI answers) is **hidden, not deleted**, behind one switch `SOS_TELUGU_ENABLED` (default `false`), read only through `app.core.languages`; a question typed in Telugu is still accepted and answered in English.

SchoolOS is a **managed SaaS** that we run, sold as a recurring subscription in two tiers from one codebase: the **shared tier** (pooled multi-tenant platform in AWS Mumbai) and the **dedicated tier** (one isolated host per school, optional custom domain). A **control plane** (platform admin panel, billing, fleet) runs only in the shared deployment and never reads student data (ADR-0015, ADR-0017, `docs/16-platform-admin-panel.md`).

## 2. Sources of truth

| Topic | Doc |
|---|---|
| Scope, business rules | `docs/01-BRD.md`, `docs/02-PRD.md` |
| Requirements with IDs | `docs/03-TRD.md` |
| Architecture, modules, flows | `docs/04-system-architecture.md` |
| Schema, RLS, classification | `docs/05-data-model.md` |
| RAG, tools, prompts, evals | `docs/06-rag-architecture.md` |
| Security controls | `docs/07-security-architecture.md` |
| Privacy/DPDP/Aadhaar | `docs/08-privacy-and-compliance.md` |
| API conventions | `docs/09-api-specification.md` |
| Standards and workflow | `docs/13-engineering-standards.md` |
| What to build next | `docs/14-roadmap.md` |
| Platform admin panel, billing, fleet | `docs/16-platform-admin-panel.md` |
| Web UI tokens, components, contrast | `docs/17-ui-design-system.md` |
| Decisions and why | `docs/adr/` (index in `docs/adr/README.md`) |

Reference requirement IDs (e.g. `FR-STU-004`, `SEC-012`) in commit messages, PR descriptions and test names.

## 3. Tech stack (do not substitute without a new ADR)

- **API/Workers:** Python 3.12+, FastAPI, Pydantic v2, SQLAlchemy 2.x (typed, sync sessions with psycopg 3), Alembic, Celery + Valkey (Redis protocol; ADR-0014), httpx
- **Database:** PostgreSQL 16+ with `pgvector`, `pg_trgm`, `citext`; RLS on every tenant table
- **Files:** S3 (ap-south-1), private buckets, SSE-KMS, presigned URLs (SeaweedFS locally and in CI; ADR-0014)
- **Web:** Next.js (App Router) + TypeScript strict + Tailwind; BFF pattern (tokens never reach browser JS); i18n `en` (the `te` catalog is kept dormant behind `SOS_TELUGU_ENABLED`, ADR-0036)
- **PDF:** HTML/CSS templates rendered by headless Chromium (Playwright) in workers; Noto Sans Telugu stays bundled but is not loaded while `SOS_TELUGU_ENABLED` is off
- **LLM:** Google Gemini on Vertex AI (regional endpoint `asia-south1`, service identity, Zero Data Retention set-up), only through `app/knowledge/gateway/` (ADR-0005, ADR-0033); provider and model per role in `app/knowledge/config/models.yaml`; Anthropic Claude kept as a config-selectable fallback
- **Embeddings:** provider interface in `app/knowledge/embeddings/`; model chosen by evaluation (ADR-0006)
- **Identity:** OIDC provider behind `app/identity/` (reference: Amazon Cognito, ADR-0012; MFA and step-up per ADR-0018); operators use a separate OIDC client
- **Infra:** Terraform, AWS ap-south-1 (Mumbai), backups copied to ap-south-2 (Hyderabad); GitHub Actions; dedicated-tier hosts run `deploy/dedicated/compose.yaml` (ADR-0015)

## 4. Repository layout

```
apps/api/app/
  core/          config, db session, tenant context, logging, errors, redaction
  identity/      OIDC integration, users, sessions
  tenancy/       tenants, academic structure (years, classes, sections)
  authz/         roles, permissions, scopes, require() dependency, policy tests
  audit/         hash-chained audit events, verification job
  students/      students, guardians, per-source attribute values, canonical view
  imports/       Excel/CSV/Sheets import (spreadsheet onboarding, 24-hour revert)
  extraction/    register-photo extraction, verification queue (provider interface, Aadhaar masking)
  dq/            data-quality rules engine, findings, name matching
  changes/       change requests (maker-checker) for identity fields
  documents/     upload, storage, versions, ACLs, virus scan hook
  knowledge/     ingestion, chunking, embeddings, retrieval, tools, prompts, gateway, evals
  exports/       board/portal pre-check sheets (CISCE, UDISE+), generic CSV/XLSX
  notifications/ in-app notifications, bilingual templates
  admin/         tenant admin, retention settings, data export
  ops/           tenant-side job runs, outbox, idempotency keys, break-glass grants
  platform/      control plane: operators, school provisioning, plans, subscriptions, invoices,
                 payments (billing), usage, fleet + heartbeat, feature flags, announcements,
                 support tickets, platform audit (DB role sos_platform; routes /api/v1/platform/*)
  devtools/      synthetic data generator (make seed-synthetic; local/ci only)
apps/api/migrations/  Alembic revisions 0001_baseline … 0049_open_items (linear chain; `alembic heads` shows the current head)
apps/api/tests/  tests per module (tests/<module>/) + cross-module suites (tests/security/, tests/migrations/)
apps/api/openapi.json  committed OpenAPI document (make openapi; freshness test)
apps/worker/     Celery entrypoint (sos_worker.celery_app; imports app.* tasks and beat schedules)
apps/web/        Next.js app (app router, BFF route handlers, i18n; operator UI under /[locale]/platform/*)
packages/api-client/  TypeScript client generated from apps/api/openapi.json
infra/terraform/ bootstrap/ + modules/ (incl. shared_platform, dedicated_host) + envs/{staging,prod,dedicated-template (one tfvars per school)}
infra/db/        bootstrap.sql: database roles, schemas, extensions (run as DB admin)
infra/docker/    local-only helpers (db init, dev OIDC stub config, SeaweedFS config)
deploy/dedicated/ compose.yaml + Caddyfile + scripts for dedicated-tier hosts
docs/            this documentation
evals/           RAG evaluation harness (package sos_evals: synthetic datasets, metrics, hard/soft gates in gates.toml; make eval)
```

Each backend module: `api.py` (routes) · `schemas.py` (Pydantic IO) · `service.py` (business logic) · `repository.py` (DB access) · `models.py` (SQLAlchemy) · `tasks.py` (Celery); its tests live in `apps/api/tests/<module>/`.
Versioned configuration ships inside the package next to the module that reads it (the API image does not copy a root `config/`): `app/authz/permissions.yaml` (the one permission catalog, tenant and `platform.*`, seeded into `core.permissions` by `0004_authz_seed`), `app/authz/roles.yaml` (tenant system roles), `app/platform/roles.yaml` (operator role matrix, two-person list), `app/platform/billing.yaml` (billing, fleet and support rules), `app/tenancy/academic_defaults.yaml`. Settings and secrets come only from `SOS_*` environment variables read by `app/core/config.py` (list: `docs/10-infrastructure-and-devops.md` §11).
Modules call other modules **only via their `service.py` public functions**. Never import another module's repository or models directly (import-linter enforces this, and forbids `core`, `identity` and `tenancy` from importing `platform`). `core`, `authz` and `audit` may be used by everyone. `platform` uses `core.db.platform_session()` for its own data and never imports tenant modules' repositories or models. `platform` may call `tenancy.service` only for tenant lifecycle (register, initialise keys, activate, suspend, reactivate, offboard, usage counts, and handing over the AI answer bundle's included answers via `set_ai_answer_allowance`, ADR-0020 B3) and never reads tenant data (ADR-0020). Its other tenant-side imports, the files that may open `tenant_session()` (school-chain audit delivery, the daily active-user and AI answer counts) and the tenant relations its SQL may name are pinned by `apps/api/tests/platform/test_boundaries.py`. School-chain copies of platform actions go through `platform.tenant_audit.enqueue()` in the platform transaction, never a direct `tenant_session` write.

## 5. Commands

```bash
make install          # uv sync --locked --all-packages + npm ci
make dev              # local stack (db, valkey, s3, migrate, api, worker, beat, web); creates .env from .env.example
make dev-host         # backing services in Docker; api, worker, beat, web on the host with reload (scripts/dev.py)
make migrate          # alembic upgrade head + audit partitions, as sos_migrator (also runs inside make dev)
make seed-synthetic   # synthetic schools, structure and staff; NEVER real data (refuses outside SOS_ENV=local|ci)
make openapi          # regenerate apps/api/openapi.json and the TS client (a test fails when it is stale)
make test             # test-api (pytest + testcontainers) + test-web (vitest)
make test-security    # security suites only (RLS catalog, isolation, route enumeration, authz matrix, BOLA)
make migration-check  # migration upgrade/downgrade round trips (fresh and populated DB)
make e2e              # playwright
make lint typecheck   # ruff, import-linter, eslint, prettier; mypy --strict, tsc
make security         # gitleaks, semgrep, pip-audit, npm audit, trivy fs + config
make eval             # RAG evaluation harness, hard gates (stub adapter until the knowledge module lands; docs/06 §13)
make check            # lint typecheck test security (what CI runs; CI adds migrations, authz-suite, terraform, images)
```

Also: `make down`, `make logs`, `make db-shell`, `make format`. The dev OIDC stub is in compose profile `dev` (`docker compose --profile dev up -d oidc`). The API image takes build arg `INSTALL_PSQL` (compose passes `SOS_INSTALL_PSQL`, default `false`). Every `SOS_*` setting, its default and the staging/prod start-up guards (no `local-dev` key wrapper, no `dev-only` secrets, no placeholder invoice supplier) are listed in `docs/10-infrastructure-and-devops.md` §11.

## 6. Non-negotiable invariants (tests enforce these; never weaken them)

1. **Tenant isolation.** Every tenant-owned table has `tenant_id uuid NOT NULL`, RLS `ENABLE` + `FORCE`, and the standard policy; references between tenant tables are composite `(tenant_id, x_id)` foreign keys. No database role has `BYPASSRLS`. Every request and job sets the transaction-local tenant context inside its transaction via `core.db.tenant_session()` (`set_config('app.tenant_id', :t, true)`, the same as `SET LOCAL`, with bound parameters). Cross-tenant access exists only through the pinned allowlist of `SECURITY DEFINER` functions owned by `sos_definer` (NOBYPASSRLS), which reach only tables carrying the `definer_access` policy (ADR-0013). The **only** schema without RLS is `platform` (control plane, no student data): it is written only by `sos_platform`, `sos_app` may only read `platform.feature_flags`, and `sos_platform` has **no** privileges on any tenant table. Never disable RLS to make a test pass.
2. **Authorization on every route.** Each route declares `Depends(require("<permission>", scope=...))` (or, for a read that any one of several permissions may use, `Depends(require_any("<permission>", "<alternative>", ...))`, whose service still checks scope per object); control-plane routes declare `Depends(require_platform("platform.<...>"))` and the fleet heartbeat declares `Depends(require_fleet_signature())`. A test enumerates all routes and fails if any lacks one of these (public health checks are the only allowlisted exceptions).
3. **Object-level access through scoped repositories.** Never fetch a student/document by ID without the caller's scope (e.g., a class teacher only sees their sections). BOLA tests exist per resource.
4. **No Aadhaar numbers, ever.** Store only `aadhaar_last4` and the as-printed demographic fields. `core.redaction` MUST mask any 12-digit sequence that passes the Verhoeff check in OCR output, extracted text, logs, prompts and exports.
5. **No PII in logs, traces, metrics, error reports or analytics.** Use structured logging with `redact()`. IDs yes, names/DOB/phones no.
6. **Never auto-correct official records.** Mismatches create `dq_findings`. Identity-field changes go through `changes` (maker-checker) with an evidence document. The admission register is the legal anchor (BR-01).
7. **Audit everything that matters** (identity data changes, role/permission changes, exports, AI queries, break-glass, logins) in the **same transaction**, via `audit.record()`; control-plane actions via `audit.record_platform()`. The audit tables are append-only (DB grants + triggers, including `TRUNCATE`).
8. **AI must be grounded.** Retrieval filters by tenant and permissions **in SQL before ranking**. The LLM never receives data the user cannot see. Answers cite the passages they were given (validated passage markers such as `[n]`, or the provider's native citations) or say "not found in school records". Citations are validated server-side: every citation must map to a passage given in that request that the caller can see, and unsupported statements are dropped or answered search-only.
9. **LLM tools are read-only in core.** Any write suggested by AI requires a human to confirm through normal endpoints.
10. **Secrets** come from environment/Secrets Manager only. Product code authenticates to AI providers with a **service identity**: Vertex AI through workload identity federation or a service-account key from Secrets Manager (organization API keys for a fallback provider), never a personal/consumer account, login or subscription. Production uses Vertex AI with the Zero Data Retention configuration (project data caching disabled, no request-response logging, abuse-monitoring logging exception requested; docs/10 §11.1).
11. **No real student data** in dev, test, staging, fixtures, screenshots, or AI coding sessions. Use `make seed-synthetic`.
12. **Migrations are backward compatible** (expand → migrate → contract). Each migration has a downgrade or is marked irreversible with reason.
13. **Model IDs, provider names, thresholds and prompts live in config/versioned files**, not inline in code.
14. **Children's data purpose limit.** Student insights (M5) are for educational activities and child safety only. No marketing, no cross-tenant analytics on identifiable data.

## 7. How to implement a user story

1. Find the story (`US-…`) in `docs/02-PRD.md` and its requirements in `docs/03-TRD.md`.
2. Restate the acceptance criteria and which invariants apply. If anything is unclear, ask before coding.
3. Write/extend tests first: unit, API (incl. authz denial + cross-tenant denial), and migration test if schema changes.
4. Implement in the owning module (routes → service → repository). Keep route handlers thin.
5. Add audit events and redaction where data is sensitive.
6. Update OpenAPI docstrings, i18n keys (`en`; `te` optional while `SOS_TELUGU_ENABLED` is off, keep existing ones), and the docs if behaviour changed.
7. Run `make check`. Paste the summary in the PR.

## 8. Definition of done

- Acceptance criteria pass as automated tests
- Authz tests: allowed role succeeds; disallowed role gets 403; other tenant gets 404
- No new PII in logs (log-redaction test covers new fields)
- Migrations upgrade and downgrade cleanly on a populated synthetic DB
- UI strings exist in `en` (`te` optional while `SOS_TELUGU_ENABLED` is off, ADR-0036; Telugu tests run with the switch on); screens usable at 1366×768 and keyboard-only
- Docs updated; ADR added if a decision changed
- CI green, including security scans and (for knowledge changes) `make eval` gates

## 9. Coding conventions (summary; full rules in docs/13)

- Python: ruff format + lint, mypy strict, Pydantic models for all IO, no business logic in routes, explicit transactions, UUIDv7 IDs, timezone-aware UTC datetimes, NFC-normalized text
- SQL: SQLAlchemy Core/ORM with bound parameters only; no string-built SQL with user input
- TypeScript: strict, no `any`, zod-validated forms, generated API client, TanStack Query
- Errors: RFC 9457 problem+json; never leak stack traces or other tenants' existence
- Commits: Conventional Commits, referencing IDs (e.g., `feat(dq): add initials rule (FR-DQ-006)`)

## 10. Frontend and browser support

- **Browser Support:** Baseline Widely Available features only unless a documented fallback exists. Targets: current Chrome/Edge on Windows 10+ office PCs, Android Chrome. Assume 1366×768 screens and slow connections.
- Before implementing a UI pattern, check current web-platform guidance (e.g., the `modern-web-guidance` tool) rather than relying on memory.
- Print is a first-class output: A4 print CSS, register formats; no clipped Telugu glyphs when `SOS_TELUGU_ENABLED` is on (while it is off nothing Telugu is printed). Fonts are self-hosted (no external font CDNs).
- Plain language, sentence case, errors that say how to fix the problem. Every AI answer shows its source chips.

## 11. Never do this

- Add microservices, Kubernetes, GraphQL, or a separate vector database in core without an ADR
- Call an LLM provider SDK outside `knowledge/gateway`
- Send whole student records to the LLM when a field suffices
- Give AI tools write access or network access
- Log request bodies, prompts or completions containing personal data
- Store files in the database or on local disk in production
- Add dependencies without checking licence (no AGPL in core), maintenance and known CVEs
- Skip tests "for now"
- Give the control plane (`platform` module, `sos_platform`) access to tenant tables, or add a `SECURITY DEFINER` function or `definer_access` policy without an ADR
- Put student data in heartbeats, support tickets, invoices or any `platform` table

## 12. When unsure

Stop, explain the trade-off, and propose an ADR (`docs/adr/ADR-XXXX-title.md`, template in `docs/adr/README.md`).
