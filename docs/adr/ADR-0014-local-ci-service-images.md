# ADR-0014: Local and CI service images: SeaweedFS and Valkey

| Field | Value |
|---|---|
| Status | Accepted |
| Date | 2026-09-26 |
| Deciders | Founder (product owner approval of build proposals B1–B25) |
| Amends / supersedes | Amends [ADR-0004](ADR-0004-technology-stack.md) (Redis-protocol store and local S3) |

## Context

The v0.1 docs used `minio/minio` for local S3 and `redis:7` for the Celery broker, rate limits and cache (10 §11, 12 §2). Two facts changed (checked September 2026; re-verify before relying on them):

- **MinIO:** the project stopped publishing community container images and binaries in late 2025, and the `minio/minio` images are no longer available from Docker Hub for new versions. The server is licensed AGPL-3.0.
- **Redis:** from 7.4 Redis is dual-licensed under RSALv2 and SSPLv1; Redis 8 added AGPL-3.0 as a third option. None of these is a permissive licence.

Our licence policy forbids AGPL/SSPL in core runtime without an ADR (07 §14; 13 §9), and CI must not depend on images that may disappear. Production already uses managed services (S3; ElastiCache), so the local choice only has to be protocol-compatible.

## Decision

- **Object storage (local, CI, tests):** **SeaweedFS** (Apache-2.0) with its S3 gateway on port 8333, image `chrislusf/seaweedfs` pinned to a release tag. A one-off `s3-init` service creates the buckets (`SOS_S3_BUCKET_FILES`, `SOS_S3_BUCKET_AUDIT`). Code talks to it only through the S3 API (`SOS_S3_ENDPOINT_URL`); production uses Amazon S3.
- **Redis-protocol store (local, CI, dedicated hosts):** **Valkey 8.1** (BSD-3-Clause), image `valkey/valkey:8.1-alpine`, port 6379. Production shared tier uses **Amazon ElastiCache for Valkey**. Celery, rate limiting and caches keep using the Redis protocol (`SOS_REDIS_URL=redis://…`) and permissive client libraries.
- **Other pinned images:** `pgvector/pgvector:0.8.6-pg16-bookworm` (database), `python:3.12-slim-bookworm`, `node:24-bookworm-slim`, `ghcr.io/navikt/mock-oauth2-server` pinned (dev OIDC stub, `dev` profile only), `caddy:2` pinned (dedicated hosts). Pin by tag in compose and by digest in built images.
- **No code may use features that exist only in Redis Stack / Redis 8 modules or only in MinIO.** Integration tests run against the same images as local development.
- Any new service image needs its licence checked and recorded in the PR (13 §9).

## Consequences

- Good: local and CI stacks use permissive licences and images that are available.
- Good: production behaviour does not change (S3, ElastiCache).
- Bad: SeaweedFS is less familiar than MinIO; S3 features we rely on (presigned GET/POST with conditions, versioning, prefix listing) must be covered by integration tests. Object Lock compliance mode is verified only in staging against real S3.
- Bad: Valkey and Redis may diverge over time; we only use core commands.
- Follow-up: update 10 §11 (local stack), 12 §2 (integration layer), 14 Task 1, and the compose file.

## Alternatives considered

| Option | Why not chosen |
|---|---|
| Keep MinIO (older image or build from source) | AGPL-3.0; no maintained community images; supply-chain risk |
| LocalStack for S3 | Heavier; emulates many services we do not use |
| Garage (S3-compatible) | AGPL-3.0 |
| Keep Redis 7.2 (last BSD release) | Old branch with a limited support window; no new features or long-term fixes |
| KeyDB / Dragonfly | Smaller communities (KeyDB) or BSL licence (Dragonfly) |

## Related requirements

NFR-SEC-003, NFR-SEC-005; 04 §3, §17; 07 §14; 10 §1, §4, §11; 12 §2; 13 §9.
