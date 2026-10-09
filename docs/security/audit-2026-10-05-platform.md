# Security audit 2026-10-05: platform (identity, sessions, web, infra, supply chain, ops)

Auditor C of the round-two audit, on branch `wip/sec2-platform` from
`claude/friendly-ptolemy-0tl3br` at 0236299. The scope covers:

- the OIDC flows and token validation (`apps/api/app/identity`, `apps/web/src/server/auth`);
- BFF sessions, security headers and rate limits;
- Valkey/Celery, Postgres and containers;
- Terraform against CIS AWS;
- GitHub Actions against the OpenSSF Scorecard;
- security monitoring.

Round one (`audit-2026-10-04-*.md`) is not repeated here, except where a round-one fix turned out to be
incomplete.

References:

- OWASP ASVS 4.0.3;
- OWASP API Security Top 10 (2023);
- CIS AWS Foundations v3.0;
- SLSA v1.0 and the OpenSSF Scorecard;
- CWE.

Each fixed finding has a test that failed before the fix and passes after it. Terraform tests ran
through `make tf-validate` (pinned image `hashicorp/terraform:1.16.4`).

## Findings

| ID | Severity (CVSS 3.1, why) | Reference | File:line | Exploit path | Status | Proving test |
|---|---|---|---|---|---|---|
| P2-01 | **Medium**, 6.8 (AV:A/AC:H/PR:N/UI:N/S:U/C:H/I:H/A:N). A network position in the VPC lets someone read and inject every Celery task (school ids, file keys, outbox payloads) | ASVS 9.2.1, 9.2.2; CWE-295; SEC-011 | `infra/terraform/modules/redis/main.tf:107` (before the fix); `apps/worker/sos_worker/celery_app.py:88` | The ElastiCache secret's `url` was `rediss://:<token>@host:6379/0` with no `ssl_cert_reqs`. Celery uses it as broker and backend. Kombu then connects with `ssl_cert_reqs=CERT_NONE`, so TLS without any certificate check (shown with the locked kombu: `{'ssl_cert_reqs': CERT_NONE}`). Anyone who can intercept or redirect 6379 inside the VPC (a compromised task, DNS or ENI) can impersonate Valkey: read the AUTH token and every task, and enqueue tasks that run under the worker role. The Celery result backend refuses the same URL outright ("A rediss:// URL must have parameter ssl_cert_reqs"), so the worker also failed whenever it stored a result. | **Fixed** in 34ea9ae and bdebabe. The URL now ends `?ssl_cert_reqs=required`; redis-py and node-redis verify by default, and node-redis ignores the parameter. Staging and prod settings refuse a `rediss://` URL without exactly one `ssl_cert_reqs=required`. | `modules/redis/tests` run `celery_verifies_the_valkey_certificate`; `tests/core/test_config.py::test_SEC_011_prod_rejects_a_valkey_tls_url_without_certificate_checks` (5 cases), `..._accepts_a_verified_or_internal_valkey_url` |
| P2-02 | **Medium**. Detection failure: audit tampering (P1) went unnoticed, and the only daily security alarm was permanently red | ASVS 7.2.1, 7.2.2; CWE-778; CIS AWS 4.x; docs/07 §15; SEC-007 | `infra/terraform/modules/observability/main.tf:228-262` (before) | 1. The P1 alarm `audit-chain-verification-failed` read `SchoolOS/AuditChainVerificationFailures`, and "verification missing" read `AuditChainVerificationRuns`. Nothing in the repository publishes either metric: no EMF and no `PutMetricData` (grep). 2. An insider or attacker who edits or deletes `audit.events` rows is detected by the nightly `audit.verify_all_chains`, which only logs `audit.chain.broken`. With `treat_missing_data = notBreaching`, no alarm fires. 3. "Missing" (`breaching`) fired every day from the first deploy, which trains people to ignore it. 4. The api task role may `PutMetricData` in `SchoolOS`, so a compromised api could have fed either metric. 5. Nothing else alarmed on the events docs/07 §15 lists. | **Fixed** in 9c8bac4. CloudWatch Logs metric filters on the worker log feed both alarms (`audit.chain.broken`; any `audit.chain.verified` or `broken` counts as a run), in `SchoolOS/Security/<env>`, which no task role may write (a validation keeps it apart from `custom_metric_namespace`). There are new alarms for API 401 spikes, break-glass session started, refresh-token reuse, rejected heartbeats and denied operators. A pytest pins every filtered event name and route to the code that writes it. | `modules/observability/tests` runs `audit_chain_alarms_are_fed_by_the_worker_logs` and `security_events_alarm` (failed before); `tests/deploy/test_security_log_events.py` |
| P2-03 | **Medium**. No exploit by itself, but security patches could not reach any dedicated school | CWE-1329; SLSA "build as code"; CIS 7 (patching); SEC-030 | `.github/workflows/deploy-dedicated.yml:147` | The SSM command ran `./deploy/dedicated/upgrade.sh`, which does not exist: the script is `deploy/dedicated/scripts/upgrade.sh` (package.sh, README §Upgrade). Every fleet rollout failed on every host with "not found", so a fix shipped through CI never reached dedicated hosts. | **Fixed** in 0c8e0f3. | `tests/deploy/test_workflows.py::test_SEC_030_every_repository_script_a_workflow_runs_exists` |
| P2-04 | **Medium**. Privilege escalation from the CI deploy role (staging: `main` or `staging` environment; prod: after approval) to the RDS master user, which reads every school and can turn RLS off | CIS AWS 1.15/1.16; ASVS 1.4.3, 14.1.3; CWE-269, CWE-250 | `infra/terraform/modules/ci_oidc/main.tf:77-97`; `modules/shared_platform/main.tf:822-825` (before) | The deploy role had `ecs:RunTask` on any task definition in the cluster, `iam:PassRole` on the db-bootstrap task and execution roles, and `logs:GetLogEvents` on `/schoolos/ecs/*`. db-bootstrap's execution role injects `PGPASSWORD` (the RDS master secret) and every role password. Two paths followed: (a) `ecs run-task --task-definition <db-bootstrap> --overrides '{"containerOverrides":[{"command":["env"]}]}'`; (b) a new task definition that reuses that execution role. Either way, the role then reads the output from the task log. A malicious workflow change, or a compromised action in a deploy job, gets the master password. | **Fixed** in 9ea6483. `RunTask` is limited to the listed one-off families (the migrate task), and a validation refuses `db-bootstrap`. Its roles are no longer passable. docs/10 (migrations) says an operator starts db-bootstrap. **Residual, left open:** the deploy role can still override the migrate task's command and so act as `sos_migrator`. This is inherent while CI runs migrations; see the hardening notes. | `modules/ci_oidc/tests` run `deploy_role_runs_only_the_listed_one_off_tasks` (failed before) |
| P2-05 | **Low-Medium**. Supply chain: needs a bundle without `release.env` (a hand-made or tampered bundle) or a tag-only pin | SLSA Build L2/L3 (verified, immutable references); Scorecard Pinned-Dependencies; CWE-494, CWE-829 | `deploy/dedicated/scripts/lib.sh:74-81` (before) | `render_compose_env` handled `release.env` in two unsafe ways. Without the file, it fell back to bare tags (`schoolos/api:<version>`, and the api image for the worker); Docker resolves a bare `schoolos/...` name on Docker Hub, outside our registry and namespace. With the file, it copied whatever `release.env` said without checking for a digest. | **Fixed** in a01c72a. `release.env` is required, with exactly one `SOS_API/WEB/WORKER_IMAGE=...@sha256:<64 hex>` each; otherwise the script stops before writing `compose.env`. | `tests/deploy/test_release_pins.py` (3 of 4 failed before) |
| P2-06 | **Medium** (defence in depth; needs RCE in the internet-facing web/BFF first) | ASVS 1.4.4, 14.1.x; CWE-653, CWE-284 | `deploy/dedicated/compose.yaml:48`, `:137`; `infra/terraform/modules/shared_platform/main.tf:94`, `:545`; `modules/redis/main.tf:74` | web/BFF, api, worker and beat share one Valkey credential and no ACL. Shared tier: the same `url`, db 0. Dedicated: the same password, db 1 vs 0, and `SELECT` is allowed. With code execution in the BFF, an attacker can `LPUSH` Celery messages to any queue: any registered task name with JSON arguments, run by the worker role (file discard and delete, audit archive signing, tenant jobs). That bypasses API authorization. They can also rewrite authz snapshot cache entries (`sos:authz:snap:*`), rate limits and AI budget counters. | **Fixed** in 6d6b95a0 (merged in 32029d64, wip/ob-ci-dedicated). One Valkey user per service: ElastiCache RBAC users on the shared tier, Valkey ACL users on dedicated hosts. web/BFF reaches only `sos:web:*` and its rate-limit keys, with no dangerous command, `SELECT` or pub/sub. Valkey moves to an internal `cache` network. | `tests/deploy/test_valkey_acl.py::test_P2_06_*`; `modules/redis/tests` |
| P2-07 | **Medium** | OWASP API4:2023; ASVS 2.2.1, 11.1.4; FR-IAM-005 (Partial), NFR-SCAL-003; docs/09 §1 promises `RateLimit-*` headers | `apps/api/app/core/middleware.py` (no limiter); `infra/terraform/modules/alb_waf/main.tf:252-305`; `modules/cognito/variables.tf:71`, `main.tf:88` | (a) The API has no per-user or per-tenant request limit except the knowledge ask/memory paths, the login event, invitation email and the Tally/heartbeat machine paths. Any authenticated staff member can loop on expensive routes (exports, DQ runs, PDF render, sheet previews), and each run queues worker jobs. This starves the school and its neighbours on the shared tier. The only throttle is the WAF's per-IP 3000/5 min, which also caps a whole school behind one office NAT. (b) Dedicated hosts have no WAF at all (Caddy only). (c) Passwords are typed into Cognito managed login. The pools are on the ESSENTIALS tier, so threat protection (`advanced_security_mode`) is forced OFF. No WAF is associated with the user pools, and the `/bff/auth/` rate rule never sees the credential attempts. Only Cognito's built-in lockout applies, and failed sign-ins are invisible to us (FR-IAM-005 "lockouts audited" is not met). | **(a) and (b) fixed** (owner request 2026-10-06) in d83ef8d, fce255e (API), a649369 (BFF, web) and 3f05420 (WAF, alarms): GCRA limiter in Valkey (one atomic Lua call) per client IP (trusted-proxy `X-Forwarded-For` only), per person, per school, per operator and per route, weighted writes, `RateLimit-Policy`/`RateLimit` headers, 429 `rate_limited` with `Retry-After`; sign-in and credential paths fail closed; soft lockout with exponential backoff per IP and per person + IP (API and BFF); `security.rate_limited`, `security.auth.failed`, `signin_failed`, `step_up_failed`, `auth_rate_limited` with metric filters and alarms; WAF rules answer 429 and cover the machine paths. Dedicated hosts rely on the app limits (Caddy has no built-in limiter). docs/09 §2.7, docs/07 §11.1. **(c) left open:** Cognito PLUS threat protection and a WAF ACL on the user pools are billed, an owner cost decision; failed password attempts inside Cognito managed login stay invisible to SchoolOS until then. | `tests/core/test_ratelimit.py`, `tests/api/test_rate_limits.py`, `tests/security/test_rate_limit_routes.py`, `apps/web/src/server/auth/rate-limit.test.ts`, `modules/alb_waf` and `modules/observability` tftests |
| P2-08 | **Medium** | SLSA Build L2/L3 (provenance), Scorecard Signed-Releases and Branch-Protection; CWE-494 | `.github/workflows/deploy-staging.yml:30-33`, `:136-166`; `deploy-dedicated.yml:22-23`; `modules/ci_oidc/main.tf:11`, `:264`; `deploy/dedicated/scripts/package.sh` | (a) Prod images and dedicated bundles are built and packaged by hand: no workflow runs `package.sh`. There is no build provenance or signature (cosign or `attest-build-provenance`), and the SBOM is only a 90-day workflow artifact. Hosts check only a SHA-256 that comes from the same S3 prefix as the bundle. (b) The deploy roles trust `environment:<name>`. `workflow_dispatch` can run from any ref, so a branch with a modified workflow reaches the environment. Only an environment reviewer, and branch rules configured outside the repository (not visible here), stand in the way. Staging deploys on `push: main` with no `needs:` on CI. | **Fixed** in 66baf982 (merged in 32029d64, wip/ob-ci-dedicated). (a) `release.yml` on CalVer tags pushes images by digest, attests build provenance and the SBOM, and runs `package.sh`; `deploy-dedicated` verifies the bundle attestation and passes the release's SHA-256 to `upgrade.sh --sha256`. (b) The deploy roles pin `job_workflow_ref`, and staging deploys only after CI passes. **Left open:** the GitHub environment deployment-branch rules and branch protection are owner settings outside the repository (docs/10 §8). | `tests/deploy/test_supply_chain.py::test_P2_08_*`; `modules/ci_oidc/tests` run `deploy_role_pins_job_workflow_ref` |
| P2-09 | **Low** (staging; synthetic data) | Round-one W3-04 is incomplete; CIS 1.16; CWE-200 | `infra/terraform/modules/ci_oidc/main.tf:180-185`, `:200-207` | The PR plan role (any same-repo pull request in staging) "reads no secrets" since W3-04, but it still has AWS `ReadOnlyAccess` and `s3:GetObject` on the state bucket. `aws_cognito_user_pool_client.client_secret` is stored in state, and `cognito-idp:DescribeUserPoolClient` returns it, so the role reads the staff, operator and support client secrets. It can also read every log group (`security-events`, WAF) and Lambda code. | **Fixed** in f60c55a0 (merged in e4afcc9c, wip/ob-infra-aws). Staging's plan role trusts only the reviewed `staging-plan` GitHub Environment, and every plan role is denied CloudWatch Logs data reads. Residual (docs/10 §7): an approved plan still reads the client secrets in the state. | `modules/ci_oidc/tests` runs `plan_role_never_reads_log_data`, `gated_plan_role_never_reads_log_data`; `envs/staging/tests` run `pr_plan_role_reads_no_secrets` |
| P2-10 | **Low**. Deployment blocker, found during the review | CWE-1188 | `infra/terraform/modules/redis/main.tf:48-53`; `modules/shared_platform/main.tf:212` | The Valkey slow-log group was encrypted with the data CMK, whose policy has no CloudWatch Logs grant (`allow_cloudwatch_logs` is set only on the logs key). `CreateLogGroup` would fail on the first apply (mock-provider tests cannot see this). | **Fixed** in 34ea9ae and 9c8bac4: new variable `log_kms_key_arn`, wired to the logs CMK. | `modules/shared_platform/tests` run `worker_image_and_upload_encryption` (asserts `module.redis.slow_log_kms_key_arn` is the logs key) |

Known from earlier rounds (owner decisions recorded, not re-reported):

- CIS CloudWatch.1-14 metric filters are not added. Root sign-in and IAM events reach EventBridge
  only in us-east-1, and no rule exists there. The SEC-023 notes list both. **Fixed in 1699c508**
  for the events: they are forwarded from us-east-1 and page through EventBridge rules. **Left
  open:** the metric filters themselves need a billed CloudWatch Logs copy of the trail (docs/10).
- The prod COMPLIANCE lock. **Left open:** it is written in `envs/prod`, but prod has not been
  applied yet; applying it is an owner step.

P2-02 adds application-level alarms. It does not close the CIS account-level ones.

## Verified clean (with the test that proves it)

**OIDC (BFF client, openid-client v6)**
- state, nonce and PKCE S256 are generated per sign-in and kept in an AES-GCM sealed, kind-bound,
  10-minute `__Host-` cookie. A mismatched state, a mismatched nonce, an injected code with the wrong
  verifier, or a tampered or missing transaction is refused. Proved by the `handlers.test.ts` cases:
  - "rejects a state mismatch"
  - "rejects a nonce mismatch in the ID token"
  - "rejects a PKCE verifier mismatch"
  - "rejects a missing or tampered transaction cookie"
- **Redirect URI exactness:**
  - The callback URL is rebuilt from `APP_BASE_URL`, never from Host (`handlers.ts:400`).
  - `redirect_uri` comes from config.
  - Cognito callback URLs equal the BFF routes (`shared_platform` run `oidc_redirects_match_the_bff`).
  - `safeNext` blocks open redirects (`redirect.test.ts`).

**API token validation (`app/identity/tokens.py`)**
- **Algorithm pinning:**
  - Only RS256/ES256 are accepted; `none` and HS256 signed with the public key are refused, and
    `alg` must equal the JWK's algorithm.
  - Symmetric or weak JWKS entries are ignored, and a `kid` with the wrong key is refused.
  - An unknown `kid` causes at most one refetch per 60 s.
  - Proved by `tests/identity/test_tokens.py::test_SEC_004_*` (14 tests).
- **iss/aud/azp and client separation:**
  - Each verifier forbids the other clients' ids.
  - Cognito ID tokens (`token_use=id`) are refused.
  - Support tokens need `client_id` plus `token_use=access` plus MFA.
  - The operator admin client is never accepted on school routes, and support or staff tokens never
    on `/platform/*`.
  - Proved by `test_tokens.py::test_FR_IAM_001_*` and `tests/identity/test_support_tokens.py`.
- **Clock skew:**
  - The leeway is 30 s.
  - `nbf` and `iat` in the future, and `auth_time` in the future, are refused.
  - A lifetime over 15 min is refused.
  - Proved by `test_FR_IAM_004_*` and `test_FR_IAM_003_auth_time_in_the_future_is_rejected`.
- **Step-up:**
  - `require_recent_auth` needs MFA and `auth_time` no older than 300 s (30 s skew), on tenant
    routes, platform routes and break-glass.
  - The BFF forces `prompt=login&max_age=0`.
  - Refresh keeps the original `auth_time`, so it cannot freshen a step-up.
  - Proved by `handlers.test.ts` "refuses a stale auth_time on step-up" and the authz-matrix step-up
    tests.

**Sessions (BFF, `src/server/session/store.ts`)**
- **Opaque id and timeouts:**
  - The session id is 256 bits; only its SHA-256 is stored, and tokens are sealed at rest.
  - The idle timeout is 15 min (5-30 per school; fixed for operators).
  - The absolute timeout is 12 h for staff and 8 h for operators and support.
  - A new session id is issued at every sign-in and step-up.
  - Proved by `store.test.ts`.
- **Cookies:** `__Host-` prefix, `Secure; HttpOnly; SameSite=Lax; Path=/`, no `Domain`, no
  `Max-Age` (browser session). http only on loopback (`handlers.test.ts` "drops Secure and the
  __Host- prefix only for http://localhost").
- **Refresh tokens:** rotation, a single-flight lock, and family revocation on reuse
  (`refresh.test.ts`).
- **Logout:**
  - Needs CSRF and the same Origin.
  - Deletes the server-side session keys, revokes the refresh token at the IdP (RFC 7009; Cognito
    revokes the access tokens it issued), and ends the IdP session.
  - Proved by `handlers.test.ts` "requires CSRF, revokes server-side ...".
- **Revocation on suspension, removal or role removal:** the next API request with a still-valid
  token fails. The membership status is read on every request through `core.resolve_login`, and a
  role change drops the cached permission snapshot when it commits. Proved by the new
  `tests/api/test_session_revocation.py` (4 tests, all passed before any change: verified clean).
  A deactivated operator is refused on the next request too, because `load_operator` reads
  `platform.operators` every time.

**Headers and DoS limits**
- **Security headers on every response type:**

  | Response type | What it gets |
  |---|---|
  | Pages and BFF | nonce CSP, nosniff, COOP, CORP, HSTS (proxy.ts) |
  | BFF JSON | `Cache-Control: no-store` |
  | API `/api/v1/*` | `no-store` unless the route sets its own |
  | File downloads | `attachment` with a server-built filename, `nosniff` and `no-store` |
  | Presigned S3 GETs | forced `attachment` and the verified content type |
  | Print pages (memo, certificate, register) | a hash-only CSP, re-checked by `isStrictPolicy` |
  | `/_next/static` | nosniff, Referrer-Policy, CORP and COOP (`next.config.ts`) |

- **JSON depth:** a body nested 100,000 or 400,000 levels deep is answered 400 "error parsing the
  body" in 2 ms (FastAPI + json). There is no RecursionError and no 500.
- **Body size:** 1 MiB at the BFF and the API (round one AA-08).
- **Pagination:** `Limit` is capped centrally (`app/authz/http.py`).

**Infra**
- Postgres (shared):
  - `sslmode=verify-full` with the RDS CA bundle;
  - `rds.force_ssl=1` and TLS 1.2 minimum;
  - only `sos_migrator`, `sos_app`, `sos_platform` and `sos_readonly` can log in; owner, definer and
    purger are NOLOGIN;
  - no BYPASSRLS;
  - `statement_timeout` is set per transaction by the app;
  - `idle_in_transaction_session_timeout` is 60 s.
- Valkey (shared): TLS required, KMS at rest, AUTH token write-only.
- Containers:
  - non-root (10001, `node`);
  - `readonlyRootFilesystem`;
  - `cap_drop ALL`;
  - `no-new-privileges`;
  - seccomp and AppArmor for the PDF worker;
  - asserted in `ecs_service` and the dedicated compose tests.
- The dedicated compose publishes only Caddy's 80/443, and the data network is `internal`. The
  Caddy admin API listens only on loopback inside its container.
- CIS AWS:
  - CloudTrail is multi-region, with validation, a CMK and Object Lock;
  - GuardDuty, Config and Security Hub (FSBP and CIS v3) run in both regions;
  - account and bucket Block Public Access, TLS-only bucket policies;
  - KMS rotation, with no wildcard principals;
  - RDS encryption, 14-day backups and deletion protection;
  - WAF managed rule groups with logging and redaction.

  These are asserted by the module tests.
- GitHub Actions:
  - top-level permissions are `contents: read` or `{}`, and only deploy jobs get `id-token: write`;
  - every action is pinned by SHA (`test_workflows.py`);
  - no `pull_request_target` or `workflow_run`, and no `${{ github.event.* }}` in `run:`;
  - `persist-credentials: false`;
  - zizmor and actionlint run in CI;
  - tool binaries are checked by SHA-256.

## Hardening (no exploit path found, or needs another area)

- **Deploy role and `sos_migrator`** (P2-04 residual): the deploy role can override the migrate
  task's command and act as `sos_migrator` (which can `SET ROLE sos_owner`). Consider running
  migrations from a fixed entrypoint that ignores overrides (an ECS `entryPoint` with no command,
  plus a role policy `ecs:RunTask` condition on `ecs:task-definition` revisions built by the
  release pipeline), or from the apply role. **Left open:** inherent while CI runs migrations;
  since 66baf982 only the pinned deploy workflows can assume the deploy role.
- **Logout everywhere:** "your sessions" lets a person revoke each session, but there is no
  one-click "sign out everywhere" and no limit on concurrent sessions (ASVS 3.3.4 is met per
  session). Suspension stops API access at once (above), but the BFF sessions stay until they
  time out. **Fixed in bef68867 and f6df992f.** "Sign out everywhere" revokes every session at the
  BFF and the IdP, a person keeps at most 5 sessions per kind, and an API 403 `no_membership` or
  `not_operator` ends the BFF session at once. Tests: `apps/web/src/server/auth/handlers.test.ts`
  "signs out everywhere ..." and "keeps at most MAX_SESSIONS_PER_PERSON sessions ...";
  `apps/web/src/server/bff/proxy.test.ts` "ends the session when the API says the membership is gone ...".
- **ID tokens with non-Cognito IdPs:** with an IdP that puts the client id in `aud` and sets no
  `token_use`, an ID token would pass as an access token. Not reachable today: the BFF never
  forwards ID tokens, and Cognito's `token_use=id` is refused. Consider requiring `typ=at+jwt` or
  `token_use=access` once the IdP is fixed. **Fixed in 2cb46a5e.** Only tokens marked `typ=at+jwt`
  or `token_use=access` are accepted. Tests: `tests/identity/test_tokens.py::test_FR_IAM_001_a_token_not_marked_as_access_token_is_rejected`,
  `::test_FR_IAM_001_access_token_marked_by_typ_or_token_use_is_accepted`.
- **TLS guard for Postgres:** `Settings` does not require `sslmode=verify-full` on the database
  URLs in the shared tier. Terraform writes it today (`modules/secrets/main.tf:41`). Adding the
  guard needs the prod `Settings` fixtures in about 20 test modules updated. **Fixed in 2d4cfdc7
  and c2f4142a.** Shared-tier staging and prod require `sslmode=verify-full` on the database URLs.
  Tests: `tests/core/test_config.py::test_SEC_011_shared_prod_requires_verify_full_tls_to_postgres`
  and the two other `test_SEC_011_*` cases from that commit.
- **Role timeouts:** `sos_readonly` has no `statement_timeout` or idle timeout, and the dedicated
  Postgres has no server-wide `statement_timeout`. **Fixed in 1df4e83e** (merged in e4afcc9c).
  `sos_readonly` gets statement, idle and idle-session timeouts; `sos_app` and `sos_platform` get a
  5-minute backstop; the dedicated Postgres sets `statement_timeout` server-wide. Tests:
  `tests/security/test_role_timeouts.py`, `tests/deploy/test_db_timeouts.py`.
- **db-bootstrap passwords in argv:** on dedicated hosts, `psql -v app_password=...` puts the
  passwords in `/proc/*/cmdline` while it runs (`deploy/dedicated/compose.yaml:280-287`). Use
  `\getenv` (psql 16). **Fixed in b6951402** (merged in 32029d64). db-bootstrap reads the role
  passwords with psql `\getenv`, on both tiers. Tests:
  `tests/deploy/test_dedicated_hardening.py::test_hardening_dedicated_db_bootstrap_reads_passwords_with_getenv`,
  `::test_hardening_shared_db_bootstrap_reads_passwords_with_getenv`.
- **Network segmentation:** web sits on the `data` network on dedicated hosts and could reach
  `db:5432` (it holds no DB password). Use a separate `cache` network. **Fixed in 6d6b95a0**
  (merged in 32029d64). Valkey sits on an internal `cache` network and web leaves `data`. Test:
  `tests/deploy/test_dedicated_hardening.py::test_hardening_web_cannot_reach_the_database_network`.
- **Caddy:**
  - It runs as root (`NET_BIND_SERVICE` only, read-only root).
  - Server timeouts are not set explicitly.
  - HSTS `includeSubDomains; preload` is also sent on a school's custom domain. If that is an apex
    domain, every subdomain of the school's domain is forced to HTTPS for 2 years. A product owner
    should confirm.

  **Fixed in b6951402** (merged in 32029d64). Caddy runs as 10001 with no capability, sets
  explicit server timeouts, and sends HSTS preload only on our own host name. Tests:
  `tests/deploy/test_dedicated_hardening.py::test_hardening_caddy_runs_as_a_non_root_user`,
  `::test_hardening_caddy_sets_server_timeouts`, `::test_hardening_hsts_preload_only_on_our_own_domain`.
- **ALB TLS policy:** `ELBSecurityPolicy-TLS13-1-2-2021-06` allows TLS 1.2 CBC suites (the
  round-one note "TLS 1.3-only" is inaccurate; the code allows 1.2 by design). The `-Res-` variant
  is stricter. **Fixed in b73529fc** (merged in e4afcc9c). The default is
  `ELBSecurityPolicy-TLS13-1-2-Res-2021-06`, and only `-Res-` or TLS 1.3 policies are accepted.
  Tests: `modules/alb_waf/tests` runs `tls_and_redirect`, `tls_policy_without_cbc_is_required`.
- **CIS account-level gaps:**
  - no IAM Access Analyzer;
  - no account password policy (break-glass IAM users);
  - no security alternate contact;
  - no EBS snapshot or AMI public-access block;
  - no Macie on the files bucket (children's PII);
  - Inspector / ECR enhanced scanning is not enabled.

  **Fixed in 1699c508** (merged in e4afcc9c): IAM Access Analyzer, an account password policy, and
  EBS snapshot and AMI public-access blocks in both regions. Test: `modules/security_baseline/tests`
  run `cis_account_guardrails`. **Left open:** the security alternate contact needs a real security
  mailbox and phone (owner step). Macie and Inspector are billed, an owner cost decision (docs/10).
- **Confused-deputy conditions:** `aws:SourceAccount` is missing on the ALB log delivery statement
  (`modules/s3/main.tf:47`), on the alarms SNS policy, and on several service trust policies
  (ecs-tasks, flow logs, RDS monitoring, DLM). **Fixed in d372acf4** (merged in e4afcc9c). Each of
  these now accepts only this account. Tests: `ecs_service` run
  `task_roles_trust_only_this_accounts_ecs`, `network` run `flow_log_role_trusts_only_this_account`,
  `rds` run `monitoring_role_trusts_only_this_account`, `dedicated_host` run
  `dlm_role_trusts_only_this_account`, `observability` run `alarm_topic_accepts_only_this_account`,
  `s3` run `alb_log_delivery_pins_the_account`.
- **Rotation and key separation:**
  - No Secrets Manager rotation for the DB role passwords, the service token key, the session
    secret or the Valkey token (manual version bumps only). **Left open:** automatic rotation needs
    a rotation function per secret and coordinated restarts; manual version bumps stay for now.
  - The prod data CMK is also the artifacts key that every dedicated host may decrypt; give the
    artifacts bucket its own key. **Fixed in 5e9d7592** (merged in e4afcc9c). The artifacts bucket
    has its own CMK, and `modules/s3` refuses the data key. Tests: `modules/s3/tests` runs
    `data_buckets_use_cmk`, `artifacts_key_is_not_the_data_key`.
- **Scorecard:**
  - no CodeQL and no `dependency-review-action`;
  - semgrep runs, but there is no SARIF upload;
  - `pip install uv==...` and `uvx semgrep/zizmor` are pinned without hashes;
  - Dependabot lists `/apps/worker`, which has no Dockerfile.

  **Fixed in e10529d5 and 66baf982** (merged in 32029d64): CodeQL, dependency review, the semgrep
  SARIF upload, hash-pinned semgrep and zizmor, uv copied from a digest-pinned image, and the
  Dependabot entry removed. Tests: `tests/deploy/test_supply_chain.py::test_scorecard_*`. **Left
  open:** npm `min-release-age` needs npm 11.10 or later.
- **Detection gaps:**
  - WAF has no Bot Control, ATP or anonymous-IP rules;
  - the `SizeRestrictions_BODY` count leaves bodies over 8 KB uninspected;
  - flow logs record REJECT only;
  - the S3 gateway endpoint has no policy.

  **Fixed in b73529fc and 94085583** (merged in e4afcc9c): a WAF rule blocks bodies over 8 KB
  unless they are JSON; flow logs record all traffic; the S3 gateway endpoint reaches only this
  account's buckets and the AWS-owned ones it needs. Tests: `modules/alb_waf/tests` run
  `oversized_non_json_bodies_are_blocked`; `modules/network/tests` runs `flow_logs_enabled`,
  `s3_endpoint_reaches_only_this_accounts_buckets`. **Left open:** WAF Bot Control, ATP and the
  anonymous-IP list are billed, an owner cost decision (docs/10).
- **Dedicated hosts have no security alarms:** the observability module is shared-tier only, and
  host logs ship to CloudWatch without metric filters. Reuse the P2-02 filters on
  `/schoolos/dedicated/<id>` once host log groups are per service. **Fixed in aa6315aa and
  0ca5c655** (merged in e4afcc9c). The P2-02 and P2-07 metric filters run on each host log group
  and alarm the prod on-call topic. Tests: `modules/dedicated_host/tests` runs
  `security_alarms_on_the_host_log_group`, `security_alarm_topic_required`;
  `tests/deploy/test_dedicated_security_alarms.py`.

## Checks run

- `make tf-validate ONLY="modules/redis modules/observability modules/shared_platform modules/ci_oidc envs/staging envs/prod"`:
  fmt and validate pass, and all tests pass (redis 4, observability 4, shared_platform 30, ci_oidc 8,
  staging 7, prod 11). Before each fix, the new run failed.
- `uv run lint-imports` (38 kept); `uv run mypy apps/api apps/worker evals` (no issues);
  `ruff check` and `ruff format --check` in apps/api (clean).
- pytest on tests/core, tests/security, tests/migrations, tests/deploy, tests/api, tests/identity,
  tests/audit and tests/platform. The result is in the final report.
- No web code changed. No OpenAPI change.
