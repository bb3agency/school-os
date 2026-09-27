# SchoolOS dedicated tier: single-host install

One isolated EC2 host per school (premium plan, ADR-0015, docs/10 §15). It runs the same images as the
shared tier. It is a one-tenant install: `tenant_id`, RLS and every test stay on, and
`SOS_DEPLOYMENT_MODE=dedicated` switches off the control-plane routes. The host only talks outbound to the
control plane (HMAC-signed heartbeat, sent by `beat`). The control plane never pulls data from a school.

| Piece | Where |
|---|---|
| Infrastructure | `infra/terraform/modules/dedicated_host`, instantiated per school by `infra/terraform/envs/dedicated-template` |
| Runtime | this directory, shipped as a versioned bundle `s3://<artifacts>/dedicated/<version>/schoolos-dedicated.tar.gz` (+ `.sha256`) built by `scripts/package.sh` |
| On the host | release in `/opt/schoolos/releases/<version>`, active release linked at `/opt/schoolos/deploy/dedicated` (`SOS_INSTALL_DIR`), data on the encrypted EBS volume at `/var/lib/schoolos` |

```
internet ──80/443──▶ caddy ──edge──▶ web (BFF) ──app──▶ api ─┐
                                        │                    ├─data (internal, no egress)─▶ db (PostgreSQL 16 + pgvector)
                                        └────────────────────┤                            ▶ valkey 8.1
                              worker, beat ──app (egress: S3, KMS, Cognito, LLM, heartbeat)┘
```

Only `caddy` publishes ports. `/api/v1/fleet/*` and `/metrics` answer 404 at the edge. The API is reachable
only on the internal network, where the BFF calls it with the signed service token.

## Security baseline (SEC-030)

- Ubuntu 24.04 LTS, IMDSv2 only, no SSH daemon and no key pair: access is **SSM Session Manager** only.
  The security group allows only 80/443 in (plus UDP 443 for HTTP/3).
- Root and data EBS volumes, the files bucket, the audit archive, secrets and logs use the school's own CMK.
  Backups go to ap-south-2 under a separate CMK in that region.
- The instance role can reach only this school's buckets, keys and secrets, plus ECR pull and the release bundles.
- Containers: `read_only` root filesystem, `tmpfs` scratch space, `no-new-privileges`, `cap_drop: ALL`
  (caddy adds only `NET_BIND_SERVICE`), non-root UIDs (app 10001, postgres/valkey 999), memory/CPU limits and
  health checks. The Valkey password lives in a tmpfs config file, never on a command line.
- Chromium renders PDFs with its sandbox on (ADR-0025 option D). Only the `worker` container leaves
  Docker's defaults, for two profiles shipped in `security/`: `seccomp-worker.json` (docker-default plus
  `chroot`, `clone` and `unshare`, installed at `/etc/schoolos/security/`) and the AppArmor profile
  `schoolos-worker` (docker-default plus `userns,`, since Ubuntu 24.04 restricts unprivileged user
  namespaces). `bootstrap-host.sh` and `upgrade.sh` install and load them (`lib.sh install_host_profiles`)
  before the worker starts; a rollback reinstalls the previous release's copies. Check on a host:
  `sudo aa-status | grep schoolos-worker` and `docker inspect schoolos-worker-1 --format '{{.HostConfig.SecurityOpt}}'`.
- Docker daemon: `live-restore`, `no-new-privileges`, `icc=false`. Logs go to CloudWatch
  (`/schoolos/dedicated/<school_code>`, 400 days) with a bounded local cache.
- Security patches: `unattended-upgrades` runs daily at 00:45 IST and reboots at 02:30 IST if needed.
  A maintenance reboot runs on the first Sunday of each month at 03:30 IST. `auditd` rules watch config and
  identity files, and the clock syncs to Amazon Time Sync. fail2ban is not installed because nothing
  listens for SSH.

## Inputs you must supply

| Input | Where |
|---|---|
| AWS account (normally prod) and region guard | `aws_account_id` in `schools/<code>.tfvars` |
| `school_code`, `deployment_id`, `tenant_id` (from the platform panel: Provision school → Dedicated) | tfvars |
| Platform host name (`domain`), optional school `custom_domain`, `acme_email` | tfvars |
| Release `release_version` + `bundle_sha256` (from CI release notes) | tfvars |
| Shared prod outputs: `artifacts_bucket`, `artifacts_kms_key_arn`, `control_plane_url` | tfvars |
| Anthropic API key (ZDR organisation) and the heartbeat key ID + key (shown once by the panel) | Secrets Manager, after apply |

Terraform writes the non-secret host settings to `/etc/schoolos/host.env` under the names the app reads
(`apps/api/app/core/config.py`; list in `.env.template`): `SOS_KMS_DATA_KEY_ARN` (the school's CMK, wraps
the tenant keys), `SOS_AUDIT_SIGNING_KEY_ARN` (the host's asymmetric key that signs the daily audit
archives), `SOS_CONTROL_PLANE_URL`, `SOS_DEPLOYMENT_ID` and `SOS_DEDICATED_TENANT_ID` (heartbeat identity).
The api, worker, beat and migrate containers all get the same settings, so each passes the production
start-up checks. `apps/api/tests/deploy/test_env_contract.py` fails CI if a name drifts.

## Provisioning

1. **Panel:** Provision school → Dedicated (step-up). Note the tenant ID, the deployment ID and the one-time
   heartbeat key ID and key.
2. **Terraform** (from `infra/terraform/envs/dedicated-template`):
   ```bash
   cp backend.hcl.example schools/<code>.backend.hcl   # set key = schoolos/dedicated/<code>/terraform.tfstate
   cp terraform.tfvars.example schools/<code>.tfvars   # fill in
   terraform init -reconfigure -backend-config=schools/<code>.backend.hcl
   terraform apply -var-file=schools/<code>.tfvars
   ```
3. **Operator secrets** (the host waits up to 2 h for them):
   ```bash
   aws secretsmanager put-secret-value --secret-id "$(terraform output -raw operator_secret_arn)" \
     --secret-string file://operator.json
   # operator.json: {"SOS_ANTHROPIC_API_KEY":"...","SOS_HEARTBEAT_KEY_ID":"hb-...","SOS_HEARTBEAT_KEY":"..."}
   shred -u operator.json
   ```
4. **DNS:** `terraform output dns_instructions`. Create the platform A record, or let Route 53 do it.
   The school adds an A record for its custom domain (a CNAME to the platform name also works).
   Caddy then gets certificates automatically (ACME HTTP-01/TLS-ALPN).
5. **Bootstrap:** cloud-init installs Docker, hardening, the AWS CLI and the ECR credential helper, then fetches
   and verifies the bundle and runs `scripts/bootstrap-host.sh`. That script mounts the data volume, installs
   the systemd units, fetches secrets, runs `db-bootstrap` (`infra/db/bootstrap.sql`) and `migrate`, and starts
   `schoolos.service`. Follow it with
   `aws ssm start-session --target <instance-id>` → `sudo journalctl -t schoolos-bootstrap -f`.
6. **School and owner:** create the owner's account in this host's user pool first (its `sub` is the
   `--owner-subject`), then, on the host:
   ```bash
   cd /opt/schoolos/deploy/dedicated
   sudo scripts/compose.sh run --rm api python -m app.platform.provision_dedicated \
     --tenant-id <tenant ID from the panel> --code <school code> --name "<school name>" \
     [--boards CISCE] --owner-subject <sub> --owner-name "<display name>" \
     --owner-email <email> [--owner-language en|te]
   ```
   It refuses unless the host runs `SOS_DEPLOYMENT_MODE=dedicated` and `--tenant-id` equals
   `SOS_DEDICATED_TENANT_ID`, and it refuses a second school on the same host. It creates the school with
   that ID, its keys (KMS) and system roles, the invited owner (MFA required, `owner` role), and makes the
   school `active`; every step is audited (platform chain `actor_type = system` and the school's own chain).
   It prints IDs and states only, and a re-run resumes an interrupted run or reports `already_active`.
   The owner then signs in, enrols MFA and accepts the invite.
7. **Verify:** the first heartbeat turns the deployment `healthy`; smoke tests pass; run a restore drill
   (`sudo scripts/restore.sh --latest`) **before go-live**.

Day-to-day commands (on the host, as root):

```bash
cd /opt/schoolos/deploy/dedicated
scripts/compose.sh ps                 # status
scripts/compose.sh logs --tail 200 api
systemctl restart schoolos            # re-reads secrets from Secrets Manager
```

Never run plain `docker compose`: `scripts/compose.sh` renders `/etc/schoolos/compose.env` (0600) from
`host.env` (Terraform), the release's `release.env` (image digests) and `secrets.env` (Secrets Manager).

## Upgrade

```bash
sudo /opt/schoolos/deploy/dedicated/scripts/upgrade.sh 2026.10.2 [--sha256 <sha>]
```

The fleet workflow runs this through SSM Run Command, targeting the tag `schoolos:tier=dedicated`
(canary first, via `schoolos:deployment-id`). Output goes to `/schoolos/dedicated/deploy`. The script:

1. fetches and verifies the new bundle (compose, Caddyfile, scripts, image digests);
2. pre-pulls images while the old release serves traffic;
3. takes a pre-upgrade backup (`daily/…-pre-upgrade-<version>.dump`) and aborts if the backup fails;
4. switches the release link and runs `db-bootstrap` and `migrate`. Migrations are expand-only, so older code
   still works;
5. brings the school's system roles in line with `app/authz/roles.yaml` (`scripts/sync-system-roles.sh --apply`,
   ADR-0022; never `--prune`). A refusal (exit 1), a failed school or a conflict with a custom role (exit 4)
   fails the upgrade;
6. restarts services in order (worker, beat, api, web, caddy), waiting for each health check;
7. checks health end to end through Caddy (`https://<host>/healthz`).

If any step fails, it relinks the previous release, restarts it and exits non-zero. It never rolls a
migration back (they are backward compatible) or a role grant the sync already added (additive, audited,
permissions the migrations put in the catalog), and it keeps the last three releases. Upgrades run outside
school hours (after 18:00 IST or on Sundays), with 48 h notice.

If a release changes `systemd/`, run `scripts/bootstrap-host.sh` afterwards. It is idempotent.

The upgrade adds missing system roles and grants itself (step 5). Grants roles.yaml no longer lists are kept
and reported; remove them only when the release notes ask for it: `sudo scripts/sync-system-roles.sh --prune`
(dry run first), then `--apply --prune`. After an exit-4 failure, fix the conflict (a custom role using a
system role key) and re-run the upgrade or `sudo scripts/sync-system-roles.sh --apply`. The command is
idempotent and audited in the school's own chain; exit code 3 means a dry run found changes (docs/10 §8).

## Backup and restore

| What | How | Where | Retention |
|---|---|---|---|
| Logical dump | `scripts/backup.sh` via `schoolos-backup.timer` (01:30 IST): `pg_dump -Fc` as superuser (RLS does not filter), verified with `pg_restore --list`, SHA-256 | `s3://<backup>/daily/YYYY/MM/DD/` (ap-south-2), `--sse aws:kms` with the school's backup CMK | 35 days (lifecycle) |
| Monthly copy | first dump of each IST month | `monthly/YYYY/MM/` | ~13 months |
| WAL-G (optional) | `archive_command` → `wal-g wal-push`, `archive_timeout` 5 min; nightly `backup-push`, `delete retain FULL 14` | `wal-g/` | 14 full backups |
| EBS snapshots | Data Lifecycle Manager, daily | ap-south-1 | 7 |
| Files | S3 versioning (90 days noncurrent) | files bucket | lifecycle |

The backup bucket has Object Lock (GOVERNANCE, 30 days), so a compromised host cannot erase recent backups.
Each run writes `/var/lib/schoolos/state/backup.json`, which `beat` reads for the heartbeat, and publishes the
CloudWatch metrics `SchoolOS/Dedicated BackupSuccess` and `BackupSizeBytes`.

**Enable WAL-G:** fill in `WALG_VERSION` and both SHA-256 values in `walg/walg.lock` (reviewed PR), set
`walg_enabled = true` in tfvars, apply, then set `SOS_WALG_ENABLED=true` in `/etc/schoolos/host.env` on
existing hosts (cloud-init renders it only at first boot) and re-run `scripts/bootstrap-host.sh`.
`compose.walg.yaml` then gives the db container the wal-g binary and S3 egress.

**Restore drill** (quarterly and before go-live, runbook R6):

```bash
sudo scripts/restore.sh --latest          # or --from s3://<backup>/daily/.../x.dump
```

The drill restores into a scratch database, counts tables and rows, and runs `python -m app.audit.verify_all`
on the copy. It uploads a timing report to `restore-drills/`, then drops the scratch database (`--keep` keeps it).

**Disaster restore** (approved incident only):
`sudo scripts/restore.sh --production --from s3://… --yes-i-understand`. It takes a safety dump, stops the app,
keeps the old database as `schoolos_replaced_<ts>`, restores, re-runs bootstrap and migrations, and restarts.
If the host itself is lost, re-apply Terraform (a new host with an empty data volume), then run the
disaster restore with the latest dump.

### RPO / RTO (dedicated tier)

| Mode | RPO | RTO |
|---|---|---|
| Nightly `pg_dump` only (default) | ≤ 24 h | ≤ 4 h (new host ≈ 20 min, restore, DNS already points at the Elastic IP) |
| WAL-G enabled | ≤ 15 min (`archive_timeout` 5 min) | ≤ 4 h |

NFR-AVL-002 (RPO ≤ 15 min) is met only with WAL-G. docs/10 §15.3 lists WAL-G as the target configuration.

## Secret rotation

| Secret | How |
|---|---|
| Generated credentials (DB roles, Valkey, service token key, session secret) | Bump `generated_secret_version` in Terraform and apply (a new value is written to Secrets Manager, never to state). On the host: `systemctl stop schoolos`, `scripts/fetch-secrets.sh`, `scripts/compose.sh up -d --wait db valkey`, `scripts/compose.sh run --rm db-bootstrap` (sets the role passwords), then `systemctl start schoolos`. Changing `POSTGRES_PASSWORD` also needs `ALTER ROLE postgres PASSWORD …` inside the db container first. Users must sign in again after a `SESSION_SECRET` change. |
| Anthropic API key / heartbeat key | `put-secret-value` on the operator secret (for the heartbeat, both `SOS_HEARTBEAT_KEY_ID` and `SOS_HEARTBEAT_KEY`), then `systemctl restart schoolos`. For the heartbeat key, follow the panel's 7-day overlap. |
| OIDC client secret | Recreate the Cognito app client (taint it), apply, restart. |
| TLS certificates | Automatic (Caddy/ACME). Expiry is reported in the heartbeat. |
| KMS | Data and backup keys: annual automatic rotation. The audit signing key is asymmetric (ECC_NIST_P256), which AWS KMS cannot rotate: to replace it, create a new key, point `SOS_AUDIT_SIGNING_KEY_ARN` at it, and keep the old key (or its exported public key) to verify older archives. |

## Decommission (crypto-shredding)

After offboarding approval (docs/16 §13.4):

1. Deliver the final tenant export. Stop the stack: `systemctl disable --now schoolos`.
2. Set `termination_protection = false`, apply, then `terraform destroy`. Buckets that still hold objects will fail
   to delete; that is expected.
3. **Crypto-shred:** schedule deletion of both school keys (the data CMK in ap-south-1 and the backup CMK in
   ap-south-2) with a 30-day window:
   `aws kms schedule-key-deletion --key-id <arn> --pending-window-in-days 30`. Once the keys are deleted,
   the EBS snapshots, backups, files, audit archive and secrets encrypted with them can never be read again.
   The audit signing key (`terraform output kms_key_arns`, `audit_signing`) encrypts nothing: export its
   public key (`aws kms get-public-key`) with the certificate of deletion, then schedule its deletion too.
4. Empty and remove the buckets after their retention windows (backup Object Lock 30 days; the audit archive
   stays under COMPLIANCE for 3 years and is unreadable after step 3). Mark the deployment `decommissioned`
   and issue the certificate of deletion.

## Files

| File | Purpose |
|---|---|
| `compose.yaml` | services: caddy, web, api, worker, beat, db, valkey; one-off `migrate`, `db-bootstrap` (profile `tools`) |
| `compose.walg.yaml` | WAL-G overlay for db |
| `Caddyfile` | TLS, security headers, edge path blocks, log redaction of OIDC `code`/`state` |
| `.env.template` | every variable the stack reads (no secrets) |
| `scripts/` | `bootstrap-host.sh`, `fetch-secrets.sh`, `compose.sh`, `upgrade.sh`, `sync-system-roles.sh`, `backup.sh`, `restore.sh`, `package.sh` (CI), `lib.sh` |
| `systemd/` | `schoolos.service`, `schoolos-backup.{service,timer}`, `schoolos-monthly-reboot.{service,timer}`, unattended-upgrades schedule drop-in |
| `walg/` | archive wrapper and pinned WAL-G version |
