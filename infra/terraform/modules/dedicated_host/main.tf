# Dedicated tier (premium plan): one isolated EC2 host per school in ap-south-1 running the SchoolOS
# containers from deploy/dedicated/compose.yaml. It is a one-tenant install: tenant_id + RLS stay on and
# SOS_DEPLOYMENT_MODE=dedicated disables control-plane routes.
#
# Controls: IMDSv2 only, no SSH/key pair (SSM Session Manager only), SG inbound 80/443 only, encrypted
# gp3 root + separate encrypted data volume (per-school CMK), per-school files bucket, backups to
# ap-south-2, instance role limited to this school's buckets/keys/secrets, no secrets in outputs or state.

data "aws_caller_identity" "current" {}
data "aws_partition" "current" {}
data "aws_region" "current" {}

data "aws_subnet" "this" {
  id = var.subnet_id
}

locals {
  name              = "sos-ded-${var.school_code}"
  partition         = data.aws_partition.current.partition
  account           = data.aws_caller_identity.current.account_id
  arch              = can(regex("^[a-z]+[0-9]+[a-z]*g[a-z]*\\.", var.instance_type)) ? "arm64" : "amd64"
  bundle_key_prefix = regex("^s3://[^/]+/(.+)$", var.bundle_s3_prefix)[0]
  generated_keys = toset([
    "POSTGRES_PASSWORD",
    "SOS_APP_DB_PASSWORD",
    "SOS_MIGRATOR_DB_PASSWORD",
    "SOS_PLATFORM_DB_PASSWORD",
    "SOS_READONLY_DB_PASSWORD",
    "VALKEY_PASSWORD",
    "SOS_SERVICE_TOKEN_KEY",
    "SESSION_SECRET",
  ])
  log_group = "/schoolos/dedicated/${var.school_code}"
  tags = merge(var.tags, {
    school_code              = var.school_code
    deployment_mode          = "dedicated"
    "schoolos:tier"          = "dedicated"
    "schoolos:deployment-id" = var.deployment_id
  })
  # Origins the school's browsers use for the app (Caddy serves both names).
  app_origins = compact(["https://${lower(var.public_host)}", var.custom_domain == "" ? "" : "https://${lower(var.custom_domain)}"])
}

# Ubuntu 24.04 LTS (unattended-upgrades, SSM agent preinstalled). The AMI is resolved at create time;
# later AMI releases do not replace the host (ignore_changes) - the OS is patched in place.
data "aws_ssm_parameter" "ubuntu" {
  name = "/aws/service/canonical/ubuntu/server/24.04/stable/current/${local.arch}/hvm/ebs-gp3/ami-id"
}

# --- Audit signing key ------------------------------------------------------------------

# Signs this school's daily audit archives (KmsSigner, ECDSA_SHA_256; FR-AUD-004). Asymmetric keys are
# not rotated by AWS (see modules/kms). It encrypts nothing, so deleting it shreds no data; on
# decommission export its public key first so archived signatures stay verifiable (README).
module "audit_signing_key" {
  source = "../kms"

  name_prefix = local.name
  keys = {
    audit-signing = {
      description = "SchoolOS dedicated ${var.school_code}: audit archive signatures (ECC_NIST_P256, SIGN_VERIFY)"
      key_spec    = "ECC_NIST_P256"
      key_usage   = "SIGN_VERIFY"
    }
  }
  tags = local.tags
}

# --- Files bucket ---------------------------------------------------------------------

module "files" {
  source = "../s3_bucket"

  name        = "${local.name}-files-${local.account}"
  kms_key_arn = var.kms_key_arn
  # Browser uploads (presigned POST, SEC-016, docs/07 §10) from the school's app origins only: the
  # platform hostname and, when set, the school's custom domain. POST only: previews use <img> and
  # downloads are navigations (no CORS needed); see modules/s3 for the header reasoning.
  cors_rules = [
    {
      allowed_origins = local.app_origins
      allowed_methods = ["POST"]
      allowed_headers = ["content-type"]
      max_age_seconds = 3600
    },
  ]
  lifecycle_rules = [
    # docs/05 §13: exports 7 days (tag set on upload by app/documents/storage.py); noncurrent after 1 day.
    { id = "exports-7d", tags = { "sos-lifecycle" = "export-7d" }, expiration_days = 7, noncurrent_version_expiration_days = 1 },
    # FR-ADM-001: the full data export (link valid 24 h, purge job); noncurrent copy after 1 day.
    { id = "tenant-export-2d", tags = { "sos-lifecycle" = "tenant-export-2d" }, expiration_days = 2, noncurrent_version_expiration_days = 1 },
    { id = "import-raw-90d", tags = { "sos-lifecycle" = "import-raw-90d" }, expiration_days = 90 },
    # PRV-016 images and every automatic deletion (retention, offboarding; docs/08 §7), tagged
    # by the app before it deletes them.
    { id = "discarded-1d", tags = { "sos-lifecycle" = "discarded" }, expiration_days = 1, noncurrent_version_expiration_days = 1 },
    { id = "noncurrent-and-multipart", noncurrent_version_expiration_days = 90, abort_incomplete_multipart_days = 7 },
  ]
  tags = local.tags
}

# Daily signed audit export (07 §4 T4) for this school; Object Lock COMPLIANCE like the shared tier.
module "audit_archive" {
  source = "../s3_bucket"

  name        = "${local.name}-audit-${local.account}"
  kms_key_arn = var.kms_key_arn
  object_lock = { mode = "COMPLIANCE", years = var.audit_object_lock_years }
  lifecycle_rules = [
    { id = "multipart", abort_incomplete_multipart_days = 7 },
  ]
  tags = merge(local.tags, { data_class = "C2-audit" })
}

# --- Logs ----------------------------------------------------------------------------------

# Container logs (Docker awslogs driver; no personal data by the app's logging contract) and host scripts.
resource "aws_cloudwatch_log_group" "host" {
  name              = local.log_group
  retention_in_days = var.log_retention_days
  kms_key_id        = var.kms_key_arn
  tags              = local.tags
}

# --- Security alarms (audit 2026-10-05 hardening: dedicated hosts had none) ------------------------
#
# The same filters as modules/observability (P2-02, P2-07), on the host log group that every container
# writes to. Events are unique per writer, so one group is enough. The control-plane-only events
# (heartbeat rejections, denied operators) are not here. The namespace is not SchoolOS/Dedicated, the
# only one the host role may write, so a compromised host cannot feed or silence these metrics.
# apps/api/tests/deploy/test_dedicated_security_alarms.py pins the events to the observability ones.

locals {
  security_ns = "SchoolOS/Security/dedicated/${var.school_code}"

  security_filters = {
    audit_chain_broken = {
      pattern = "{ $.event = \"audit.chain.broken\" }"
      metric  = "AuditChainVerificationFailures"
    }
    audit_chain_checked = {
      pattern = "{ ($.event = \"audit.chain.verified\") || ($.event = \"audit.chain.broken\") }"
      metric  = "AuditChainsChecked"
    }
    api_auth_failures = {
      pattern = "{ ($.event = \"http.request\") && ($.status = 401) }"
      metric  = "ApiAuthFailures"
    }
    breakglass_session = {
      pattern = "{ ($.event = \"http.request\") && ($.route = \"POST /api/v1/breakglass/support-session\") && ($.status = 200) }"
      metric  = "BreakGlassSessionsStarted"
    }
    refresh_token_reuse = {
      pattern = "{ $.event = \"refresh_token_reuse_detected\" }"
      metric  = "RefreshTokenReuse"
    }
    api_rate_limited = {
      pattern = "{ $.event = \"security.rate_limited\" }"
      metric  = "ApiRateLimited"
    }
    api_auth_failed = {
      pattern = "{ $.event = \"security.auth.failed\" }"
      metric  = "ApiAuthFailed"
    }
    rate_limiter_unavailable = {
      pattern = "{ $.event = \"security.rate_limit.unavailable\" }"
      metric  = "RateLimiterUnavailable"
    }
    sign_in_failed = {
      pattern = "{ ($.event = \"signin_failed\") || ($.event = \"step_up_failed\") }"
      metric  = "SignInFailures"
    }
    bff_auth_rate_limited = {
      pattern = "{ $.event = \"auth_rate_limited\" }"
      metric  = "BffAuthRateLimited"
    }
  }

  # One school per host: lower volumes than the shared tier, same thresholds as its defaults.
  security_alarms = {
    api_auth_failures        = { description = "SECURITY: many 401 answers from the API (token replay or a stolen service token). docs/07 §15.", threshold = 50 }
    breakglass_session       = { description = "SECURITY (notice): a SchoolOS support break-glass session started (ADR-0023). Check it matches an approved request.", threshold = 1 }
    refresh_token_reuse      = { description = "SECURITY: a spent refresh token was presented again; the session family was revoked (FR-IAM-004).", threshold = 1 }
    api_rate_limited         = { description = "SECURITY: many API requests refused by rate limits (P2-07).", threshold = 200 }
    api_auth_failed          = { description = "SECURITY: many rejected tokens or refused sign-ins at the API (ASVS 2.2.1).", threshold = 30 }
    rate_limiter_unavailable = { description = "SECURITY: the API rate limiter cannot reach Valkey.", threshold = 1 }
    sign_in_failed           = { description = "SECURITY: a spike of refused sign-in or step-up callbacks in the BFF (P2-07).", threshold = 30 }
    bff_auth_rate_limited    = { description = "SECURITY: the BFF is refusing sign-ins for its per-IP limit or backoff (P2-07).", threshold = 200 }
  }
}

resource "aws_cloudwatch_log_metric_filter" "security" {
  for_each = local.security_filters

  name           = "${local.name}-${replace(each.key, "_", "-")}"
  log_group_name = aws_cloudwatch_log_group.host.name
  pattern        = each.value.pattern

  metric_transformation {
    name          = each.value.metric
    namespace     = local.security_ns
    value         = "1"
    default_value = "0"
    unit          = "Count"
  }
}

resource "aws_cloudwatch_metric_alarm" "security" {
  for_each = local.security_alarms

  alarm_name          = "${local.name}-${replace(each.key, "_", "-")}"
  alarm_description   = "${var.school_code}: ${each.value.description}"
  namespace           = local.security_ns
  metric_name         = local.security_filters[each.key].metric
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = each.value.threshold
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [var.security_alarm_topic_arn]
  tags                = local.tags

  depends_on = [aws_cloudwatch_log_metric_filter.security]
}

resource "aws_cloudwatch_metric_alarm" "audit_chain_failure" {
  alarm_name          = "${local.name}-audit-chain-verification-failed"
  alarm_description   = "P1 SECURITY (${var.school_code}): audit hash-chain verification failed (SEC-007). Runbook R5."
  namespace           = local.security_ns
  metric_name         = local.security_filters.audit_chain_broken.metric
  statistic           = "Sum"
  period              = 3600
  evaluation_periods  = 1
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [var.security_alarm_topic_arn]
  ok_actions          = [var.security_alarm_topic_arn]
  tags                = local.tags

  depends_on = [aws_cloudwatch_log_metric_filter.security]
}

resource "aws_cloudwatch_metric_alarm" "audit_chain_not_run" {
  alarm_name          = "${local.name}-audit-chain-verification-missing"
  alarm_description   = "${var.school_code}: daily audit chain verification has not checked any chain in the last day (SLO: 100% daily)."
  namespace           = local.security_ns
  metric_name         = local.security_filters.audit_chain_checked.metric
  statistic           = "Sum"
  period              = 86400
  evaluation_periods  = 1
  threshold           = 1
  comparison_operator = "LessThanThreshold"
  treat_missing_data  = "breaching"
  alarm_actions       = [var.security_alarm_topic_arn]
  ok_actions          = [var.security_alarm_topic_arn]
  tags                = local.tags

  depends_on = [aws_cloudwatch_log_metric_filter.security]
}

# --- Secrets (values never in state) ----------------------------------------------------

ephemeral "random_password" "generated" {
  for_each = local.generated_keys

  length  = 48
  special = false
}

resource "aws_secretsmanager_secret" "generated" {
  name        = "sos/dedicated/${var.school_code}/generated"
  description = "Generated credentials for the ${var.school_code} dedicated host (JSON env map)"
  kms_key_id  = var.kms_key_arn
  tags        = local.tags
}

resource "aws_secretsmanager_secret_version" "generated" {
  secret_id                = aws_secretsmanager_secret.generated.id
  secret_string_wo         = jsonencode({ for k in local.generated_keys : k => ephemeral.random_password.generated[k].result })
  secret_string_wo_version = var.generated_secret_version
}

resource "aws_secretsmanager_secret" "operator" {
  name        = "sos/dedicated/${var.school_code}/operator"
  description = "Operator-supplied values for ${var.school_code} (API keys, heartbeat key ID and key). Set with put-secret-value."
  kms_key_id  = var.kms_key_arn
  tags        = merge(local.tags, { value_source = "operator" })
}

resource "aws_secretsmanager_secret_version" "operator" {
  secret_id                = aws_secretsmanager_secret.operator.id
  secret_string_wo         = jsonencode({ for k in var.operator_secret_keys : k => "__SET_ME__" })
  secret_string_wo_version = 1
}

# --- IAM ------------------------------------------------------------------------------

data "aws_iam_policy_document" "assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ec2.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "host" {
  name               = "${local.name}-host"
  assume_role_policy = data.aws_iam_policy_document.assume.json
  tags               = local.tags
}

resource "aws_iam_role_policy_attachment" "ssm" {
  role       = aws_iam_role.host.name
  policy_arn = "arn:${local.partition}:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

# Audit W3-06: the host role is used by the host's own scripts (secrets, release bundles, backups,
# WAL-G, logs) and never reaches the school's files. Each app container gets its own role through
# short-lived credentials that scripts/app-credentials.sh writes for it (credential_process files,
# refreshed every 10 minutes by schoolos-app-credentials.timer). IMDS hop limit 1 (below) keeps every
# container away from the instance role. The internet-facing api role cannot tag or delete files; only
# the worker role discards (PRV-016, retention, the api's queued leftovers).
data "aws_iam_policy_document" "host" {
  statement {
    sid       = "AssumeAppRoles"
    actions   = ["sts:AssumeRole"]
    resources = [aws_iam_role.app["api"].arn, aws_iam_role.app["worker"].arn]
  }

  # Backups: write + read for restore drills. No delete except WAL-G's own retention under wal-g/
  # (lifecycle expires dumps; Object Lock protects every version for its retention window).
  statement {
    sid       = "BackupBucket"
    actions   = ["s3:ListBucket", "s3:GetObject", "s3:PutObject", "s3:AbortMultipartUpload"]
    resources = [var.backup_bucket_arn, "${var.backup_bucket_arn}/*"]
  }

  statement {
    sid       = "WalgRetention"
    actions   = ["s3:DeleteObject"]
    resources = ["${var.backup_bucket_arn}/wal-g/*"]
  }

  statement {
    sid       = "HostLogs"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents", "logs:DescribeLogStreams"]
    resources = ["${aws_cloudwatch_log_group.host.arn}:*", "arn:${local.partition}:logs:${data.aws_region.current.region}:${local.account}:log-group:${var.fleet_deploy_log_group}:*"]
  }

  statement {
    sid       = "ReleaseBundles"
    actions   = ["s3:GetObject"]
    resources = ["${var.artifacts_bucket_arn}/${local.bundle_key_prefix}/*"]
  }

  statement {
    sid       = "SchoolKeys"
    actions   = ["kms:Encrypt", "kms:Decrypt", "kms:ReEncrypt*", "kms:GenerateDataKey*", "kms:DescribeKey"]
    resources = [var.kms_key_arn, var.backup_kms_key_arn]
  }

  statement {
    sid       = "ArtifactsDecrypt"
    actions   = ["kms:Decrypt"]
    resources = [var.artifacts_kms_key_arn]
  }

  statement {
    sid       = "OwnSecrets"
    actions   = ["secretsmanager:GetSecretValue", "secretsmanager:DescribeSecret"]
    resources = compact([aws_secretsmanager_secret.generated.arn, aws_secretsmanager_secret.operator.arn, var.oidc_client_secret_arn, var.support_oidc_client_secret_arn])
  }

  statement {
    sid       = "EcrAuth"
    actions   = ["ecr:GetAuthorizationToken"]
    resources = ["*"]
  }

  statement {
    sid       = "EcrPull"
    actions   = ["ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer", "ecr:BatchCheckLayerAvailability"]
    resources = var.ecr_repository_arns
  }

  statement {
    sid       = "BackupMetrics"
    actions   = ["cloudwatch:PutMetricData"]
    resources = ["*"]
    condition {
      test     = "StringEquals"
      variable = "cloudwatch:namespace"
      values   = ["SchoolOS/Dedicated"]
    }
  }
}

resource "aws_iam_role_policy" "host" {
  name   = "schoolos-host"
  role   = aws_iam_role.host.id
  policy = data.aws_iam_policy_document.host.json
}

# --- App container roles (audit W3-06) -------------------------------------------------------

# Only the host role (root on the host, through scripts/app-credentials.sh) may assume them.
data "aws_iam_policy_document" "app_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "AWS"
      identifiers = [aws_iam_role.host.arn]
    }
  }
}

resource "aws_iam_role" "app" {
  for_each = toset(["api", "worker"])

  name                 = "${local.name}-${each.key}"
  assume_role_policy   = data.aws_iam_policy_document.app_assume.json
  max_session_duration = 3600
  tags                 = local.tags
}

# api (internet-facing): read and write school files, never tag or delete them.
data "aws_iam_policy_document" "api" {
  statement {
    sid       = "FilesBucketList"
    actions   = ["s3:ListBucket"]
    resources = [module.files.arn]
  }

  statement {
    sid       = "FilesBucketObjects"
    actions   = ["s3:GetObject", "s3:PutObject", "s3:GetObjectTagging", "s3:AbortMultipartUpload"]
    resources = ["${module.files.arn}/*"]
  }

  statement {
    sid       = "DataKey"
    actions   = ["kms:Encrypt", "kms:Decrypt", "kms:GenerateDataKey*", "kms:DescribeKey"]
    resources = [var.kms_key_arn]
  }
}

# worker: the files (including discard: tag, then delete), the audit archive and its signing key.
data "aws_iam_policy_document" "worker" {
  statement {
    sid = "FilesBucketList"
    # ListBucketVersions: discard and purge tag every stored version (PRV-016, audit W3-07).
    actions   = ["s3:ListBucket", "s3:ListBucketVersions"]
    resources = [module.files.arn]
  }

  statement {
    sid = "FilesBucketObjects"
    actions = [
      "s3:GetObject", "s3:PutObject", "s3:DeleteObject", "s3:GetObjectVersion",
      "s3:GetObjectTagging", "s3:PutObjectTagging", "s3:PutObjectVersionTagging",
      "s3:AbortMultipartUpload",
    ]
    resources = ["${module.files.arn}/*"]
  }

  statement {
    sid       = "AuditArchiveWrite"
    actions   = ["s3:PutObject", "s3:GetObject", "s3:ListBucket"]
    resources = [module.audit_archive.arn, "${module.audit_archive.arn}/*"]
  }

  statement {
    sid       = "DataKey"
    actions   = ["kms:Encrypt", "kms:Decrypt", "kms:GenerateDataKey*", "kms:DescribeKey"]
    resources = [var.kms_key_arn]
  }

  statement {
    sid       = "AuditSigning"
    actions   = ["kms:Sign", "kms:GetPublicKey"]
    resources = [module.audit_signing_key.key_arns["audit-signing"]]
  }
}

resource "aws_iam_role_policy" "app" {
  for_each = aws_iam_role.app

  name   = "schoolos-${each.key}"
  role   = each.value.id
  policy = each.key == "api" ? data.aws_iam_policy_document.api.json : data.aws_iam_policy_document.worker.json
}

resource "aws_iam_instance_profile" "host" {
  name = "${local.name}-host"
  role = aws_iam_role.host.name
  tags = local.tags
}

# --- Network ----------------------------------------------------------------------------

resource "aws_security_group" "host" {
  name        = "${local.name}-host"
  description = "Dedicated SchoolOS host: 80/443 inbound only; no SSH"
  vpc_id      = var.vpc_id
  tags        = merge(local.tags, { Name = "${local.name}-host" })
}

# Schools reach their install over the internet; Caddy serves HTTPS and uses :80 for ACME + redirect.
#trivy:ignore:AVD-AWS-0107
resource "aws_vpc_security_group_ingress_rule" "https" {
  security_group_id = aws_security_group.host.id
  description       = "HTTPS (Caddy)"
  ip_protocol       = "tcp"
  from_port         = 443
  to_port           = 443
  cidr_ipv4         = "0.0.0.0/0"
}

#trivy:ignore:AVD-AWS-0107
resource "aws_vpc_security_group_ingress_rule" "http3" {
  security_group_id = aws_security_group.host.id
  description       = "HTTP/3 (QUIC, Caddy)"
  ip_protocol       = "udp"
  from_port         = 443
  to_port           = 443
  cidr_ipv4         = "0.0.0.0/0"
}

#trivy:ignore:AVD-AWS-0107
resource "aws_vpc_security_group_ingress_rule" "http" {
  security_group_id = aws_security_group.host.id
  description       = "HTTP (ACME HTTP-01 + redirect to HTTPS)"
  ip_protocol       = "tcp"
  from_port         = 80
  to_port           = 80
  cidr_ipv4         = "0.0.0.0/0"
}

# Outbound 443: ECR, S3, Secrets Manager, KMS, SSM, ACME, Cognito, LLM/embeddings/OCR APIs, heartbeat.
#trivy:ignore:AVD-AWS-0104
resource "aws_vpc_security_group_egress_rule" "https" {
  security_group_id = aws_security_group.host.id
  description       = "HTTPS egress"
  ip_protocol       = "tcp"
  from_port         = 443
  to_port           = 443
  cidr_ipv4         = "0.0.0.0/0"
}

# Outbound 80: Ubuntu archive/security mirrors (packages are GPG-signed) and ACME OCSP.
#trivy:ignore:AVD-AWS-0104
resource "aws_vpc_security_group_egress_rule" "http" {
  security_group_id = aws_security_group.host.id
  description       = "HTTP egress for signed OS packages"
  ip_protocol       = "tcp"
  from_port         = 80
  to_port           = 80
  cidr_ipv4         = "0.0.0.0/0"
}

# --- Storage + instance ------------------------------------------------------------------

resource "aws_ebs_volume" "data" {
  availability_zone = data.aws_subnet.this.availability_zone
  size              = var.data_volume_gb
  type              = "gp3"
  encrypted         = true
  kms_key_id        = var.kms_key_arn
  final_snapshot    = true
  tags              = merge(local.tags, { Name = "${local.name}-data", "sos-dlm" = local.name })
}

resource "aws_instance" "host" {
  ami                                  = data.aws_ssm_parameter.ubuntu.value
  instance_type                        = var.instance_type
  subnet_id                            = var.subnet_id
  vpc_security_group_ids               = [aws_security_group.host.id]
  iam_instance_profile                 = aws_iam_instance_profile.host.name
  associate_public_ip_address          = false
  ebs_optimized                        = true
  monitoring                           = true
  disable_api_termination              = var.termination_protection
  instance_initiated_shutdown_behavior = "stop"

  metadata_options {
    http_endpoint = "enabled"
    http_tokens   = "required"
    # Audit W3-06: 1 hop, so no container on a Docker bridge can reach the instance role. Each app
    # container gets its own role's short-lived credentials from scripts/app-credentials.sh instead.
    # 2 only while an older release (whose containers still use IMDS) is upgraded (README).
    http_put_response_hop_limit = var.imds_hop_limit
    instance_metadata_tags      = "disabled"
  }

  root_block_device {
    volume_type           = "gp3"
    volume_size           = var.root_volume_gb
    encrypted             = true
    kms_key_id            = var.kms_key_arn
    delete_on_termination = true
  }

  user_data = templatefile("${path.module}/templates/cloud-init.yaml.tftpl", {
    school_code            = var.school_code
    region                 = data.aws_region.current.region
    backup_region          = var.backup_region
    public_host            = var.public_host
    custom_domain          = var.custom_domain
    acme_email             = var.acme_email
    release_version        = var.release_version
    ecr_registry           = var.ecr_registry
    files_bucket           = module.files.id
    audit_bucket           = module.audit_archive.id
    backup_bucket          = var.backup_bucket_name
    backup_kms_key_arn     = var.backup_kms_key_arn
    kms_key_arn            = var.kms_key_arn
    audit_signing_key_arn  = module.audit_signing_key.key_arns["audit-signing"]
    data_volume_id         = aws_ebs_volume.data.id
    secret_json_ids        = "${aws_secretsmanager_secret.generated.arn} ${aws_secretsmanager_secret.operator.arn}"
    oidc_client_secret_arn = var.oidc_client_secret_arn
    oidc_issuer            = var.oidc_issuer
    oidc_client_id         = var.oidc_client_id
    # Break-glass support sign-in (ADR-0023); empty strings = off.
    support_oidc_issuer            = var.support_oidc_issuer
    support_oidc_client_id         = var.support_oidc_client_id
    support_oidc_client_secret_arn = var.support_oidc_client_secret_arn
    control_plane_url              = var.control_plane_url
    bundle_s3_prefix               = var.bundle_s3_prefix
    bundle_sha256                  = var.bundle_sha256
    install_dir                    = var.install_dir
    deployment_id                  = var.deployment_id
    tenant_id                      = var.tenant_id
    log_group                      = local.log_group
    walg_enabled                   = var.walg_enabled ? "true" : "false"
  })
  user_data_replace_on_change = false

  tags        = merge(local.tags, { Name = local.name })
  volume_tags = merge(local.tags, { Name = "${local.name}-root" })

  lifecycle {
    # Never replace a school's host because a newer AMI appeared or bootstrap config changed.
    ignore_changes = [ami, user_data]
  }
}

resource "aws_volume_attachment" "data" {
  device_name                    = "/dev/sdf"
  volume_id                      = aws_ebs_volume.data.id
  instance_id                    = aws_instance.host.id
  stop_instance_before_detaching = true
}

resource "aws_eip" "host" {
  domain = "vpc"
  tags   = merge(local.tags, { Name = local.name })
}

resource "aws_eip_association" "host" {
  allocation_id = aws_eip.host.id
  instance_id   = aws_instance.host.id
}

resource "aws_route53_record" "public_host" {
  count = var.route53_zone_id == null ? 0 : 1

  zone_id = var.route53_zone_id
  name    = var.public_host
  type    = "A"
  ttl     = 300
  records = [aws_eip.host.public_ip]
}

# --- Daily EBS snapshots (crash-consistent, complements pg_dump/WAL-G) ------------------------

# Confused deputy (audit 2026-10-05 hardening): only on behalf of this account.
data "aws_iam_policy_document" "dlm_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["dlm.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [data.aws_caller_identity.current.account_id]
    }
  }
}

resource "aws_iam_role" "dlm" {
  count = var.ebs_snapshots_enabled ? 1 : 0

  name               = "${local.name}-dlm"
  assume_role_policy = data.aws_iam_policy_document.dlm_assume.json
  tags               = local.tags
}

resource "aws_iam_role_policy_attachment" "dlm" {
  count = var.ebs_snapshots_enabled ? 1 : 0

  role       = aws_iam_role.dlm[0].name
  policy_arn = "arn:${local.partition}:iam::aws:policy/service-role/AWSDataLifecycleManagerServiceRole"
}

resource "aws_dlm_lifecycle_policy" "data" {
  count = var.ebs_snapshots_enabled ? 1 : 0

  description        = "Daily snapshots of ${local.name} data volume"
  execution_role_arn = aws_iam_role.dlm[0].arn
  state              = "ENABLED"

  policy_details {
    resource_types = ["VOLUME"]
    target_tags    = { "sos-dlm" = local.name }

    schedule {
      name      = "daily-0200-ist"
      copy_tags = true
      create_rule {
        interval      = 24
        interval_unit = "HOURS"
        times         = ["20:30"]
      }
      retain_rule {
        count = 7
      }
    }
  }

  tags = local.tags
}
