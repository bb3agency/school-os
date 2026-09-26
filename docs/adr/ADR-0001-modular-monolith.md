# ADR-0001: Modular monolith with async workers

| Field | Value |
|---|---|
| Status | Accepted (recorded retroactively 2026-09-26) |
| Date | 2026-09-26 |
| Deciders | Founder |
| Amends / supersedes | none |

## Context

SchoolOS is built and run by a solo student-developer on a limited budget (01-BRD §13). The product still needs strong boundaries: tenant isolation, authorization on every route, audit in the same transaction as the change, and AI that only sees what the user may see. Early load is small (Stage 0: ≤ 5 schools, ≤ 25 concurrent users; 03-TRD §4.4). Work such as OCR, embeddings, data-quality runs and PDF rendering is slow and must not block web requests.

## Decision

- Build **one deployable backend application** (FastAPI, package `app`) split into modules with clear ownership: `core`, `identity`, `tenancy`, `authz`, `audit`, `students`, `imports`, `dq`, `changes`, `documents`, `knowledge`, `exports`, `notifications`, `admin`, `ops` (04 §4; CLAUDE.md §4). (ADR-0017 later adds `platform`.)
- Each module has `api.py` → `service.py` → `repository.py` (+ `models.py`, `schemas.py`, `tasks.py`, `tests/`). Modules call each other **only through `service.py`**. `core`, `authz` and `audit` may be used by everyone.
- Dependency rules (no cycles, no reaching into another module's repository or models) are enforced in CI with import-linter (NFR-MNT-002).
- Slow or bulk work runs in **Celery workers** from the same codebase (`apps/worker` imports `app.*` tasks), on separate queues (`ingest`, `embed`, `ocr`, `dq`, `exports`, `pdf`, `maintenance`), with a single scheduler (`beat`).
- API and workers are stateless and horizontally scalable (NFR-SCAL-001). A module MAY become a separate service later only through a new ADR, when a measured need exists (04 §11).

## Consequences

- Good: one repository, one build, one deploy, one database transaction for change + audit; simple local development; cheap to run.
- Good: module seams and service-only calls keep a later split possible without rewriting business logic.
- Bad: a bug in one module can affect the whole process; mitigated by tests, separate queues per workload, per-tenant limits and quick rollback.
- Bad: discipline is needed to keep boundaries; enforced by import-linter and review rather than network boundaries.

## Alternatives considered

| Option | Why not chosen |
|---|---|
| Microservices | Many deployables, distributed transactions and more security boundaries; too much operational load for one person (CLAUDE.md §11) |
| Kubernetes platform | Operational overhead with no benefit at Stage 0–1; ECS Fargate or a single host is enough |
| Serverless functions per endpoint | Cold starts, harder local development, awkward long-running OCR/PDF jobs, harder same-transaction audit |

## Related requirements

NFR-MNT-002, NFR-SCAL-001..003; 03-TRD §7; 04-system-architecture §1, §4, §6, §11; 13-engineering-standards §1, §4.
