# ADR-0004: Technology stack

| Field | Value |
|---|---|
| Status | Accepted (recorded retroactively 2026-09-26) · Amended by [ADR-0014](ADR-0014-local-ci-service-images.md) (Valkey for the Redis-protocol store; SeaweedFS for local S3) |
| Date | 2026-09-26 |
| Deciders | Founder |
| Amends / supersedes | none |

## Context

One developer builds the product with AI assistance. The stack must be mainstream (so assistants and libraries are strong), typed (so mistakes are caught early), good at NLP and document processing (Telugu, OCR, PDF), and able to render Telugu correctly in print. Browsers must never hold tokens (BFF pattern).

## Decision

| Concern | Choice |
|---|---|
| API and workers | Python 3.12+, FastAPI, Pydantic v2, SQLAlchemy 2.x (typed, sync sessions, psycopg 3), Alembic, httpx |
| Background jobs | Celery with a Redis-protocol broker; Celery beat for schedules |
| Database | PostgreSQL 16+ (ADR-0002) |
| Files | Amazon S3, private buckets, SSE-KMS, presigned URLs |
| Web | Next.js (App Router) + TypeScript strict + Tailwind; BFF route handlers; i18n `en` + `te` |
| PDF | HTML/CSS templates rendered by headless Chromium (Playwright) in workers, bundled Noto Sans Telugu |
| Identity | OIDC provider behind `app/identity/` (ADR-0012) |
| LLM | Anthropic Claude through `app/knowledge/gateway/` only (ADR-0005) |
| Infrastructure | Terraform; containers; GitHub Actions with OIDC to AWS |
| Telemetry | OpenTelemetry → CloudWatch/X-Ray (swap-able) |

Substituting any of these needs a new ADR (CLAUDE.md §3).

## Consequences

- Good: strong typing end to end (mypy strict, TypeScript strict, generated API client); large ecosystem for AI/NLP and document handling.
- Good: Chromium renders Telugu shaping and print CSS correctly.
- Bad: two languages (Python, TypeScript) to maintain; mitigated by a generated client and a shared OpenAPI contract.
- Bad: Celery's configuration surface is large; kept small by the conventions in 13 §4.

## Alternatives considered

| Concern | Alternatives | Why not chosen |
|---|---|---|
| Backend | Node/NestJS, Go | Weaker Python NLP/OCR ecosystem; Go slower to iterate for a solo developer |
| Jobs | Dramatiq, Postgres-backed queues | Celery's retries, routing and scheduling are mature |
| Frontend | Remix, SvelteKit | Next.js has the largest ecosystem and first-class BFF route handlers |
| PDF | WeasyPrint | Telugu shaping and complex print layouts less reliable |
| IaC | Pulumi, CDK | Terraform is standard, reviewable and portable |
| Telemetry | Datadog | Vendor lock-in and telemetry leaving India |

## Related requirements

03-TRD §2; 04 §14; 10 §5–7; 13 §4–5; CLAUDE.md §3.
