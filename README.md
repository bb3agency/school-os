# SchoolOS

> The memory and operations layer for Indian private schools, starting with the admin office.
> Working name · Documentation baseline v0.1 · 26 September 2026 · Region: Andhra Pradesh, India

SchoolOS lets a school office **enter student details once and use them everywhere**: board and government portal submissions, certificates, registers, parent notices, and an **"Ask the school"** assistant that answers questions from the school's own records and documents, always with sources.

The product is built **module by module** on a shared core that does not change as features grow:

| Core | What it is |
|---|---|
| **Knowledge core** | Hybrid RAG over documents + permission-aware, read-only tools over structured records, with citations |
| **Storage core** | PostgreSQL (pgvector, pg_trgm) as the system of record; S3-compatible object storage for files |
| **Security core** | Tenant isolation via Postgres Row-Level Security, RBAC with scopes, maker-checker, tamper-evident audit log |
| **Platform core** | Modular monolith (FastAPI) + workers (Celery), Next.js web app, OpenTelemetry, Terraform, GitHub Actions |

## Documentation map

| # | Document | Answers |
|---|---|---|
| 01 | [Business Requirements (BRD)](docs/01-BRD.md) | Why, for whom, business rules, objectives, KPIs, risks |
| 02 | [Product Requirements (PRD)](docs/02-PRD.md) | Core capabilities, user stories, acceptance criteria, UX principles |
| 03 | [Technical Requirements (TRD)](docs/03-TRD.md) | FR/NFR with IDs, interfaces, constraints, traceability |
| 04 | [System Architecture](docs/04-system-architecture.md) | Components, flows, deployment, scaling path, failure modes |
| 05 | [Data Model](docs/05-data-model.md) | Schema, per-source student attributes, RLS, classification, retention |
| 06 | [RAG Architecture](docs/06-rag-architecture.md) | Ingestion, retrieval, tools, prompts, citations, evaluation |
| 07 | [Security Architecture](docs/07-security-architecture.md) | Threat model, AuthN/AuthZ, RBAC matrix, encryption, LLM security |
| 08 | [Privacy & Compliance](docs/08-privacy-and-compliance.md) | DPDP Act/Rules, children's data, Aadhaar, CERT-In, processor duties |
| 09 | [API Specification](docs/09-api-specification.md) | Conventions and core endpoints |
| 10 | [Infrastructure & DevOps](docs/10-infrastructure-and-devops.md) | Environments, AWS layout, CI/CD, backups, DR |
| 11 | [Observability & Operations](docs/11-observability-and-operations.md) | Telemetry, SLOs, alerts, runbooks, incident response |
| 12 | [Testing Strategy](docs/12-testing-strategy.md) | Test layers, authz/RLS tests, RAG evals, security testing |
| 13 | [Engineering Standards](docs/13-engineering-standards.md) | Repo layout, conventions, reviews, AI-assisted development |
| 14 | [Roadmap](docs/14-roadmap.md) | Milestones M0–M7 with exit criteria |
| 15 | [Glossary](docs/15-glossary.md) | Domain, technical, platform and billing terms |
| 16 | [Platform Admin Panel](docs/16-platform-admin-panel.md) | Control plane: provisioning (shared and dedicated tiers), plans, invoices, usage, fleet heartbeat, support, operators |
| — | [ADRs](docs/adr/README.md) | Architecture decisions and why they were made (process, template, index) |
| — | [CLAUDE.md](CLAUDE.md) | Operating contract for AI coding assistants |
| — | [SECURITY.md](SECURITY.md) | Vulnerability reporting |

### Architecture decisions

| ADR | Decision | Status |
|---|---|---|
| [0001](docs/adr/ADR-0001-modular-monolith.md) | Modular monolith with async workers | Accepted |
| [0002](docs/adr/ADR-0002-postgresql-pgvector.md) | PostgreSQL + pgvector as the single store of record | Accepted |
| [0003](docs/adr/ADR-0003-pool-tenancy-rls.md) | Pool multi-tenancy with row-level security | Accepted, amended by 0013, 0015 |
| [0004](docs/adr/ADR-0004-technology-stack.md) | Technology stack | Accepted, amended by 0014 |
| [0005](docs/adr/ADR-0005-llm-gateway-and-provider.md) | LLM gateway and provider | Accepted |
| [0006](docs/adr/ADR-0006-embeddings-by-evaluation.md) | Embeddings chosen by evaluation | Accepted |
| [0007](docs/adr/ADR-0007-no-aadhaar-storage.md) | Never store Aadhaar numbers | Accepted |
| [0008](docs/adr/ADR-0008-tools-not-text-to-sql.md) | Read-only tools instead of text-to-SQL | Accepted |
| [0009](docs/adr/ADR-0009-aws-india-hosting.md) | Host on AWS in India | Accepted, amended by 0015 |
| [0010](docs/adr/ADR-0010-maker-checker.md) | Maker-checker for identity changes | Accepted |
| [0011](docs/adr/ADR-0011-hash-chained-audit.md) | Hash-chained, append-only audit | Accepted, amended by 0013 |
| [0012](docs/adr/ADR-0012-managed-oidc-identity.md) | Managed OIDC identity | Accepted, amended by 0013, 0018 |
| [0013](docs/adr/ADR-0013-cross-tenant-access-and-platform-privilege-separation.md) | Cross-tenant access paths and platform privilege separation | Accepted |
| [0014](docs/adr/ADR-0014-local-ci-service-images.md) | SeaweedFS and Valkey for local/CI | Accepted |
| [0015](docs/adr/ADR-0015-deployment-and-commercial-model.md) | Managed SaaS: shared and dedicated tiers | Accepted |
| [0016](docs/adr/ADR-0016-payments-provider.md) | Payments provider | Proposed |
| [0017](docs/adr/ADR-0017-platform-admin-panel-architecture.md) | Platform admin panel architecture | Accepted |
| [0018](docs/adr/ADR-0018-mfa-and-step-up-with-cognito.md) | MFA and step-up with Cognito | Accepted |

## Reading order

- **Starting to build (with AI):** `CLAUDE.md` → `14-roadmap.md` (M0) → `04` → `05` → `07` → `13` → ADRs 0013–0018
- **Building the platform admin panel (control plane):** `16` → ADR-0013, ADR-0015, ADR-0017 → `05` §3 → `07` §6.5–6.6 → `12` §4.8–4.13
- **Setting up a dedicated-tier school:** ADR-0015 → `10` §15 → `16` §12–13 → `11` §11
- **Building the knowledge core:** `06` → `05` (kb schema) → `07` §LLM security → `12` §RAG evaluation
- **Before the first real school data:** `08` → `07` → `10` §Backups → `14` §Pilot-ready gate
- **Talking to a school:** `01` (incl. §11 commercial model) → `02`

## Document conventions

- IDs: `BO-` business objective · `BR-` business rule · `US-` user story · `FR-` functional req · `NFR-` non-functional req · `SEC-` security control · `PRV-` privacy control · `ADR-` decision
- Normative keywords per RFC 2119: **MUST**, **SHOULD**, **MAY**
- Code and docs change in the **same PR** when behaviour changes; ADRs are never edited after acceptance, only superseded
- Facts about laws, portals, boards and vendors were checked in **September 2026**. They change. Re-verify via the References sections before relying on them. Nothing here is legal advice.

## Getting started

**Prerequisites:** Docker (with Compose v2), [uv](https://docs.astral.sh/uv/) ≥ 0.8, Node 24 LTS (22.12+ works), GNU make.

```bash
cp .env.example .env          # dev-only placeholders; never commit .env
make install                  # uv sync (Python 3.12) + npm ci (web)
make dev                      # postgres+pgvector, valkey, seaweedfs (S3), api, worker, beat, web
make migrate                  # alembic upgrade head (as sos_migrator)
make seed-synthetic           # synthetic tenants only; never real data
make check                    # lint + typecheck + tests + security scans (what CI runs)
```

- API: <http://localhost:8000/healthz>, <http://localhost:8000/readyz>, OpenAPI at `/api/v1/docs` (local only)
- Web: <http://localhost:3000> (school app at `/en` or `/te`, platform admin panel at `/en/platform`)
- Tests need Docker: they start PostgreSQL 16 + pgvector with testcontainers and run `infra/db/bootstrap.sql` + migrations, then connect as the real `sos_app` role (RLS enforced).
- Run `uv run pre-commit install` once to get ruff, mypy, eslint, prettier and gitleaks on every commit.

## Version history

| Version | Date | Author | Notes |
|---|---|---|---|
| 0.1 | 2026-09-26 | Founder | Initial documentation baseline |
