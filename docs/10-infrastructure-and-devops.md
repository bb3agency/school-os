# 10 · Infrastructure & DevOps

| Field | Value |
|---|---|
| Version | 0.5 · 2026-09-27 |
| Cloud | AWS ap-south-1 (Mumbai) primary · ap-south-2 (Hyderabad) backups/DR |
| Tooling | Terraform · Docker · GitHub Actions · OpenTelemetry |
| Related | 04-Architecture §11, §16–17, 07-Security §13–14, 11-Operations, 16-Platform admin panel §12–13, ADR-0014, ADR-0015, ADR-0018 |
| Changes | 0.5: §11 lists the documents, malware-scan, SSE-KMS, presign and extraction settings (a test now checks every setting is listed). 0.4: edge access logs keep full URLs, so no personal data in query strings; Caddy and WAF log redaction (§4, §15.2; SEC-008). 0.3: Terraform module and root list as built (§5); CI jobs as in `ci.yml` (§7); migrate + partition step (§9); local stack, make targets and every `SOS_*` setting from `config.py` (§11). 0.2: dedicated tier (§15: Terraform module `dedicated_host`, `deploy/dedicated/compose.yaml`, Caddy TLS, WAL-G/`pg_dump` backups to ap-south-2, hardening, patching, fleet upgrades); local compose with SeaweedFS, Valkey, `migrate`, `beat`, OIDC stub and `infra/db/bootstrap.sql` (§11); Valkey and two Cognito pools (§4–5, ADR-0018); fleet step in CD (§8). 0.1: baseline |

---

## 1. Environments

| Env | Purpose | Data | Deploys |
|---|---|---|---|
| **local** | Development (docker compose) | Synthetic only | Developer |
| **ci** | Ephemeral test services per PR (Postgres+pgvector, Valkey, SeaweedFS; ADR-0014) | Synthetic fixtures | Every PR |
| **staging** | Integration, E2E, load, ZAP, demo | Synthetic only (never production copies) | Auto on merge to `main` |
| **production** (shared) | Shared-tier schools + control plane | Real data | Manual approval after staging passes |
| **production** (dedicated) | One host per dedicated-tier school | Real data (one school per host) | Fleet waves after the shared release (§15.5) |

## 2. AWS account structure

| Stage | Accounts |
|---|---|
| Stage 0 (pilot) | Management (billing, Identity Center) · `staging` · `prod` |
| Stage 1+ | + `log-archive` (CloudTrail, audit archive copies, Object Lock) · + `security` (GuardDuty/Security Hub delegated admin) · + `backup` (cross-account AWS Backup vault) |

SCPs: restrict regions to ap-south-1/ap-south-2 (global services excepted), deny disabling logging/detection, deny public S3, deny root usage.

Security logging by stage (SEC-023, §5.1): at **Stage 0** each workload account (`staging`, `prod`) runs its own multi-region account trail, GuardDuty, Config and Security Hub from `envs/<env>` (`modules/security_baseline`); logs stay in that account's Object Lock bucket in ap-south-1. The management account is not managed by this Terraform: enable a trail there by hand (it records Organizations, IAM Identity Center and billing activity) until Stage 1. At **Stage 1** an organization trail in the management account delivers to `log-archive` and GuardDuty/Security Hub use `security` as delegated administrator; the per-account trails are removed only after the organization trail has covered the retention period. Dedicated-tier hosts run in the prod account (ADR-0015, §15), so the prod baseline covers them.

## 3. Network

```
VPC 10.20.0.0/16 (ap-south-1, two AZs)
├── public   10.20.0.0/24, 10.20.1.0/24    ALB, NAT (Stage 1+)
├── app      10.20.10.0/24, 10.20.11.0/24  ECS tasks: web, api, worker, beat
└── data     10.20.20.0/24, 10.20.21.0/24  RDS, ElastiCache for Valkey (no internet route)
Endpoints: S3 (gateway), ECR, Secrets Manager, KMS, CloudWatch Logs (interface; add when cost-justified)
```

- Security groups: ALB → web (443→3000), web → api (internal port), app → RDS (5432), app → Valkey (6379). Nothing else inbound.
- Egress: third-party APIs (LLM, embeddings, OCR, IdP) via NAT; Stage 1 adds a domain allowlist (egress proxy or firewall rules).
- **Cost note (Stage 0):** a NAT gateway is a notable fixed monthly cost. Options: single NAT in one AZ; or tasks in public subnets with public IPs and security groups allowing **only** ALB inbound (acceptable for pilot with strict SGs); revisit at Stage 1.

## 4. Services by stage

| Component | Stage 0 · Pilot | Stage 1 · Growth | Stage 2 · Scale |
|---|---|---|---|
| Compute | ECS Fargate: 1 task each (web, api, worker, beat); `worker-pdf` (queue `pdf`) on one small EC2 instance whose Docker daemon allows the Chromium sandbox (§6, ADR-0025) | Autoscaling per service; worker pools per queue | + Spot for batch workers; ingestion service split if needed |
| DB | RDS PostgreSQL single-AZ, PITR, gp3 | Multi-AZ, RDS Proxy/PgBouncer, read replica | Partitioning, silo large tenants, larger instances |
| Cache/queue | ElastiCache for Valkey (small) | Replication group with failover | Cluster mode |
| Files | S3 + versioning + lifecycle | + cross-region replication for documents to ap-south-2 | Same |
| Edge | ALB + ACM + WAF | + CloudFront for static assets | Same |
| Identity | Two Cognito user pools in ap-south-1 on the Essentials plan: staff (MFA optional, `sos:mfa` claim) and operators (MFA on); pre-token-generation Lambda; access tokens 10 min (ADR-0018) | Same | Same |
| Observability | CloudWatch Logs/Metrics, X-Ray via OTel collector sidecar | + dashboards, synthetic checks | + tracing sampling policies |
| Backups | RDS automated (14 days) + daily snapshot copy to ap-south-2 | AWS Backup cross-account vault, 35-day PITR | Warm standby option |

**Edge access logs keep full URLs (SEC-008).** The ALB writes an access log line for every request to the access-log bucket (prefix `alb/`), including the complete path and query string; ALB access logs have no field filter or redaction. CloudFront logs (Stage 1+) do the same. So personal data (names, phone numbers, emails, dates of birth, addresses, admission numbers, Aadhaar-like input, free-text searches) must **never** be sent in a URL: every endpoint, including new ones, takes such values in a JSON body (searches: `POST …/search`, e.g. `POST /api/v1/students/search`), and query strings carry only IDs, codes, enums, record dates, cursors and page sizes (09 §2, 13 §6, 07 §11). WAF logs (CloudWatch `aws-waf-logs-*`) redact the `authorization`, `cookie` and `x-service-token` headers and the whole query string.

## 5. Terraform

```
infra/terraform/
├── bootstrap/                remote-state bucket + CMK per AWS account (run once, local state)
├── modules/
│   ├── network/              VPC, public/app/data subnets in two AZs, endpoints, NAT
│   ├── rds/                  PostgreSQL 16 (pgvector), parameter group, TLS required, PITR 14 days
│   ├── rds_backup_replication/  automated-backup replication to ap-south-2 (DR provider)
│   ├── redis/                ElastiCache for Valkey 8 (TLS, AUTH token via write-only attributes)
│   ├── s3/                   shared-tier buckets (files, audit archive with Object Lock), lifecycle by tag
│   ├── s3_bucket/            hardened private bucket used by every stack
│   ├── kms/                  CMKs (data, audit, backup, logs; annual rotation) + asymmetric audit-signing keys, key policies
│   ├── secrets/              Secrets Manager secrets (ephemeral generation, write-only values)
│   ├── ecr/                  repositories (immutable tags, scan on push, KMS)
│   ├── ecs_cluster/          Fargate cluster (+ optional EC2 capacity providers), Container Insights, ECS Exec audit, Service Connect
│   ├── ecs_service/          hardened Fargate service or one-off task (migrate, db-bootstrap); EC2 mode for worker-pdf
│   ├── ecs_ec2_capacity/     EC2 capacity provider for the pdf queue: ECS AL2023 ASG, daemon seccomp profile for the Chromium sandbox (ADR-0025)
│   ├── alb_waf/              ALB (TLS 1.2+), WAFv2 managed + rate rules; optional fleet heartbeat route
│   ├── cognito/              staff and operator user pools (Essentials), app clients, pre-token Lambda (ADR-0018)
│   ├── observability/        alarms, SNS topic, log groups (400 days), budget
│   ├── security_baseline/    SEC-023 per account: CloudTrail + Object Lock log bucket, evidence bucket, Config role, alerts (§5.1)
│   ├── security_detection/   SEC-023 per region: GuardDuty, AWS Config, Security Hub (FSBP + CIS), forwarding to ap-south-1
│   ├── ci_oidc/              GitHub OIDC provider + deploy roles
│   ├── shared_platform/      composition of the above for one shared-tier environment
│   └── dedicated_host/       one school's EC2 host, KMS key, buckets, IAM, secrets, log group (§15)
├── envs/
│   ├── staging/              shared_platform + security_baseline (GOVERNANCE, 180 days), small sizes, synthetic data only
│   ├── prod/                 shared_platform + DR copies + security_baseline (COMPLIANCE, 400 days); asserted by tests
│   └── dedicated-template/   one backend config + tfvars per school under schools/<code>.* (module dedicated_host)
└── scripts/validate.sh
```

Each module and root has `terraform test` files (`tests/*.tftest.hcl`). CI runs `terraform fmt -check`, `init -backend=false` + `validate`, `tflint` and `trivy config` on every root; **no plan or apply has run against AWS yet** (14 · M0 status). Locally, `make tf-validate` runs fmt, init, validate and `terraform test` on every root in the official `hashicorp/terraform` image at CI's version (1.16.4, pinned by digest; `ONLY="modules/s3 …"` limits the roots).

- **Files-bucket lifecycle.** Keys start with the tenant (`t/<tenant_id>/…`) and S3 lifecycle filters match only a literal prefix, so expiring categories are selected by the object tag `sos-lifecycle`, which the app sets in the upload itself (`ObjectStore.put(…, lifecycle=…)`). Export files get `export-7d`: rule `exports-7d` expires them after 7 days and their noncurrent versions after 1 day (docs/05 §13), as a backstop to the daily purge job. `discarded` is set by `ObjectStore.discard` (PRV-016). The rules `tenant-export-2d` and `import-raw-90d` exist but no upload sets their tags yet.

- Remote state in S3 (versioned, encrypted) with locking; separate state per env.
- Planned: `terraform plan` on PR (posted as comment); `apply` only from the pipeline with approval. Today CI validates only (§7).
- Checks: `terraform fmt/validate`, `tflint`, Trivy/Checkov IaC scan (no public buckets, encryption on, logging on).
- Mandatory tags: `project`, `env`, `owner`, `data_class`, `cost_center`.
- Log group retention: security/access/app logs **400 days** (≥ 13 months) in ap-south-1.
- App container settings: `shared_platform` gives the `api`, `worker`, `worker-pdf`, `beat` and `migrate` tasks the same base settings (`local.app_env` + `local.app_base_secrets`; `migrate` adds `SOS_MIGRATOR_DATABASE_URL`, `api`/`worker` add the provider API keys; `worker-pdf` gets none) under the names in §11, so each task passes the staging/prod start-up guards. The shared tier needs `billing_supplier_legal_name` and `billing_supplier_gstin` (validated GSTIN whose first two digits equal `billing_supplier_state_code`, default `37`); the staging tfvars example uses a synthetic supplier that is not valid for tax invoices. `apps/api/tests/deploy/test_env_contract.py` parses the HCL maps and `deploy/dedicated/compose.yaml` and fails when a name is not a setting or a container would be refused at start-up.
- Audit archive signing (FR-AUD-004): an asymmetric `audit-signing` KMS key (`ECC_NIST_P256`, `SIGN_VERIFY`, `ECDSA_SHA_256`) per shared environment and per dedicated host; only the worker task role (shared) and the host's instance role (dedicated) may `kms:Sign`/`kms:GetPublicKey`. AWS KMS does not rotate asymmetric keys: replacing one means a new key and keeping the old public key to verify older archives.

### 5.1 Security logging and detection (SEC-023)

`modules/security_baseline` (which calls `modules/security_detection` once per region) is instantiated only by `envs/staging` and `envs/prod`; its `env` input accepts nothing else, so it never runs locally or in CI. ap-south-2 resources use the AWS provider's per-resource `region` argument (provider 6.x), so one provider with `allowed_account_ids` guards both regions. Planned against mocks by `terraform test`; **never applied yet**.

| Control | Setting |
|---|---|
| CloudTrail | One multi-region account trail `sos-<env>-trail`: management events (read and write, global services included), S3 object-level (data) events on the files and audit-archive buckets, and in prod on every bucket named `sos-ded-*` (dedicated hosts' files buckets in ap-south-1 and backup buckets in ap-south-2); log file validation on; SSE-KMS with `alias/sos-<env>-security-logs` (annual rotation). The log buckets are never data-event sources (a precondition refuses it) |
| Log archive | `sos-<env>-cloudtrail-<account>` in ap-south-1: S3 Object Lock default retention (prod **COMPLIANCE, 400 days**; staging **GOVERNANCE, 180 days**), versioned, SSE-KMS, Block Public Access, TLS 1.2+ only, server access logs to the environment's logs bucket (`s3/`), lifecycle deletes versions after retention. The bucket policy denies `s3:DeleteObject`, `s3:DeleteObjectVersion`, `s3:BypassGovernanceRetention` and `s3:DeleteBucket` to every principal (staging may exempt a named teardown role, `security_log_delete_exempt_principal_arns`) |
| Evidence | `sos-<env>-security-evidence-<account>`: AWS Config snapshots and history (`config/`) and exported GuardDuty findings from both regions (`guardduty/`; GuardDuty itself keeps findings 90 days), same key, deny-delete policy and retention. No Object Lock: these are evidence, not the log of record, and not every AWS delivery service writes the checksums Object Lock requires |
| GuardDuty | Both regions, findings every 15 min, every protection plan set explicitly. ap-south-1: S3 Protection, Malware Protection for EC2 (EBS; dedicated hosts), RDS Protection, Lambda Protection; Runtime Monitoring off unless `guardduty_runtime_agent_management` lists `ECS_FARGATE_AGENT_MANAGEMENT`/`EC2_AGENT_MANAGEMENT`. ap-south-2: S3 Protection. EKS off (no EKS) |
| AWS Config | Both regions: recorder for all supported resource types (global IAM types in ap-south-1 only), continuous recording, KMS-encrypted delivery to the evidence bucket through role `sos-<env>-config-recorder`. Managed rules in ap-south-1: CloudTrail enabled, multi-region, log validation and encryption; root MFA and no root access key; IAM console MFA; account and bucket S3 public access, TLS-only and encryption; RDS encrypted, not public, snapshots not public; EBS default encryption and encrypted volumes; IMDSv2; no SSH from the internet; KMS rotation; GuardDuty and Security Hub enabled; VPC flow logs |
| Security Hub | Both regions, consolidated control findings, standards AWS Foundational Security Best Practices v1.0.0 and CIS AWS Foundations Benchmark v3.0.0 (default standards off, list explicit); ap-south-2 findings aggregated into ap-south-1 |
| Guardrails | Account-level S3 Block Public Access; EBS encryption by default in both regions |
| Alerts | EventBridge rules in ap-south-1 → SNS `sos-<env>-security-alerts` (same CMK) → `security_alert_emails` (defaults to `alarm_emails`): GuardDuty severity ≥ 7 in either region (ap-south-2 forwards GuardDuty findings and tampering API calls to the ap-south-1 default bus); new, active Security Hub findings labelled CRITICAL that are not GuardDuty (add `HIGH` via `securityhub_alert_labels` once the first-run backlog is triaged); API calls that stop or blind logging and detection; policy, lifecycle, lock, logging or encryption changes on the log buckets. Routing and severities: 11 §6; response: R5 (CERT-In 6-hour clock) |

**Retention.** CERT-In Directions (April 2022) require ICT system logs for 180 days **within India**; the DPDP Rules require at least one year of logs for breach investigation; SchoolOS keeps security logs 400 days (same as the log groups, 08 §6). The module refuses less than 180 days. Everything is stored in ap-south-1 (India). COMPLIANCE mode in prod means nobody, the root user included, can delete or shorten retention; the bucket (and the account) cannot be emptied until the last object expires. GOVERNANCE in staging lets an exempted role with `s3:BypassGovernanceRetention` tear the account down.

**After the first apply (runbook).** Confirm each SNS email subscription; check Security Hub's first findings and record accepted exceptions; if GuardDuty, Config or Security Hub was already enabled by hand in the account, `terraform import` the detector, recorder, delivery channel and hub before applying; run `aws cloudtrail validate-logs` once to prove digest validation.

**Known gaps (owner decisions).** Root console sign-in and IAM API events are delivered to EventBridge only in us-east-1, which the region guardrail excludes, so they are recorded and checked by Config/Security Hub but not paged in real time. The trail does not send to CloudWatch Logs, so CIS controls CloudWatch.1-14 (metric filters and alarms) will fail until either CloudWatch Logs delivery is added (billed per GB ingested) or those controls are disabled with a recorded reason. GuardDuty Malware Protection for S3 (scanning new uploads to the files bucket) is not enabled: it overlaps the upload antivirus hook (SEC-016) and is billed per GB scanned.

**Cost (Stage 0, estimate; check current ap-south-1 pricing).** Per account: one KMS key (about USD 1/month) plus requests; CloudTrail's first copy of management events is free and S3 data events cost about USD 0.10 per 100,000 events (every presigned upload or download of a school file is one); GuardDuty is billed by the volume of CloudTrail, VPC flow, DNS and S3 events analysed (small at pilot scale; 30-day free trial per region); Config about USD 0.003 per configuration item recorded plus rule evaluations; Security Hub per control check and per finding ingested beyond the free tier; S3 storage of compressed logs is small. Expect tens of US dollars per month per account at pilot volume; the monthly budget alert (§12) covers surprises. `config_recording_frequency = "DAILY"` lowers Config cost if needed.

## 6. Containers

- Multi-stage Dockerfiles; slim/distroless bases; pinned digests. Base and service images per ADR-0014 (`python:3.12-slim-bookworm`, `node:24-bookworm-slim`, `pgvector/pgvector:0.8.6-pg16-bookworm`, `valkey/valkey:8.1-alpine`, pinned `chrislusf/seaweedfs`, pinned `caddy:2`).
- Run as non-root; read-only root FS; `/tmp` as tmpfs; drop Linux capabilities.
- `apps/api/Dockerfile` builds two Python images from one base (`python:3.12-slim-bookworm`, uv-built venv, UID 10001, `INSTALL_PSQL=true` for release images, RDS CA bundle): target `api` (default; api, migrate, db-bootstrap) and target `worker` (worker and beat; ECR repository `worker`, Terraform `worker_image_repository`, dedicated `SOS_WORKER_IMAGE`). The worker adds chrome-headless-shell at the revision the locked Playwright expects (1194 for 1.56.0; `tests/deploy/test_worker_image.py` fails when they diverge) with its Debian libraries, read-only under `/opt/ms-playwright`; the renderer never downloads a browser and serves the bundled Noto Sans Telugu itself. CI renders a synthetic Telugu PDF in the built worker image as UID 10001 on a read-only root with all capabilities dropped and no network (`infra/docker/worker-pdf-smoke.py`), then renders it again with the Chromium **sandbox on** (`infra/docker/worker-pdf-sandbox-check.sh`): it must fail under Docker's default seccomp profile and succeed under the worker profiles below, with the AppArmor profile loaded on the Ubuntu 24.04 runner and `kernel.apparmor_restrict_unprivileged_userns=1`. A ClamAV service arrives with the module that needs it (M3).
- **Chromium sandbox ([ADR-0025](adr/ADR-0025-chromium-sandbox-for-pdf-rendering.md), accepted).** `pdf.chromium_sandbox` stays `true`: staging, prod and dedicated hosts render only with the sandbox. Its first layer needs an unprivileged user namespace, which Docker's default seccomp profile refuses (and Fargate forbids outright). The worker profile `deploy/dedicated/security/seccomp-worker.json` is Docker's default (moby/profiles) minus the flag-filtered `clone` rules plus `chroot`, `clone` and `unshare` (measured: without `chroot` or `unshare` the sandbox still fails; `setns` stays `CAP_SYS_ADMIN`-only); `derive-seccomp.py` regenerates it. Capabilities stay dropped, so outside the sandbox's own namespace nothing new is allowed.
  - Shared tier (option A): the Fargate `worker` consumes every queue except `pdf` (`worker_queues`, validated). `worker-pdf` (`-Q pdf`, `--concurrency=2`, no provider keys, the api's task role) runs on the `ecs_ec2_capacity` capacity provider: an Auto Scaling group of ECS-optimized Amazon Linux 2023 instances (default `t4g.medium`, 1 to 2 instances; AMI from the public SSM parameter, a new AMI rolls out through an instance refresh on the next apply), IMDSv2 with hop limit 1, no key pair (SSM only), no inbound rules, root volume encrypted with the data CMK. User data makes the worker profile **dockerd's default** (`daemon.json` `seccomp-profile`; task definitions cannot name one), checks `docker info` reports it and only then starts the ECS agent (tasks blocked from IMDS, no privileged containers, awslogs and image pulls via the execution role); on any failure the instance masks the agent and powers off. Only `worker-pdf` is placed there (capacity provider strategy + `memberOf(attribute:schoolos.seccomp == chromium-sandbox)`), with `no-new-privileges`, read-only root, UID 10001, all capabilities dropped and tmpfs `/tmp`. Add `sos-<env>-worker-pdf` to the deploy pipeline's service list (`ecs_services` output). Needs NAT (or interface endpoints): awsvpc tasks on EC2 get no public IP.
  - Dedicated tier (option D): see §15.2.
- RDS CA bundle: `infra/certs/rds-global-bundle.pem` is Amazon's public RDS certificate bundle (certificates only, no keys), committed so clean image builds work (`.gitignore` excludes other `*.pem`). Source `https://truststore.pki.rds.amazonaws.com/global/global-bundle.pem`, fetched 2026-09-27 (108 certificates, all `O=Amazon Web Services`, sha256 `e5bb2084ccf45087bda1c9bffdea0eb15ee67f0b91646106e466714f9de3c7e3`). `tests/deploy/test_rds_ca_bundle.py` fails if it goes missing or contains anything but certificates. Refresh it when AWS publishes new RDS CAs (download from the same URL, check the subjects, update the hash here).
- Health endpoints: `/healthz` (liveness), `/readyz` (DB, Valkey reachable).
- Images tagged with git SHA; SBOM attached; Trivy scan must pass (no critical CVEs) before push to ECR.

## 7. CI pipeline (GitHub Actions)

`.github/workflows/ci.yml` runs on pull requests, pushes to `main`, merge groups and manually. Every job calls the same `make` target a developer runs:

| Job | Runs |
|---|---|
| `lint` | `make lint` (ruff, ruff format check, import-linter, eslint, prettier) |
| `typecheck` | `make typecheck` (mypy strict, tsc) |
| `test (test-api)` / `test (test-web)` | `make test-api` (pytest + testcontainers: Postgres + pgvector, Valkey, SeaweedFS) / `make test-web` (vitest) |
| `migrations` | `make migration-check` (fresh and populated upgrade/downgrade round trips) |
| `authz-suite` | `make test-security` (RLS catalog, isolation, route enumeration, authz matrix, BOLA, definer functions, composite FKs) |
| `security` | `make security` (gitleaks full history, semgrep, pip-audit, npm audit, trivy fs + config) |
| `ci-config` | semgrep rule tests, actionlint, zizmor |
| `terraform` | fmt, validate, tflint, trivy config on every Terraform root |
| `images (api, worker, web)` | docker build, SPDX SBOM (syft), trivy image scan (HIGH/CRITICAL, fixable); nothing pushed |
| `ci-ok` | The single required check: fails if any job failed or was cancelled |

`nightly.yml` (02:47 IST) runs `make test`, `make test-security`, `make migration-check`, `make e2e`, `make security`, a ZAP baseline against staging (skipped until staging is configured) and `make eval`. The RAG eval subset per PR and `terraform plan` comments arrive with the knowledge module and AWS accounts.

- Required check on `main`: `ci-ok` (branch protection not yet configured); squash merges; Conventional Commit titles.
- Concurrency groups cancel superseded runs; caches for pip/npm.
- Secrets in GitHub Environments; AWS via OIDC roles scoped to branch/environment.

## 8. CD and releases

1. Merge to `main` → **staging deploy** (`deploy-staging.yml`): build, SBOM and scan each image, push to ECR → one-off ECS migrate task (`sos_migrator`: `alembic upgrade head` + audit partitions) → roll ECS services → smoke test. Planned additions: Playwright E2E, k6 smoke; ZAP baseline runs nightly.
2. **Production**: manual approval (GitHub Environment) → migrations (expand-only by policy) → rolling deploy with health checks and circuit-breaker rollback → post-deploy smoke → release notes.
3. **Rollback:** redeploy previous task definition; migrations are backward compatible so code rollback is safe; contract migrations only after a full release cycle.
4. **Windows:** production deploys outside school hours (after 18:00 IST or Sundays) unless hotfix; schools notified 48 h ahead of maintenance with expected impact.
5. **Versioning:** CalVer releases (`2026.10.1`) + CHANGELOG; feature flags decouple deploy from release.
6. **Dedicated fleet:** after production (shared) is healthy, the `deploy-dedicated` workflow upgrades dedicated hosts in waves (§15.5) and sets their `target_version` in the fleet registry.
7. **System-role sync (post-migration step, ADR-0022).** When a release changes the system roles in `apps/api/app/authz/roles.yaml` (the pinned fingerprint test `tests/authz/test_system_role_fingerprint.py` makes the PR say so), existing schools get the change only when an operator runs `python -m app.identity.sync_system_roles` **after** that release's migrations. The command connects as `sos_app` (`SOS_DATABASE_URL`) and refuses any other role, a superuser or BYPASSRLS role, and a database whose `core.permissions` lacks a roles.yaml permission (migrations not applied). It lists schools with `core.list_tenant_ids` (provisioning, active, suspended; offboarding and deleted are skipped) and handles each in its own `tenant_session`.
   - **Dry run first** (default; read-only): it prints one line per school (`tenant=<id> result=in_line|pending|failed` with counts) and one line per change (`+ <role> <permission>`, `- …` with `--prune`, `= …` stale grant kept, `~ <role> display names`, `! <role>` conflict with a custom role, `? <role>` unknown system role), IDs and keys only. Check that the grants match the release notes.
   - **Apply:** `--apply` adds missing system roles and grants and updates display names; `--prune` also removes grants roles.yaml no longer lists (removing access can lock staff out: only when the release notes ask for it, after a `--prune` dry run). Custom roles and `platform_support` are never changed. Every change is audited in the school's chain (`role.permission_granted`, `role.permission_revoked`, `role.created`, `role.updated`, `role.system_sync_applied`; actor `system`). A second `--apply` changes nothing. `--tenant <id>` limits the run to one school.
   - **Exit codes:** `0` in line or applied · `1` refused (nothing done) · `2` invalid arguments · `3` dry run found changes · `4` a school failed or has a conflict (the others were still processed; re-run after fixing, it is idempotent).
   - **Shared tier (staging, then production):** a one-off ECS task from the current **worker** task definition (its `SOS_DATABASE_URL` is `sos_app`; the migrate task's `sos_migrator` is refused) with a command override, in the same network configuration as the services. The container is named `app` in every task definition (`modules/ecs_service` `container_name`); output goes to the task's CloudWatch log stream (`/schoolos/ecs/sos-<env>-worker`, stream prefix `app`):
     ```bash
     td=$(aws ecs describe-services --cluster "$CLUSTER" --services "$WORKER_SERVICE" --query 'services[0].taskDefinition' --output text)
     net=$(aws ecs describe-services --cluster "$CLUSTER" --services "$WORKER_SERVICE" --query 'services[0].networkConfiguration' --output json)
     aws ecs run-task --cluster "$CLUSTER" --task-definition "$td" --launch-type FARGATE \
       --network-configuration "$net" --started-by "ops-role-sync" \
       --overrides '{"containerOverrides":[{"name":"app","command":["python","-m","app.identity.sync_system_roles"]}]}'
     # read the log, then repeat with "command":["python","-m","app.identity.sync_system_roles","--apply"]
     ```
   - **Dedicated tier:** automatic (ADR-0026). `upgrade.sh` runs `scripts/sync-system-roles.sh --apply` after the release's migrations on every upgrade (§15.5), never `--prune`; any exit other than `0` fails the upgrade and rolls the host back. By hand (`sudo /opt/schoolos/deploy/dedicated/scripts/sync-system-roles.sh`, dry run by default) only for a dry run, a reviewed `--prune`, or a re-run after fixing a conflict. It runs the command in a one-off `api` container of the active release and handles only `SOS_DEDICATED_TENANT_ID`.
   - **Local:** `make sync-system-roles` (dry run) or `make sync-system-roles ARGS="--apply"`.

## 9. Database operations

- Bootstrap: `infra/db/bootstrap.sql` runs once per database as the admin user (RDS master user via an ECS one-off task; compose init locally; testcontainers in CI; the dedicated-host bootstrap). It creates roles (`sos_owner`, `sos_migrator`, `sos_app`, `sos_platform`, `sos_readonly`, `sos_definer`; none with BYPASSRLS), schemas (`core`, `sis`, `kb`, `audit`, `ops`, `platform`) and extensions (05 §3).
- Migrations: Alembic via one-off ECS task as `sos_migrator` (`SET ROLE sos_owner`); `lock_timeout` and `statement_timeout` set; large index builds `CONCURRENTLY`. The same task then runs `python -m app.audit.partitions --months-ahead 12` to keep `audit.events` partitions 12 months ahead (05 §7.1). The db-bootstrap one-off task (`psql -f infra/db/bootstrap.sql` as the RDS master user) runs before it and after role-password rotation.
- Parameters: `log_min_duration_statement` (e.g., 500 ms, no bind values logged), `pg_stat_statements`, `idle_in_transaction_session_timeout`, SSL required.
- Backups: automated backups with PITR (14 days Stage 0, 35 days Stage 1+); daily snapshot copied to ap-south-2, retained 30 days; monthly snapshot retained 12 months (encrypted, access-restricted).
- **Restore drill (quarterly):** restore PITR to a new instance in staging account (via snapshot share), run integrity checks (row counts, audit chain verification), record timings against RPO/RTO.
- Maintenance: autovacuum tuning for high-churn tables (`attribute_values`, `document_chunks`), HNSW index monitoring, alert (`audit.partitions.low_runway`) when `audit.events` partitions cover less than 90 days ahead.

### 9.1 Runbook: rotate a school's data encryption key (SEC-012, 07 §8)

Rotation gives one school a new DEK version and moves its C3 values to it; old versions are retired once nothing uses them. It is an operator action per school (`python -m app.tenancy.rotate_keys`); **when** to rotate (cadence, incident triggers) and **who** may start it are not yet decided (see 07 §8). The command connects as `sos_app` (`SOS_DATABASE_URL`) and refuses any other role, a superuser or a BYPASSRLS role; it works only on an `active` or `suspended` school, inside that school's own `tenant_session` (no definer function). Output: IDs, key versions, key id and counts only. Run it like the system-role sync (§8 step 7): shared tier as a one-off ECS task from the **worker** task definition with a command override; dedicated tier in a one-off `api` container of the active release (`--tenant` defaults to `SOS_DEDICATED_TENANT_ID`); locally with `uv run python -m app.tenancy.rotate_keys`.

1. **Check** (dry run, writes nothing): `python -m app.tenancy.rotate_keys --tenant <id>` prints each key version (`current`/`active`/`retired`, key id, created) with how many stored values use it, and the plan (`add key_version=N`).
2. **Rotate:** `--apply` (add `--new-hmac-key` only for incident response: it also replaces the blind-index HMAC key; by default the HMAC key is carried over so guardian-phone lookups keep matching). One transaction adds the key version (fresh DEK wrapped by `SOS_KEY_WRAPPER`, KMS with the tenant id as encryption context), audits `tenant.key.rotated` and queues `keys.rotated`. New writes use the new version at once in that process and within 15 minutes (key cache TTL) in every other API and worker process.
3. **Re-encrypt:** a worker picks up `keys.rotated` and runs `maintenance.reencrypt_tenant` (maintenance queue) in batches of 200 rows, 100 batches per task run, queuing its own continuation (`keys.reencrypt_requested`) until done. Each batch is one transaction (rows locked, re-encrypted with the same associated data, `tenant.key.reencrypted` audited with counts, progress written). Progress: the `ops.job_runs` row with `task_name = 'maintenance.reencrypt_tenant'` and `idempotency_key = 'dek-reencrypt-v<N>'` (`progress.total`, `progress.remaining`; `status` `running`/`succeeded`/`failed`). A crash loses only the open batch; the job is idempotent, so rerun or wait for the next event. Without a worker, or to sweep values written by processes that still had the old key cached, run it in the task: `--reencrypt --apply` (`--batch-size` 1..1000). Wait at least 16 minutes after step 2 and run `--reencrypt --apply` once more before retiring; `remaining=0` means every listed ciphertext column uses the current version.
4. **Retire:** `--retire` (dry run) then `--retire --apply`. A version is retired only if it is not current, the census (ciphertext header of every column in `app.students.rotation.CIPHERTEXT_COLUMNS`) finds no value using it, and the current version is older than 16 minutes; otherwise it is kept with `values=<n>` or `reason=key_cache_window`. Audit `tenant.key.retired`. Retiring only stops the version from being used for writes: the wrapped key row stays and still decrypts (restored backups), and KMS material is never destroyed by this command. Destroying keys is the offboarding crypto-shredding step (08 §7).
5. **Exit codes:** `0` done / nothing to do · `1` refused (nothing done) · `2` invalid arguments · `3` dry run found work, or work remains (values on older versions, retirement not yet allowed) · `4` failed (error code printed; rerun after fixing, every step is idempotent).

Values under `kb.queries` are counted by the census but re-encrypted only once the knowledge module registers its re-encryptor (`rotation.register_reencryptor`); until then a version they use is kept (reported as `values=<n>`).

## 10. Disaster recovery

| Scenario | Strategy | Target |
|---|---|---|
| AZ failure | Multi-AZ RDS and ECS across two AZs (Stage 1+) | Minutes |
| Data corruption / bad migration | PITR to timestamp before incident; replay safe events from outbox if needed | RPO ≤ 15 min, RTO ≤ 4 h |
| Region impairment (ap-south-1) | Restore from snapshot copies + S3 replicas in ap-south-2 via Terraform `envs/dr` | RTO ≤ 24 h (Stage 1), ≤ 4 h with warm standby (Stage 2) |
| Account compromise | Cross-account backup vault with separate credentials; Object Lock audit archive | Recover from immutable copies |

## 11. Local development

The local stack (`docker-compose.yml`, project name `schoolos`) runs the shared deployment (tenant app + control plane) with permissive-licence images pinned by digest (ADR-0014). Every port is bound to `127.0.0.1`.

| Service | Image | Port | What it does |
|---|---|---|---|
| `db` | `pgvector/pgvector:0.8.6-pg16-bookworm` | 5432 | PostgreSQL; on first start `infra/docker/db-init.sh` runs `infra/db/bootstrap.sql` with the role passwords from `.env` |
| `valkey` | `valkey/valkey:8.1-alpine` | 6379 | Celery broker, rate limits, BFF sessions (DB 1), caches; no persistence |
| `s3` | `chrislusf/seaweedfs` (pinned digest) | 8333 | S3-compatible storage (`infra/docker/seaweedfs/s3.json`) |
| `s3-init` | API image | — | One-off: creates `SOS_S3_BUCKET_FILES` and `SOS_S3_BUCKET_AUDIT` |
| `migrate` | API image | — | One-off: `alembic upgrade head && python -m app.audit.partitions --months-ahead 12` as `sos_migrator` |
| `api` | API image (`apps/api/Dockerfile`) | 8000 | FastAPI (tenant and platform routes; `SOS_DEPLOYMENT_MODE=shared`); starts after `migrate` and `s3-init` succeed |
| `worker` | API image | — | `celery -A sos_worker.celery_app worker -Q ingest,embed,ocr,dq,exports,pdf,maintenance --concurrency=2` |
| `beat` | API image | — | `celery -A sos_worker.celery_app beat` |
| `web` | `apps/web/Dockerfile` | 3000 | Next.js school app, platform route group and BFF (`API_INTERNAL_URL=http://api:8000`, `REDIS_URL=redis://valkey:6379/1`) |
| `oidc` (profile `dev`) | `ghcr.io/navikt/mock-oauth2-server:6.0.3` | 8080 | Dev OIDC stub for staff (`/schoolos`) and operators (`/platform`), config `infra/docker/oidc.json` (both issuers assert MFA, so a developer types only a subject); never in staging/prod (`tests/deploy/test_dev_oidc_stub.py`). Not started by `make dev`: use `docker compose --profile dev up -d oidc` |

All Python services share one image (`schoolos-python:dev`) with a read-only root filesystem, `/tmp` as tmpfs, `no-new-privileges` and all capabilities dropped. The image's build argument `INSTALL_PSQL` (default `true`; release images always install `postgresql-client` for the ECS db-bootstrap task) is set from `SOS_INSTALL_PSQL` in compose (default `false`, because the `db` container runs `bootstrap.sql` itself).

**Commands** (`make help` lists them; CI runs the same targets):

| Target | What it does |
|---|---|
| `make install` | `uv sync --locked --all-packages` and `npm ci` |
| `make dev-host` / `dev-stop` | Backing services (db, valkey, s3, oidc) in Docker and the app processes on the host with reload (`scripts/dev.py`: api :8000, worker, beat, web :3000). Idempotent setup first: buckets, `alembic upgrade head` + audit partitions as `sos_migrator`, synthetic seed (`ARGS=--no-seed` skips it). Host processes use `localhost` URLs, including the OIDC issuer `http://localhost:8080/...` (`oidc.localhost` does not resolve outside browsers on Windows). `SOS_DB_PORT` / `SOS_VALKEY_PORT` in `.env` move the host ports when another project uses 5432/6379. `ARGS=--raw` shows every log line unformatted. After setup it prints a "Sign in as" list (synthetic subjects from `app.devtools.plan`) and the dev sign-in page <http://localhost:3000/en/dev/sign-in> (404 unless `next dev` and a loopback/`*.localhost` issuer). Sign-in: type a subject at the stub (it asserts MFA for every token; no claims JSON); step-up: paste "Copy step-up claims" from that page into the stub's claims box (`apps/web/README.md`). Production MFA and step-up are unchanged: the stub runs only here and the API refuses its issuers outside `local`. `dev-stop` stops the containers and keeps their volumes |
| `make dev` / `down` / `logs` | Start (creating `.env` from `.env.example` if missing; builds, waits for health), stop, tail the stack. Migrations run as part of `make dev` (`migrate` service) |
| `make migrate` | Starts `db` and runs the `migrate` service (migrations + audit partitions) |
| `make db-shell` | `psql` as the local admin |
| `make seed-synthetic` | `python -m app.devtools.seed_synthetic`: deterministic synthetic schools, structure and staff; each first owner created through the production path (owner invite → activate → invitation acceptance). Refuses unless `SOS_ENV` is `local` or `ci` |
| `make openapi` | Writes `apps/api/openapi.json` (shared mode, sorted) and regenerates `packages/api-client` |
| `make test` | `test-api` (pytest with coverage; real Postgres via testcontainers, or `SOS_TEST_ADMIN_DATABASE_URL`) + `test-web` (vitest) |
| `make test-security` | `pytest apps/api/tests/security` (RLS catalog, isolation, route enumeration, authz matrix, BOLA, definer functions, composite FKs) |
| `make migration-check` | `pytest apps/api/tests/migrations` (fresh and populated round trips) |
| `make e2e` | Playwright (after `next build`, or against `E2E_BASE_URL`) |
| `make lint` / `format` / `typecheck` | ruff (+ format check), import-linter, eslint/prettier / auto-format / mypy strict + tsc |
| `make security` | gitleaks (full history), semgrep (`.semgrep` + p/python, p/typescript, p/owasp-top-ten), pip-audit, `npm audit --audit-level=high`, trivy fs + config (pinned container images when the tools are not installed) |
| `make eval` | Placeholder until the knowledge module exists (M2) |
| `make check` | `lint typecheck test security` |

**API and worker settings** (`apps/api/app/core/config.py`, the only code that reads the environment; prefix `SOS_`, unknown variables ignored). In `staging`/`prod` the process refuses to start with `SOS_KEY_WRAPPER=local-dev`, with a `dev-only` value in `SOS_DATABASE_URL`, `SOS_PLATFORM_DATABASE_URL`, `SOS_SERVICE_TOKEN_KEY`, `SOS_ANTHROPIC_API_KEY` or `SOS_EMBEDDINGS_API_KEY`, with the placeholder invoice supplier name or GSTIN, with `SOS_KB_PROVIDER_MODE=fake`, or with `SOS_KB_ENABLED=true` and no `SOS_ANTHROPIC_API_KEY`.

| Variable | Default | Purpose |
|---|---|---|
| `SOS_ENV` | `local` | `local`, `ci`, `staging`, `prod` (staging/prod: production guards on, API docs off) |
| `SOS_DEPLOYMENT_MODE` | `shared` | `dedicated` removes control-plane routes and schedules |
| `SOS_SERVICE_NAME`, `SOS_VERSION`, `SOS_LOG_LEVEL` | `api`, `0.0.0-dev`, `INFO` | Telemetry identity and log level |
| `SOS_DATABASE_URL` | local `sos_app` URL | Tenant role (RLS) |
| `SOS_PLATFORM_DATABASE_URL` | local `sos_platform` URL | Control-plane role |
| `SOS_MIGRATOR_DATABASE_URL` | local `sos_migrator` URL | Alembic and the partition CLI |
| `SOS_DB_POOL_SIZE` | 10 | Connection pool per engine |
| `SOS_DB_STATEMENT_TIMEOUT_MS`, `SOS_WORKER_STATEMENT_TIMEOUT_MS` | 5000, 120000 | Transaction-local statement timeouts (requests; long worker jobs) |
| `SOS_REDIS_URL` | `redis://localhost:6379/0` | Valkey |
| `SOS_S3_ENDPOINT_URL`, `SOS_S3_BUCKET_FILES`, `SOS_S3_BUCKET_AUDIT` | none, `sos-local-files`, `sos-local-audit-archive` | Object storage (endpoint only for SeaweedFS) |
| `SOS_S3_PRESIGN_ENDPOINT_URL` | none | Endpoint used only to sign browser-facing presigned URLs (locally `http://localhost:8333`); unset in staging/prod |
| `SOS_S3_KMS_KEY_ID` | none | KMS key for SSE-KMS on uploaded files (FR-DOC-003): presigned POST policies require it and server writes send it. Shared tier: the data CMK (the files bucket's key); dedicated: `SOS_KMS_DATA_KEY_ARN` (the host key). Unset locally (SeaweedFS) |
| `SOS_DOCUMENTS_MAX_UPLOAD_BYTES`, `SOS_DOCUMENTS_IMPORT_MAX_UPLOAD_BYTES` | 25 MiB, 10 MiB (at most 100 MiB) | Largest document / spreadsheet import upload (FR-DOC-001, SEC-016) |
| `SOS_DOCUMENTS_UPLOAD_URL_TTL_S`, `SOS_DOCUMENTS_DOWNLOAD_URL_TTL_S` | 600, 300 (at most 600, 300) | Presigned POST and GET lifetimes (FR-DOC-004) |
| `SOS_DOCUMENTS_ALLOWED_KINDS`, `SOS_DOCUMENTS_IMPORT_ALLOWED_KINDS` | `pdf,jpg,png,docx,xlsx`, `xlsx,csv` | File kinds accepted by content sniffing (never by extension) |
| `SOS_AV_SCANNER` | `dev-noop` | `clamav` or `dev-noop`; a `dev-noop` scan refuses to run in staging/prod (FR-DOC-002) |
| `SOS_CLAMAV_HOST`, `SOS_CLAMAV_PORT`, `SOS_CLAMAV_TIMEOUT_S` | `localhost`, 3310, 30 | clamd connection when `SOS_AV_SCANNER=clamav` |
| `SOS_EXTRACTION_PROVIDER` | unset: `fake` in local/ci, `not-configured` in staging/prod | Register-photo extraction provider (FR-IMP-024); `fake` refuses to run in staging/prod |
| `SOS_EXTRACTION_LOW_CONFIDENCE_THRESHOLD` | 0.8 | Fields read below this confidence are highlighted for the reviewer (US-402 AC4) |
| `SOS_KB_ENABLED` | `false` | Knowledge module master switch ("Ask the school", M2); a school also needs the feature flag `kb.ask.enabled`. In staging/prod `true` requires `SOS_ANTHROPIC_API_KEY` |
| `SOS_KB_PROVIDER_MODE` | unset: `fake` in local/ci, `live` in staging/prod | `fake` = offline deterministic providers (refused in staging/prod); `live` = the providers and models in `app/knowledge/config/*.yaml` through `knowledge/gateway`. Model IDs, budgets and thresholds are versioned files, not settings (invariant 13) |
| `SOS_ANTHROPIC_API_KEY` | none | Anthropic organization API key for the LLM gateway (ADR-0005; never a personal subscription; `dev-only` values refused in staging/prod) |
| `SOS_EMBEDDINGS_API_KEY` | none | Embeddings provider API key, when the provider chosen by ADR-0006 needs one (`dev-only` values refused in staging/prod) |
| `AWS_REGION` (no prefix) | `ap-south-1` | AWS SDK region |
| `SOS_OIDC_ISSUER`, `SOS_OIDC_AUDIENCE` | local stub `/schoolos`, `schoolos-web` | Staff tokens (Cognito: audience = app client ID) |
| `SOS_PLATFORM_OIDC_ISSUER`, `SOS_PLATFORM_OIDC_AUDIENCE` | local stub `/platform`, `schoolos-platform` | Operator tokens |
| `SOS_OIDC_JWKS_URI`, `SOS_PLATFORM_OIDC_JWKS_URI` | none (discovery) | Explicit JWKS URLs; compose points them at `http://oidc:8080/...` |
| `SOS_SERVICE_TOKEN_KEY` | dev-only value | HS256 key for the BFF's `X-Service-Token` (same value in the web app) |
| `SOS_KEY_WRAPPER` | `local-dev` | `kms` or `local-dev` (local/CI only) |
| `SOS_LOCAL_DEV_MASTER_KEY` | none | Local-dev key wrapper and local audit-archive signing key |
| `SOS_KMS_DATA_KEY_ARN` | none | KMS key that wraps tenant DEKs and heartbeat keys (`SOS_KEY_WRAPPER=kms`) |
| `SOS_AUDIT_SIGNING_KEY_ARN` | none | Asymmetric KMS key (ECC_NIST_P256) signing daily audit archives |
| `SOS_AUDIT_ARCHIVE_RETENTION_DAYS` | 1096 | Object Lock retention for audit archives |
| `SOS_OTEL_EXPORTER_OTLP_ENDPOINT` | none | OTLP endpoint; no trace export when unset |
| `SOS_BILLING_SUPPLIER_LEGAL_NAME`, `SOS_BILLING_SUPPLIER_GSTIN`, `SOS_BILLING_SUPPLIER_STATE_CODE` | dev placeholders, `37` | Supplier block on GST invoices (placeholders refused in staging/prod) |
| `SOS_CONTROL_PLANE_URL` | none | Dedicated hosts: where the heartbeat is sent |
| `SOS_DEPLOYMENT_ID`, `SOS_DEDICATED_TENANT_ID` | none | Dedicated hosts: identity in the heartbeat |
| `SOS_HEARTBEAT_KEY_ID`, `SOS_HEARTBEAT_KEY` | none | Dedicated hosts: heartbeat HMAC key (base64url, shown once by the panel) |

Deployments use exactly these names: Terraform `shared_platform` (§5) and `deploy/dedicated/compose.yaml` (§15.2) give every app container (api, worker, beat, migrate) the full set it needs, and `apps/api/tests/deploy/test_env_contract.py` enforces it. Names that reach app containers without being settings are allowlisted there with the reader: `AWS_DEFAULT_REGION` (AWS SDK) and `SOS_HOST_STATE_DIR` (dedicated host state mount).

Test-only: `SOS_TEST_ADMIN_DATABASE_URL` (use an existing database instead of testcontainers), `SOS_WEB_TEST_REDIS_URL` (real-Valkey web test), `SOS_WEB_TEST_LOGS` (print the web app's JSON logs during vitest). Compose-only: `SOS_DB_ADMIN_PASSWORD`, `SOS_DB_APP_PASSWORD`, `SOS_DB_MIGRATOR_PASSWORD`, `SOS_DB_PLATFORM_PASSWORD`, `SOS_DB_READONLY_PASSWORD`, `SOS_INSTALL_PSQL`.

**Web (BFF) settings** (`apps/web/src/server/config.ts`; see `apps/web/README.md`): `APP_BASE_URL`, `SESSION_SECRET` (≥ 32 bytes), `SOS_SERVICE_TOKEN_KEY`, `REDIS_URL`, `API_INTERNAL_URL`, `OIDC_ISSUER`, `OIDC_CLIENT_ID`, `OIDC_CLIENT_SECRET`, `PLATFORM_OIDC_ISSUER`, `PLATFORM_OIDC_CLIENT_ID`, `PLATFORM_OIDC_CLIENT_SECRET`, optional `SOS_DEPLOYMENT_MODE`, `FILES_ORIGIN`.

`FILES_ORIGIN` is the origin of presigned upload and preview URLs, added to the CSP `connect-src` and `img-src` (docs/07 §10, §11; https only, except a loopback http origin under `next dev`). It must equal the origin the API presigns with, which the files bucket's CORS rule allows the app to `POST` to:

| Where | `FILES_ORIGIN` | Set by |
|---|---|---|
| Local (`make dev-host`) | `http://localhost:8333` (SeaweedFS, = `SOS_S3_PRESIGN_ENDPOINT_URL`) | `scripts/dev.py` |
| Shared tier (staging/prod) | `https://sos-<env>-files-<account>.s3.ap-south-1.amazonaws.com` | Terraform `shared_platform` web task (`module.s3.files_browser_origin`; also output `files_browser_origin`); CORS allows `https://<app_domain>` |
| Dedicated tier | `https://sos-ded-<school>-files-<account>.s3.ap-south-1.amazonaws.com` | `deploy/dedicated/compose.yaml` web service, from `SOS_S3_BUCKET_FILES` and `AWS_REGION` in host.env (Terraform output `files_browser_origin` shows the same value); CORS allows `https://<public_host>` and `https://<custom_domain>` |

In staging/prod `SOS_S3_ENDPOINT_URL` and `SOS_S3_PRESIGN_ENDPOINT_URL` stay unset: the API then signs virtual-hosted, regional URLs (`documents/storage.py`). A school's custom domain added after the host was created needs a Terraform apply of its `dedicated_host` stack so the files bucket's CORS rule includes it.

- Local identity: the dev OIDC stub serves both staff and operator logins; the stub's `http://localhost:8080` issuer is not reachable from inside the web container, so for browser sign-in run the web app on the host (`apps/web/README.md`). `SOS_KEY_WRAPPER=local-dev` replaces KMS locally only.
- Model calls in local/CI use the offline deterministic fake (`SOS_KB_PROVIDER_MODE` unset); `SOS_KB_PROVIDER_MODE=live` with an organization key calls real providers (M2, knowledge module).
- To try the dedicated tier locally, run `deploy/dedicated/compose.yaml` with `SOS_DEPLOYMENT_MODE=dedicated` against a separate project name.

## 12. Cost management

- AWS Budgets with alerts at 50/80/100% per account; anomaly detection on.
- Cost allocation tags on everything; monthly cost per active school tracked (NFR-CST-002).
- LLM/embedding spend metered per tenant and feature in-app; provider dashboards reconciled monthly.
- Right-size quarterly; prefer Graviton (arm64) images where dependencies allow.
- PDF capacity (ADR-0025 option A): one always-on `t4g.medium` (2 vCPU, 4 GiB) per shared environment plus a 30 GiB gp3 root volume, roughly USD 20 to 25 a month on demand in ap-south-1 (check current pricing); a second instance only during deployments or bursts (`pdf_worker.max_instances`). `pdf_worker.min_instances = 0` removes the idle cost at the price of a cold start of a few minutes for the first PDF.

## 13. Edge agent (M6: Tally connector)

- Small signed Windows service installed on the office PC running TallyPrime; reads via Tally's XML-over-HTTP interface on localhost (read-only export requests only).
- Outbound HTTPS only to SchoolOS; per-device credential bound to the tenant; revocable from admin console.
- Sends only configured data (e.g., outstanding fee ledgers), queues offline, retries; signed auto-updates; logs locally without personal data.

## 14. Production readiness checklist (per release of a new module)

- [ ] Runbook entries and alerts exist for new components
- [ ] Dashboards include new metrics; SLOs updated if needed
- [ ] Backup/retention settings cover new data
- [ ] Threat model and data classification updated
- [ ] Load test for new heavy paths
- [ ] Feature flag and rollback plan documented

## 15. Dedicated tier (ADR-0015)

A dedicated-tier school gets its own host running the same images as the shared tier. It is a one-tenant install: `tenant_id`, RLS, roles and tests are unchanged. The provisioning runbook outline is in 16 §13.

### 15.1 Terraform module `dedicated_host`

| Resource | Setting |
|---|---|
| EC2 instance | ap-south-1; Graviton where images allow; Ubuntu 24.04 LTS (Canonical AMI via SSM public parameter), hardened; IMDSv2 only; encrypted gp3 EBS with the host's KMS key; SSM agent; no SSH key pair |
| Network | Dedicated VPC subnet per region shared by dedicated hosts, one security group per host: inbound 80/443 only; egress to AWS endpoints, the control plane, LLM/embeddings/OCR providers and OS/image registries |
| KMS | One customer-managed key per host (EBS, buckets, Secrets Manager secrets, tenant key wrapping: `SOS_KMS_DATA_KEY_ARN`) plus the backup key in ap-south-2; deleting them crypto-shreds the host's data and backups. A separate asymmetric audit-signing key per host (`ECC_NIST_P256`, `SOS_AUDIT_SIGNING_KEY_ARN`; the instance role may `kms:Sign` and `kms:GetPublicKey`) signs the daily audit archives |
| S3 | Files bucket (ap-south-1) and backup bucket (ap-south-2), private, versioned, TLS-only, encrypted with the host key; lifecycle per 05 §13 |
| IAM | Instance role limited to its own buckets, key, Secrets Manager secrets (`schoolos/<tenant_code>/*`) and log group |
| Secrets | AWS Secrets Manager secrets encrypted with the host key: generated secrets (DB passwords, `SOS_SERVICE_TOKEN_KEY`, `SESSION_SECRET`, …) and an operator secret filled after apply (`SOS_ANTHROPIC_API_KEY`, `SOS_HEARTBEAT_KEY_ID`, `SOS_HEARTBEAT_KEY`); `deploy/dedicated/scripts/fetch-secrets.sh` renders them into a 0600 env file. Non-secret host settings (`SOS_CONTROL_PLANE_URL`, `SOS_DEPLOYMENT_ID`, `SOS_DEDICATED_TENANT_ID`, key ARNs) come from Terraform in `/etc/schoolos/host.env` |
| Logs | CloudWatch log group in ap-south-1, 400-day retention (CERT-In/DPDP) |
| DNS | Default host name under the SchoolOS domain; optional custom domain (school adds a CNAME) |

### 15.2 Runtime: `deploy/dedicated/compose.yaml`

Services: `caddy` (TLS termination, ACME certificates for the default and custom domains, HSTS, security headers), `web`, `api`, `worker`, `beat`, `postgres` (`pgvector/pgvector:0.8.6-pg16-bookworm`), `valkey` (`valkey/valkey:8.1-alpine`). Images are pulled **by digest**. Only `caddy` publishes ports (80/443); Postgres and Valkey listen on the internal container network only. Caddy's JSON access log (stdout, shipped with the host logs) keeps the request URI but its `format filter` replaces the values of the query parameters `code` and `state` (OIDC callback) and `query`, `q`, `admission_no`, `name`, `phone`, `email`, `dob` and `address` with `REDACTED`, and deletes `Authorization`, `Cookie` and `Set-Cookie`. This is defense in depth for old clients of the deprecated `GET /api/v1/students?query=`; the rule is still that personal data never goes in a URL (§4). `SOS_DEPLOYMENT_MODE=dedicated` removes control-plane routes and schedules. `beat` schedules the heartbeat and the worker sends it (16 §12). The `worker` alone runs under two host profiles for the Chromium sandbox (ADR-0025 option D): `security_opt: seccomp=/etc/schoolos/security/seccomp-worker.json` (§6) and `apparmor=schoolos-worker` (Docker's `docker-default` AppArmor rules plus `userns,`, because Ubuntu 24.04 sets `kernel.apparmor_restrict_unprivileged_userns=1`). Both ship in the bundle's `security/` directory; `scripts/lib.sh install_host_profiles` installs them and loads the AppArmor profile (`apparmor_parser --replace`) from `bootstrap-host.sh` before the stack starts and from `upgrade.sh` right after the release switch (a rollback reinstalls the previous release's copies). Every other service keeps `docker-default`. The `api`, `worker`, `beat` and one-off `migrate` services share one settings block (`x-app-env`, names as in §11), so each passes the production start-up guards.

After `db-bootstrap` and `migrate`, the school is created on the host with `python -m app.platform.provision_dedicated` (run in the api image via `scripts/compose.sh run --rm api ...`; see `deploy/dedicated/README.md`, Provisioning step 6). It refuses unless `SOS_DEPLOYMENT_MODE=dedicated` and `--tenant-id` equals `SOS_DEDICATED_TENANT_ID` (the tenant ID the control plane chose, 16 §5.4), and it refuses a second school on the same host. It registers the tenant with that ID, creates its keys and system roles, invites the owner and activates the school; it is resumable and audited.

### 15.3 Backups and restore

| What | How | Where | Retention |
|---|---|---|---|
| Continuous WAL archive | WAL-G, `archive_timeout` 5 min | Backup bucket, ap-south-2, host KMS key | 14 days |
| Base backup | WAL-G nightly (≈ 01:00 IST) | Same | 14 daily + 12 monthly |
| Logical dump | `pg_dump -Fc` nightly, encrypted | Same | 30 days |
| Files | S3 versioning on the files bucket; replication to ap-south-2 | ap-south-2 | Per lifecycle |

Targets: RPO ≤ 15 min, RTO ≤ 8 h (NFR-AVL-005). The RPO needs continuous WAL archiving: `deploy/dedicated/compose.walg.yaml` is opt-in and the host refuses to install WAL-G until `deploy/dedicated/walg/walg.lock` carries a reviewed version and checksum, so **enabling WAL-G is a go-live precondition for every dedicated school** (without it the nightly `pg_dump` gives RPO 24 h). A restore to a scratch host is tested **before go-live** and quarterly (R6). Backup age and WAL lag are reported in the heartbeat and alerted (11 §11).

### 15.4 Hardening and patching (SEC-030)

- CIS-aligned OS baseline; no SSH (SSM Session Manager with session logging); IMDSv2 only; host firewall allows 80/443 in.
- Automatic OS security updates; monthly maintenance reboot outside school hours; critical patches within 7 days, high within 30 days (NFR-SEC-005).
- Containers non-root, read-only root filesystems where possible, resource limits; Docker daemon with live-restore and log rotation. Only the worker leaves `docker-default`, for the Chromium sandbox profiles (§15.2); `auditd` watches `/etc/apparmor.d/`.
- Clock sync to Amazon Time Sync Service (CERT-In NTP requirement; 08 §6).
- Logs shipped to CloudWatch (no personal data; same allowlist and `redact()` as the shared tier).
- Account-level detection (SEC-023): hosts run in the prod account, so the prod `security_baseline` covers them: CloudTrail records their API calls and object-level access to their `sos-ded-*` buckets, GuardDuty watches EC2 (with EBS malware scans) and the buckets in both regions, and Config/Security Hub check IMDSv2, EBS encryption and open SSH (§5.1). A host placed in any other AWS account needs its own `security_baseline` instance first.
- Operator access only through the pipeline or SSM, under the same break-glass rules as the shared tier; the control plane has no inbound path.

### 15.5 Fleet upgrades

- The `deploy-dedicated` GitHub Actions workflow runs after a shared-tier production release: it reads the fleet list, then upgrades hosts in waves (canary host first, then the rest) via SSM Run Command: pull new digests → run `migrate` (backward-compatible migrations only) → system-role sync → `docker compose up -d` → smoke test → confirm the next heartbeat reports the new version.
- System-role sync (§8 item 7, ADR-0022, ADR-0026): after `migrate`, `upgrade.sh` runs `scripts/sync-system-roles.sh --apply` (never `--prune`), so the school's system roles follow the release's roles.yaml. Exit `1` (refused), `4` (the school failed, or a custom role holds a system role key) or any other non-zero code fails the upgrade loudly and rolls the host back; grants already added stay (additive, audited). Removing grants roles.yaml no longer lists stays manual: `--prune` dry run, then `--apply --prune`.
- A failed wave stops the rollout and rolls the host back to the previous digests. Upgrades run outside school hours (after 18:00 IST or Sundays) with 48-hour notice via announcements.
- Target: every host no more than one release behind 14 days after a release (NFR-FLT-002).

### 15.6 Custom domains and TLS

The school points `office.<school>.edu.in` (example) at its host with a CNAME. Caddy obtains and renews certificates automatically (ACME). Certificate expiry is reported in the heartbeat and alerted at 14 days. The OIDC app client for that deployment lists the domain's callback URL (ADR-0018; 16 §19, Q4).
