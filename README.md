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
| 15 | [Glossary](docs/15-glossary.md) | Domain and technical terms |
| — | [ADRs](docs/adr/) | Architecture decisions and why they were made |
| — | [CLAUDE.md](CLAUDE.md) | Operating contract for AI coding assistants |
| — | [SECURITY.md](SECURITY.md) | Vulnerability reporting |

## Reading order

- **Starting to build (with AI):** `CLAUDE.md` → `14-roadmap.md` (M0) → `04` → `05` → `07` → `13`
- **Building the knowledge core:** `06` → `05` (kb schema) → `07` §LLM security → `12` §RAG evaluation
- **Before the first real school data:** `08` → `07` → `10` §Backups → `14` §Pilot-ready gate
- **Talking to a school:** `01` → `02`

## Document conventions

- IDs: `BO-` business objective · `BR-` business rule · `US-` user story · `FR-` functional req · `NFR-` non-functional req · `SEC-` security control · `PRV-` privacy control · `ADR-` decision
- Normative keywords per RFC 2119: **MUST**, **SHOULD**, **MAY**
- Code and docs change in the **same PR** when behaviour changes; ADRs are never edited after acceptance, only superseded
- Facts about laws, portals, boards and vendors were checked in **September 2026**. They change. Re-verify via the References sections before relying on them. Nothing here is legal advice.

## Quick start (once code exists)

```bash
cp .env.example .env          # never commit .env
make dev                      # docker compose: postgres+pgvector, redis, api, worker, web
make migrate && make seed-synthetic
make check                    # lint + typecheck + tests + security scans
```

## Version history

| Version | Date | Author | Notes |
|---|---|---|---|
| 0.1 | 2026-09-26 | Founder | Initial documentation baseline |
