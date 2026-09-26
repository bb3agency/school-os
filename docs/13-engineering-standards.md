# 13 · Engineering Standards

| Field | Value |
|---|---|
| Version | 0.2 · 2026-09-26 |
| Applies to | All code, infra, prompts and docs in this repository |
| Related | CLAUDE.md, 07-Security, 12-Testing, 16-Platform admin panel |
| Changes | 0.2: layout adds `platform/`, `deploy/dedicated/`, `infra/db/`, platform config; session-branch policy for AI-assisted work (§2); security review scope incl. `platform/` (§3); ownership and CODEOWNERS (§13). 0.1: baseline |

---

## 1. Repository layout (monorepo)

```
schoolos/
├── apps/
│   ├── api/                 FastAPI app (package: app) + Alembic migrations
│   │   ├── app/             modules (see CLAUDE.md §4)
│   │   ├── migrations/
│   │   └── tests/           cross-module suites: security/, contract/
│   ├── worker/              Celery entrypoint (imports app.*)
│   └── web/                 Next.js (app router), BFF route handlers, i18n
├── packages/
│   └── api-client/          TS client generated from OpenAPI
├── evals/                   RAG datasets (synthetic) + harness + reports
├── infra/
│   ├── terraform/           modules/ (incl. dedicated_host) + envs/ (staging, prod, dedicated/<tenant_code>)
│   └── db/                  bootstrap.sql (roles, schemas, extensions; run as DB admin)
├── deploy/
│   └── dedicated/           compose.yaml + Caddyfile for dedicated-tier hosts
├── config/                  models.yaml, dq_rules.yaml, export_profiles/, prompts index,
│                            permissions (tenant catalog + role templates), platform_permissions.yaml, billing.yaml
├── scripts/                 seed-synthetic, maintenance tools
├── docs/                    documentation, ADRs, templates
├── .github/                 workflows, PR template, CODEOWNERS
├── CLAUDE.md  README.md  SECURITY.md  Makefile  .env.example  compose.yaml
```

## 2. Git workflow

- **Trunk-based:** short-lived branches (`feat/…`, `fix/…`, `chore/…`), merged to `main` via PR within days.
- **Conventional Commits** with requirement IDs: `feat(changes): block self-approval in DB (FR-CR-002)`.
- Squash merge; PR title becomes the commit message.
- `main` is always deployable; protected with required checks and linear history.
- Tags: CalVer (`2026.10.1`); CHANGELOG generated from commits.
- **Session branches for AI-assisted work:** a build session works on one session branch (e.g., `claude/<session-name>`). When several agents work in parallel, each commits only the files it owns in its own git worktree and branch; the lead reviews and merges them into the session branch, and the session branch reaches `main` through a normal PR with all checks. Agents never push to `main` and never edit files owned by another agent; they ask the owner instead.

## 3. Pull requests

- Small (aim < 400 changed lines excluding generated files), one story or fix per PR.
- Description: what/why, requirement IDs, screenshots for UI (synthetic data), risk and rollback notes, `make check` summary.
- Template: `.github/pull_request_template.md` (security/privacy checklist included).
- **Review rules:** even as a solo developer, every PR gets a structured self-review against the checklist plus an independent AI review pass (fresh session, reviewer instructions, no write access). Changes to `core/`, `authz/`, `audit/`, `platform/`, `knowledge/gateway/`, migrations, crypto, `infra/` (incl. `infra/db/`), `deploy/dedicated/` and `config/platform_permissions.yaml` require the full security checklist and a second review sitting on a different day.

## 4. Python standards (API/workers)

- Python 3.12+; `ruff` (lint + format), `mypy --strict`; no `# type: ignore` without a reason comment.
- Layering: `api.py` (HTTP only) → `service.py` (business rules, transactions) → `repository.py` (queries). No SQL in services; no business logic in routes or repositories.
- Pydantic v2 models for all request/response and task payloads; `model_config = ConfigDict(extra="forbid")`.
- SQLAlchemy 2.x typed ORM/Core; bound parameters only; explicit `select()` columns for large tables; no lazy-loading surprises in API paths (use eager options).
- Transactions: one per request/job via `tenant_session()`; no implicit autocommit; audit in the same transaction.
- Time: `datetime` with `timezone.utc` only; convert to IST at presentation.
- Text: normalize with `unicodedata.normalize("NFC", s)` at the boundary.
- Errors: raise domain exceptions (`NotFound`, `Forbidden`, `Conflict`, `ValidationFailed`, `StepUpRequired`) mapped centrally to problem+json.
- HTTP clients: `httpx` with explicit timeouts, retries with jitter for idempotent calls, circuit breakers for providers.
- Celery tasks: idempotent, small payloads (IDs), `acks_late=True`, explicit queues, time limits.
- Config via `pydantic-settings`; no `os.environ` reads scattered in code.

## 5. TypeScript/Next.js standards

- `strict: true`, `noUncheckedIndexedAccess: true`; no `any` (use `unknown` + narrowing).
- ESLint + Prettier; React Server Components by default; client components only when interactive.
- Data fetching via generated API client + TanStack Query in client components; server-side fetches through BFF helpers that attach the session's token.
- Forms: `react-hook-form` + `zod`; server errors mapped to fields by `field` + `message_key`.
- i18n: all strings in `messages/en.json` and `messages/te.json`; ICU message format; CI fails on missing keys; no string concatenation for sentences.
- Accessibility: semantic HTML, labels for all inputs, visible focus, keyboard paths, `prefers-reduced-motion` respected.
- Browser support: Baseline Widely Available; check current guidance before adopting newer web platform features.
- Security: no `dangerouslySetInnerHTML`; CSP nonces; no tokens in `localStorage`/`sessionStorage`; no third-party scripts or font CDNs.

## 6. API design rules

- Resource-oriented nouns, plural collections; actions as sub-resources (`/change-requests/{id}/approve`).
- Every endpoint: permission declared, request/response models, examples, error codes documented.
- Backward-compatible changes only within `/v1`; never repurpose a field.
- Pagination mandatory for collections; hard caps on `limit`.
- Idempotency keys on create/start-job POSTs.

## 7. Database rules

- Every tenant table: `tenant_id`, RLS ENABLE+FORCE, policy in the same migration, `tenant_id` leading in indexes.
- Constraints in the DB for invariants (FKs, CHECKs like no self-approval, partial unique indexes like one current value).
- No destructive migration without an expand/contract plan; no data backfills inside schema migrations for large tables.
- Query review: `EXPLAIN (ANALYZE, BUFFERS)` for new heavy queries against seeded data; add to performance tests when critical.

## 8. Logging, errors, observability

- Use `core.logging.get_logger(__name__)` with structured fields; never f-string personal data into messages.
- One `INFO` business event per meaningful action; `ERROR` only for actionable failures.
- Add spans for new external calls and heavy computations; attributes allowlisted.

## 9. Dependencies

- Add only with a reason in the PR: maintenance activity, licence (permissive preferred; no AGPL/SSPL in runtime without ADR), security history, size.
- Pin with lockfiles and hashes; weekly automated update PRs; remove unused dependencies promptly.

## 10. Documentation

- Behaviour change → doc change in the same PR (PRD/TRD/API/data model as relevant).
- Decisions → ADR (`docs/adr/`, template in `docs/adr/README.md`); accepted ADRs are immutable; supersede with a new ADR.
- Public functions in services have docstrings stating permissions assumed, side effects, audit events emitted.

## 11. AI-assisted development (Claude Code and similar)

**Workflow**
1. Start each session by loading `CLAUDE.md` and the specific doc sections for the story (e.g., PRD US-601, TRD FR-CR-*, 05 §5 change_requests, 07 §6.3).
2. Ask the assistant to restate acceptance criteria, applicable invariants and the files it plans to touch **before** it writes code.
3. Work in vertical slices: tests first (including authz denial and cross-tenant cases), then implementation, then docs.
4. Run `make check` locally; paste results into the PR.
5. Review every diff line; reject changes outside the planned files unless justified.
6. For security-sensitive paths, run a separate reviewer session with the checklist in `.github/pull_request_template.md` and 07 §16.

**Rules**
- Never paste real student data, production logs, secrets, API keys or school documents into AI tools. Use synthetic data.
- Development tools (e.g., Claude Code on the developer's plan) are for writing code only; the **product** calls the LLM exclusively via organization API keys through `knowledge/gateway`.
- Don't accept generated code that disables tests, weakens RLS, broadens permissions, logs bodies, or adds dependencies silently.
- Product prompts are code: versioned files, reviewed, evaluated (`make eval`) before merge.
- Keep sessions focused; restart with fresh context for unrelated work to avoid drift.

## 12. Definition of done (engineering view)

See CLAUDE.md §8 and 12-Testing §10. In short: tests (incl. security suites) green, docs updated, i18n complete, migrations reversible, observability added, no PII in logs, reviewed against the checklist.

## 13. Ownership and CODEOWNERS

Each area has an owner who reviews every change to it (`.github/CODEOWNERS`). At Stage 0 the founder owns everything; the table records which review lens applies so it scales when people join.

| Path | Owner (role) | Review lens |
|---|---|---|
| `apps/api/app/core/`, `authz/`, `audit/` | Platform security owner | Tenant isolation, authz, audit invariants |
| `apps/api/app/platform/` | Control-plane owner | Privilege separation (ADR-0013), billing correctness, platform audit |
| `apps/api/app/knowledge/gateway/`, prompts, `config/models.yaml` | Knowledge owner | ADR-0005, evals |
| `apps/api/migrations/` | Data owner | Expand/contract, RLS, composite FKs, definer allowlists |
| `infra/terraform/`, `infra/db/`, `deploy/dedicated/` | Infrastructure owner | Least privilege, encryption, hardening (SEC-030) |
| `config/platform_permissions.yaml`, `config/billing.yaml` | Control-plane owner + security owner | Permission matrix (07 §6.5), money rules |
| `docs/` (incl. `docs/adr/`), `CLAUDE.md`, `SECURITY.md` | Documentation owner | Consistency with code and ADRs |

The `platform` module has one owner. Other modules call it only through `platform.service`; it never imports tenant modules' repositories or models.
