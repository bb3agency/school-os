# Shared-tier (pooled multi-tenant) platform in ap-south-1: the composition used by envs/staging and
# envs/prod. DR-region resources (ap-south-2 KMS key + RDS backup replication) live in the env roots so
# this module needs only the default provider.
#
# Requirement coverage: SEC-009 (Secrets Manager), SEC-011 (KMS everywhere), SEC-022 (WAF),
# SEC-030 (hardened containers/IaC guardrails), NFR-AVL-002 (PITR + cross-region backups),
# NFR-PRV-001 (India-only regions).

data "aws_caller_identity" "current" {}
data "aws_region" "current" {}

locals {
  name       = "sos-${var.env}"
  account_id = data.aws_caller_identity.current.account_id
  region     = data.aws_region.current.region
  secret_ns  = "sos/${var.env}"
  # Known at plan time (the bucket name is), so tests can check the task-role resources (W3-06).
  files_arn = "arn:aws:s3:::${module.s3.files_bucket}"

  image = {
    api    = "${module.ecr.repository_urls["api"]}:${var.release_version}"
    worker = "${module.ecr.repository_urls[var.worker_image_repository]}:${var.release_version}"
    web    = "${module.ecr.repository_urls["web"]}:${var.release_version}"
  }

  # Stage 0 no-NAT option: tasks in public subnets with public IPs; SGs still admit only the ALB/web.
  task_subnets     = var.nat_mode == "none" ? module.network.public_subnet_ids : module.network.app_subnet_ids
  assign_public_ip = var.nat_mode == "none"

  db_secret   = module.secrets.db_secret_arns
  rnd_secret  = module.secrets.random_secret_arns
  op_secret   = module.secrets.operator_secret_arns
  kms_data    = module.kms.key_arns["data"]
  kms_logs    = module.kms.key_arns["logs"]
  kms_audit   = module.kms.key_arns["audit"]
  kms_signing = module.kms.key_arns["audit-signing"]
  master_user = module.rds.master_user_secret_arn

  # Settings for every app container (api, worker, beat, migrate). Names are exactly the ones
  # apps/api/app/core/config.py reads, and each container gets them all so it passes the staging/prod
  # start-up guards on its own (no local-dev key wrapper, no dev-only secrets, no placeholder invoice
  # supplier). apps/api/tests/deploy/test_env_contract.py parses these maps and checks both rules.
  app_env = merge({
    SOS_ENV             = var.env
    SOS_DEPLOYMENT_MODE = "shared"
    SOS_VERSION         = var.release_version
    AWS_REGION          = local.region
    SOS_S3_BUCKET_FILES = module.s3.files_bucket
    SOS_S3_BUCKET_AUDIT = module.s3.audit_bucket
    # SSE-KMS with the files bucket's CMK on every write and presigned POST (FR-DOC-003, SEC-011).
    SOS_S3_KMS_KEY_ID          = local.kms_data
    SOS_OIDC_ISSUER            = module.cognito.tenant_issuer
    SOS_OIDC_AUDIENCE          = module.cognito.tenant_client_id
    SOS_PLATFORM_OIDC_ISSUER   = module.cognito.platform_issuer
    SOS_PLATFORM_OIDC_AUDIENCE = module.cognito.platform_client_id
    # Break-glass support sign-in (ADR-0023): the support app client of the operator pool. Issuer
    # and JWKS are left to their defaults (SOS_PLATFORM_OIDC_*, the same pool), which the shared-tier
    # config guard requires anyway.
    SOS_SUPPORT_OIDC_AUDIENCE       = module.cognito.support_client_id
    SOS_KEY_WRAPPER                 = "kms"
    SOS_KMS_DATA_KEY_ARN            = local.kms_data
    SOS_AUDIT_SIGNING_KEY_ARN       = local.kms_signing
    SOS_BILLING_SUPPLIER_LEGAL_NAME = var.billing_supplier_legal_name
    SOS_BILLING_SUPPLIER_GSTIN      = var.billing_supplier_gstin
    SOS_BILLING_SUPPLIER_STATE_CODE = var.billing_supplier_state_code
    SOS_BILLING_SUPPLIER_ADDRESS    = var.billing_supplier_address
    SOS_LOG_LEVEL                   = var.log_level
    # Claude safety lock (docs/10 §11): the api and every Celery process refuse to start in
    # staging/prod while a models.yaml role uses anthropic, unless this is true (default false).
    SOS_ANTHROPIC_ZDR_CONFIRMED = tostring(var.anthropic_zdr_confirmed)
  }, local.invoice_env)

  # Staff invitation email (notifications.send_email runs in the worker; the api queues it). Only the
  # api and worker get these; off by default. Links in emails point at the school app.
  email_env = merge(
    { SOS_EMAIL_PROVIDER = var.email_provider },
    var.email_from == null ? {} : {
      SOS_EMAIL_FROM                  = var.email_from
      SOS_EMAIL_APP_URL               = "https://${var.app_domain}"
      SOS_EMAIL_SES_CONFIGURATION_SET = one(module.ses[*].configuration_set_name)
    },
  )

  # Invoice PDFs (docs/16 §5.8): a separate control-plane bucket only when configured; otherwise the
  # app uses the files bucket under platform/invoices/.
  invoice_env = var.platform_invoice_bucket == null ? {} : {
    SOS_PLATFORM_INVOICE_BUCKET = var.platform_invoice_bucket
  }

  # Secrets every app container needs to start: the guarded settings and the broker.
  app_base_secrets = {
    SOS_DATABASE_URL          = "${local.db_secret["app"]}:url::"
    SOS_PLATFORM_DATABASE_URL = "${local.db_secret["platform"]}:url::"
    SOS_REDIS_URL             = "${module.redis.secret_arn}:url::"
    SOS_SERVICE_TOKEN_KEY     = local.rnd_secret["service_token_key"]
  }
  app_base_secret_arns = [
    local.db_secret["app"], local.db_secret["platform"], module.redis.secret_arn, local.rnd_secret["service_token_key"],
  ]

  # Provider API keys: only for the containers that call providers (api, worker).
  provider_secrets     = { for env_name, short in var.operator_secret_env : env_name => local.op_secret[short] }
  provider_secret_arns = [for short in values(var.operator_secret_env) : local.op_secret[short]]

  # The only queue the worker-pdf service consumes (ADR-0025). var.worker_queues + this = every queue
  # of sos_worker.celery_app.QUEUES (apps/api/tests/deploy/test_env_contract.py).
  pdf_worker_queues = "pdf"
}

# --- Keys ------------------------------------------------------------------------------

module "kms" {
  source = "../kms"

  name_prefix = local.name
  keys = {
    data = {
      description = "SchoolOS ${var.env}: RDS, S3 files, Secrets Manager, ECR, ElastiCache, DEK wrapping"
    }
    audit = {
      description = "SchoolOS ${var.env}: audit archive (Object Lock)"
    }
    backup = {
      description = "SchoolOS ${var.env}: backups and snapshots in ap-south-1"
    }
    logs = {
      description           = "SchoolOS ${var.env}: CloudWatch Logs and alarm topic"
      allow_cloudwatch_logs = true
      service_principals    = ["cloudwatch.amazonaws.com", "budgets.amazonaws.com"]
    }
    # Signs the daily audit archives (ECDSA_SHA_256, FR-AUD-004). Asymmetric keys are not rotated
    # by AWS; see modules/kms for the manual rotation note.
    audit-signing = {
      description = "SchoolOS ${var.env}: audit archive signatures (ECC_NIST_P256, SIGN_VERIFY)"
      key_spec    = "ECC_NIST_P256"
      key_usage   = "SIGN_VERIFY"
    }
  }
  tags = var.tags
}

# --- Network + storage ---------------------------------------------------------------------

module "network" {
  source = "../network"

  name                = local.name
  nat_mode            = var.nat_mode
  interface_endpoints = var.interface_endpoints
  log_kms_key_arn     = local.kms_logs
  tags                = var.tags
}

module "s3" {
  source = "../s3"

  name_prefix             = local.name
  bucket_suffix           = local.account_id
  data_kms_key_arn        = local.kms_data
  audit_kms_key_arn       = local.kms_audit
  audit_object_lock_mode  = var.audit_object_lock_mode
  audit_object_lock_years = var.audit_object_lock_years
  audit_object_lock_days  = var.audit_object_lock_days
  force_destroy           = var.force_destroy_buckets
  # Browsers upload (presigned POST) only from the school-facing app; the operator panel on
  # admin_domain never handles school files (docs/07 §10).
  files_upload_origins = ["https://${var.app_domain}"]
  tags                 = var.tags
}

module "ecr" {
  source = "../ecr"

  kms_key_arn = local.kms_data
  tags        = var.tags
}

# --- Data tier ---------------------------------------------------------------------------

module "rds" {
  source = "../rds"

  identifier               = "${local.name}-pg"
  vpc_id                   = module.network.vpc_id
  subnet_ids               = module.network.data_subnet_ids
  instance_class           = var.rds_instance_class
  allocated_storage_gb     = var.rds_allocated_storage_gb
  max_allocated_storage_gb = var.rds_max_allocated_storage_gb
  multi_az                 = var.rds_multi_az
  deletion_protection      = var.rds_deletion_protection
  skip_final_snapshot      = !var.rds_deletion_protection
  backup_retention_days    = var.rds_backup_retention_days
  kms_key_arn              = local.kms_data
  allowed_security_groups = {
    api          = module.api.security_group_id
    worker       = module.worker.security_group_id
    worker-pdf   = module.worker_pdf.security_group_id
    migrate      = module.migrate.security_group_id
    db-bootstrap = module.db_bootstrap.security_group_id
  }
  tags = var.tags
}

module "redis" {
  source = "../redis"

  name               = "${local.name}-valkey"
  vpc_id             = module.network.vpc_id
  subnet_ids         = module.network.data_subnet_ids
  node_type          = var.redis_node_type
  num_cache_clusters = var.redis_num_nodes
  kms_key_arn        = local.kms_data
  log_kms_key_arn    = local.kms_logs # the data key does not grant CloudWatch Logs
  secret_name        = "${local.secret_ns}/valkey"
  allowed_security_groups = {
    web        = module.web.security_group_id
    api        = module.api.security_group_id
    worker     = module.worker.security_group_id
    worker-pdf = module.worker_pdf.security_group_id
    beat       = module.beat.security_group_id
  }
  tags = var.tags
}

module "secrets" {
  source = "../secrets"

  name_prefix = local.secret_ns
  kms_key_arn = local.kms_data
  db = {
    host        = module.rds.address
    port        = 5432
    name        = "schoolos"
    sslrootcert = var.db_ssl_root_cert_path
  }
  random_secrets = {
    service_token_key = { description = "HS256 key for BFF->API service tokens (SOS_SERVICE_TOKEN_KEY)" }
    session_secret    = { description = "BFF session encryption secret (SESSION_SECRET)" }
  }
  operator_secrets = var.operator_secrets
  tags             = var.tags
}

# --- Identity ---------------------------------------------------------------------------

# Redirect and sign-out URLs match what the BFF sends: it builds every one of them from APP_BASE_URL
# (https://app_domain), operators included (apps/web/src/server/config.ts).
module "cognito" {
  source = "../cognito"

  name_prefix            = local.name
  tenant_domain_prefix   = "${var.cognito_domain_prefix}-schools"
  tenant_callback_urls   = ["https://${var.app_domain}${var.bff_callback_path}"]
  tenant_logout_urls     = ["https://${var.app_domain}/signed-out"]
  create_platform_pool   = true
  platform_domain_prefix = "${var.cognito_domain_prefix}-ops"
  platform_callback_urls = ["https://${var.app_domain}${var.platform_callback_path}"]
  platform_logout_urls   = ["https://${var.app_domain}/signed-out?kind=operator"]
  create_support_client  = true
  support_callback_urls  = ["https://${var.app_domain}/bff/auth/support/callback"]
  support_logout_urls    = ["https://${var.app_domain}/signed-out?kind=support"]
  deletion_protection    = var.rds_deletion_protection ? "ACTIVE" : "INACTIVE"
  ses_email_identity_arn = var.ses_email_identity_arn
  from_email_address     = var.from_email_address
  secrets_kms_key_arn    = local.kms_data
  secret_name_prefix     = local.secret_ns
  logs_kms_key_arn       = local.kms_logs
  tags                   = var.tags
}

# --- Email (staff invitations, US-102) ------------------------------------------------------------
# SES v2 domain identity with Easy DKIM + default configuration set; reputation alarms to the ops topic.
# The account starts in the SES sandbox: request production access by hand (docs/10 §5.2).

module "ses" {
  source = "../ses_email"
  count  = var.email_domain == null ? 0 : 1

  name_prefix       = local.name
  domain            = var.email_domain
  route53_zone_id   = var.email_route53_zone_id
  reputation_alarms = true
  alarm_topic_arn   = module.observability.alarm_topic_arn
  tags              = var.tags
}

# --- Edge ---------------------------------------------------------------------------------

module "alb" {
  source = "../alb_waf"

  name                    = local.name
  vpc_id                  = module.network.vpc_id
  vpc_cidr                = module.network.vpc_cidr_block
  public_subnet_ids       = module.network.public_subnet_ids
  domain_names            = [var.app_domain, var.admin_domain]
  route53_zone_id         = var.route53_zone_id
  deletion_protection     = var.alb_deletion_protection
  access_logs_bucket      = module.s3.logs_bucket
  waf_rate_limit_per_5min = var.waf_rate_limit_per_5min
  expose_fleet_heartbeat  = var.expose_fleet_heartbeat
  log_kms_key_arn         = local.kms_logs
  tags                    = var.tags
}

module "cluster" {
  source = "../ecs_cluster"

  name                   = local.name
  kms_key_arn            = local.kms_logs
  ec2_capacity_providers = [module.pdf_capacity.capacity_provider_name]
  tags                   = var.tags
}

# --- PDF rendering capacity (ADR-0025 option A) --------------------------------------------------
# Chromium's sandbox needs a user namespace, which Fargate forbids. The pdf queue therefore runs on
# EC2 instances whose Docker daemon's default seccomp profile allows it (docker-default + chroot,
# clone, unshare); only worker-pdf is placed there. Every other service stays on Fargate.

module "pdf_capacity" {
  source = "../ecs_ec2_capacity"

  name                        = "${local.name}-pdf"
  cluster_name                = module.cluster.name
  cluster_arn                 = module.cluster.arn
  vpc_id                      = module.network.vpc_id
  subnet_ids                  = local.task_subnets
  associate_public_ip_address = local.assign_public_ip
  instance_type               = var.pdf_worker.instance_type
  min_size                    = var.pdf_worker.min_instances
  max_size                    = var.pdf_worker.max_instances
  ebs_kms_key_arn             = local.kms_data
  tags                        = var.tags
}

check "pdf_worker_has_egress" {
  assert {
    condition     = var.nat_mode != "none" || length(var.interface_endpoints) > 0
    error_message = "nat_mode = none: awsvpc tasks on EC2 get no public IP, so worker-pdf cannot reach KMS, CloudWatch or the providers without NAT or interface endpoints; PDF exports will fail (closed)."
  }
}

# --- Task role policies ------------------------------------------------------------------------

# Audit W3-06: the internet-facing api can read and write school files but never tag or delete them
# (no s3:DeleteObject, s3:PutObjectTagging or s3:PutObjectVersionTagging on t/*): tagging an object
# sos-lifecycle=discarded (or export-7d) and deleting it would bypass the 90-day recovery window.
# The api queues every discard as an outbox event (document.object.discard_requested); only the
# worker role below tags and deletes. Asserted by tests/shared_platform.tftest.hcl.
data "aws_iam_policy_document" "api" {
  statement {
    sid       = "FilesList"
    actions   = ["s3:ListBucket"]
    resources = [local.files_arn]
    condition {
      test     = "StringLike"
      variable = "s3:prefix"
      values   = ["t/*"]
    }
  }

  statement {
    sid       = "FilesObjects"
    actions   = ["s3:GetObject", "s3:PutObject", "s3:GetObjectTagging", "s3:AbortMultipartUpload"]
    resources = ["${local.files_arn}/t/*"]
  }

  # Control-plane invoice PDFs (docs/16 §5.8, ADR-0017 Amendment 2026-09-28) and certificates
  # of deletion (docs/16 §5.5, ADR-0029): rendered by worker-pdf, downloaded through presigned
  # GETs signed by the api. Never under a school prefix.
  statement {
    sid     = "InvoicePdfObjects"
    actions = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"]
    resources = [
      "${local.files_arn}/platform/invoices/*",
      "${local.files_arn}/platform/deletion-certificates/*",
    ]
  }

  statement {
    sid       = "DataKey"
    actions   = ["kms:Encrypt", "kms:Decrypt", "kms:GenerateDataKey", "kms:GenerateDataKeyWithoutPlaintext", "kms:DescribeKey"]
    resources = [local.kms_data]
  }

  statement {
    sid       = "AppMetrics"
    actions   = ["cloudwatch:PutMetricData"]
    resources = ["*"]
    condition {
      test     = "StringEquals"
      variable = "cloudwatch:namespace"
      values   = ["SchoolOS"]
    }
  }

  # Optional separate invoice bucket (platform_invoice_bucket): the same prefix as in the files bucket.
  dynamic "statement" {
    for_each = var.platform_invoice_bucket == null ? [] : [var.platform_invoice_bucket]
    content {
      sid     = "InvoicePdfBucketObjects"
      actions = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"]
      resources = [
        "arn:aws:s3:::${statement.value}/platform/invoices/*",
        "arn:aws:s3:::${statement.value}/platform/deletion-certificates/*",
      ]
    }
  }
}

# worker-pdf: the api's file access plus the one tag its exports carry (export-7d, set in the same
# PUT by documents.store_export_file), only under t/<tenant>/exports/. No delete, no other tag.
data "aws_iam_policy_document" "worker_pdf" {
  source_policy_documents = [data.aws_iam_policy_document.api.json]

  statement {
    sid       = "ExportLifecycleTag"
    actions   = ["s3:PutObjectTagging"]
    resources = ["${local.files_arn}/t/*/exports/*"]
    condition {
      test     = "StringEquals"
      variable = "s3:RequestObjectTag/sos-lifecycle"
      values   = ["export-7d"]
    }
  }
}

data "aws_iam_policy_document" "worker" {
  source_policy_documents = [data.aws_iam_policy_document.api.json]

  # Audit W3-06: only the worker discards (PRV-016, retention purges, offboarding, the upload path's
  # leftovers queued by the api) and tags exports on upload. ListBucketVersions: discard and purge
  # tag every stored version (audit W3-07).
  statement {
    sid       = "FilesListVersions"
    actions   = ["s3:ListBucketVersions"]
    resources = [local.files_arn]
    condition {
      test     = "StringLike"
      variable = "s3:prefix"
      values   = ["t/*"]
    }
  }

  statement {
    sid       = "FilesDiscard"
    actions   = ["s3:DeleteObject", "s3:PutObjectTagging", "s3:PutObjectVersionTagging"]
    resources = ["${local.files_arn}/t/*"]
  }

  # Daily signed audit export (07 §4 T4): write-only into the Object Lock bucket.
  statement {
    sid       = "AuditArchiveWrite"
    actions   = ["s3:PutObject", "s3:GetObject"]
    resources = ["${module.s3.audit_bucket_arn}/t/*"]
  }

  statement {
    sid       = "AuditKey"
    actions   = ["kms:GenerateDataKey", "kms:Decrypt", "kms:DescribeKey"]
    resources = [local.kms_audit]
  }

  # audit.archive_daily runs in the worker and signs each archive (KmsSigner, ECDSA_SHA_256). Beat
  # only enqueues tasks, so it gets no KMS access.
  statement {
    sid       = "AuditSigning"
    actions   = ["kms:Sign", "kms:GetPublicKey"]
    resources = [local.kms_signing]
  }

  # Staff invitation email: notifications.send_email (worker) sends through the SES identity and its
  # configuration set only, from the sending domain only.
  dynamic "statement" {
    for_each = var.email_provider == "ses" ? [1] : []
    content {
      sid       = "SesSendInvitations"
      actions   = ["ses:SendEmail", "ses:SendRawEmail"]
      resources = [one(module.ses[*].identity_arn), one(module.ses[*].configuration_set_arn)]
      condition {
        test     = "StringLike"
        variable = "ses:FromAddress"
        values   = ["*@${coalesce(var.email_domain, "invalid.invalid")}"]
      }
    }
  }
}

# --- Services -------------------------------------------------------------------------------

module "web" {
  source = "../ecs_service"

  cpu_architecture       = var.cpu_architecture
  name                   = "${local.name}-web"
  cluster_arn            = module.cluster.arn
  cluster_name           = module.cluster.name
  image                  = local.image.web
  cpu                    = var.services["web"].cpu
  memory                 = var.services["web"].memory
  desired_count          = var.services["web"].desired_count
  autoscaling            = var.services["web"].autoscaling
  container_port         = 3000
  user                   = var.container_user
  writable_paths         = ["/tmp", "/app/.next/cache"]
  vpc_id                 = module.network.vpc_id
  vpc_cidr               = module.network.vpc_cidr_block
  subnet_ids             = local.task_subnets
  assign_public_ip       = local.assign_public_ip
  egress_vpc_ports       = [6379, 8000]
  enable_execute_command = var.enable_execute_command

  ingress_from_security_groups   = { alb = module.alb.security_group_id }
  attach_load_balancer           = true
  load_balancer_target_group_arn = module.alb.web_target_group_arn
  service_connect                = { namespace_arn = module.cluster.service_connect_namespace_arn }

  environment = {
    NODE_ENV                = "production"
    SOS_ENV                 = var.env
    APP_BASE_URL            = "https://${var.app_domain}"
    PLATFORM_BASE_URL       = "https://${var.admin_domain}"
    API_INTERNAL_URL        = "http://api:8000"
    OIDC_ISSUER             = module.cognito.tenant_issuer
    OIDC_CLIENT_ID          = module.cognito.tenant_client_id
    PLATFORM_OIDC_ISSUER    = module.cognito.platform_issuer
    PLATFORM_OIDC_CLIENT_ID = module.cognito.platform_client_id
    # Break-glass support sign-in (ADR-0023); the issuer defaults to PLATFORM_OIDC_ISSUER (same pool).
    SUPPORT_OIDC_CLIENT_ID = module.cognito.support_client_id
    # Origin of presigned upload/preview URLs (CSP connect-src + img-src, SEC-010/SEC-016).
    FILES_ORIGIN = module.s3.files_browser_origin
    # Public marketing site (docs/17 §5.6), read at request time; empty = that part is hidden.
    # Not secrets. Only the web task gets them (dedicated hosts serve no marketing pages).
    SOS_PUBLIC_CONTACT_EMAIL   = var.public_contact_email
    SOS_PUBLIC_COMPANY_NAME    = var.public_company_name
    SOS_PUBLIC_COMPANY_ADDRESS = var.public_company_address
    SOS_PUBLIC_WHATSAPP_NUMBER = var.public_whatsapp_number
  }
  secrets = {
    SESSION_SECRET              = local.rnd_secret["session_secret"]
    SOS_SERVICE_TOKEN_KEY       = local.rnd_secret["service_token_key"]
    OIDC_CLIENT_SECRET          = module.cognito.tenant_client_secret_arn
    PLATFORM_OIDC_CLIENT_SECRET = module.cognito.platform_client_secret_arn
    SUPPORT_OIDC_CLIENT_SECRET  = module.cognito.support_client_secret_arn
    REDIS_URL                   = "${module.redis.secret_arn}:url::"
  }
  secret_arns = [
    local.rnd_secret["session_secret"], local.rnd_secret["service_token_key"],
    module.cognito.tenant_client_secret_arn, module.cognito.platform_client_secret_arn,
    module.cognito.support_client_secret_arn, module.redis.secret_arn,
  ]
  secrets_kms_key_arns = [local.kms_data]
  log_kms_key_arn      = local.kms_logs
  health_check         = { command = ["CMD-SHELL", "node -e \"fetch('http://127.0.0.1:3000/healthz').then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))\""] }
  tags                 = var.tags
}

module "api" {
  source = "../ecs_service"

  cpu_architecture       = var.cpu_architecture
  name                   = "${local.name}-api"
  cluster_arn            = module.cluster.arn
  cluster_name           = module.cluster.name
  image                  = local.image.api
  cpu                    = var.services["api"].cpu
  memory                 = var.services["api"].memory
  desired_count          = var.services["api"].desired_count
  autoscaling            = var.services["api"].autoscaling
  container_port         = 8000
  user                   = var.container_user
  vpc_id                 = module.network.vpc_id
  vpc_cidr               = module.network.vpc_cidr_block
  subnet_ids             = local.task_subnets
  assign_public_ip       = local.assign_public_ip
  egress_vpc_ports       = [5432, 6379]
  enable_execute_command = var.enable_execute_command

  ingress_from_security_groups = merge(
    { web = module.web.security_group_id },
    var.expose_fleet_heartbeat ? { alb = module.alb.security_group_id } : {},
  )
  attach_load_balancer           = var.expose_fleet_heartbeat
  load_balancer_target_group_arn = module.alb.api_target_group_arn
  service_connect = {
    namespace_arn     = module.cluster.service_connect_namespace_arn
    server            = true
    discovery_name    = "api"
    client_alias_port = 8000
  }

  environment             = merge(local.app_env, local.email_env, { SOS_SERVICE_NAME = "api" })
  secrets                 = merge(local.app_base_secrets, local.provider_secrets)
  secret_arns             = concat(local.app_base_secret_arns, local.provider_secret_arns)
  secrets_kms_key_arns    = [local.kms_data]
  attach_task_role_policy = true
  task_role_policy_json   = data.aws_iam_policy_document.api.json
  log_kms_key_arn         = local.kms_logs
  health_check            = { command = ["CMD-SHELL", "python -c \"import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=3).status == 200 else 1)\""] }
  tags                    = var.tags
}

module "worker" {
  source = "../ecs_service"

  cpu_architecture       = var.cpu_architecture
  name                   = "${local.name}-worker"
  cluster_arn            = module.cluster.arn
  cluster_name           = module.cluster.name
  image                  = local.image.worker
  cpu                    = var.services["worker"].cpu
  memory                 = var.services["worker"].memory
  desired_count          = var.services["worker"].desired_count
  autoscaling            = var.services["worker"].autoscaling
  command                = ["celery", "-A", var.celery_app, "worker", "--loglevel=INFO", "-Q", var.worker_queues]
  stop_timeout           = 120
  user                   = var.container_user
  vpc_id                 = module.network.vpc_id
  vpc_cidr               = module.network.vpc_cidr_block
  subnet_ids             = local.task_subnets
  assign_public_ip       = local.assign_public_ip
  egress_vpc_ports       = [5432, 6379]
  enable_execute_command = var.enable_execute_command
  service_connect        = { namespace_arn = module.cluster.service_connect_namespace_arn }

  environment             = merge(local.app_env, local.email_env, { SOS_SERVICE_NAME = "worker" })
  secrets                 = merge(local.app_base_secrets, local.provider_secrets)
  secret_arns             = concat(local.app_base_secret_arns, local.provider_secret_arns)
  secrets_kms_key_arns    = [local.kms_data]
  attach_task_role_policy = true
  task_role_policy_json   = data.aws_iam_policy_document.worker.json
  log_kms_key_arn         = local.kms_logs
  tags                    = var.tags
}

# Consumes only the pdf queue, on the sandbox capacity (ADR-0025 option A), with the Chromium sandbox
# on (pdf.chromium_sandbox). No provider API keys (it calls no LLM/embeddings/OCR provider); task
# role = the api's file access + the export-7d tag (worker_pdf), no delete, no audit archive or signing key.
module "worker_pdf" {
  source = "../ecs_service"

  name                   = "${local.name}-worker-pdf"
  cluster_arn            = module.cluster.arn
  cluster_name           = module.cluster.name
  capacity_provider_name = one(module.cluster.ec2_capacity_providers)
  placement_constraint   = module.pdf_capacity.placement_constraint
  cpu_architecture       = module.pdf_capacity.cpu_architecture
  image                  = local.image.worker
  cpu                    = var.pdf_worker.cpu
  memory                 = var.pdf_worker.memory
  desired_count          = var.pdf_worker.desired_count
  command                = ["celery", "-A", var.celery_app, "worker", "--loglevel=INFO", "--concurrency=${var.pdf_worker.concurrency}", "-Q", local.pdf_worker_queues]
  stop_timeout           = 120
  user                   = var.container_user
  vpc_id                 = module.network.vpc_id
  vpc_cidr               = module.network.vpc_cidr_block
  subnet_ids             = local.task_subnets
  assign_public_ip       = false
  egress_vpc_ports       = [5432, 6379]
  enable_execute_command = var.enable_execute_command

  environment             = merge(local.app_env, { SOS_SERVICE_NAME = "worker-pdf" })
  secrets                 = local.app_base_secrets
  secret_arns             = local.app_base_secret_arns
  secrets_kms_key_arns    = [local.kms_data]
  attach_task_role_policy = true
  task_role_policy_json   = data.aws_iam_policy_document.worker_pdf.json
  log_kms_key_arn         = local.kms_logs
  tags                    = var.tags
}

module "beat" {
  source = "../ecs_service"

  cpu_architecture = var.cpu_architecture
  name             = "${local.name}-beat"
  cluster_arn      = module.cluster.arn
  cluster_name     = module.cluster.name
  image            = local.image.worker
  cpu              = var.services["beat"].cpu
  memory           = var.services["beat"].memory
  desired_count    = 1
  command          = ["celery", "-A", var.celery_app, "beat", "--loglevel=INFO", "--schedule=/tmp/celerybeat-schedule"]
  user             = var.container_user
  vpc_id           = module.network.vpc_id
  vpc_cidr         = module.network.vpc_cidr_block
  subnet_ids       = local.task_subnets
  assign_public_ip = local.assign_public_ip
  egress_vpc_ports = [6379]

  # Beat only talks to Valkey, but it loads the same Settings, so it needs the full base settings to
  # pass the start-up guards (egress stays limited to 6379).
  environment          = merge(local.app_env, { SOS_SERVICE_NAME = "beat" })
  secrets              = local.app_base_secrets
  secret_arns          = local.app_base_secret_arns
  secrets_kms_key_arns = [local.kms_data]
  log_kms_key_arn      = local.kms_logs
  tags                 = var.tags
}

# --- One-off tasks (aws ecs run-task) ------------------------------------------------------------

module "migrate" {
  source = "../ecs_service"

  cpu_architecture = var.cpu_architecture
  name             = "${local.name}-migrate"
  create_service   = false
  cluster_arn      = module.cluster.arn
  cluster_name     = module.cluster.name
  image            = local.image.api
  entry_point      = ["/bin/sh", "-c"]
  command          = [var.migrate_command]
  user             = var.container_user
  vpc_id           = module.network.vpc_id
  vpc_cidr         = module.network.vpc_cidr_block
  subnet_ids       = local.task_subnets
  assign_public_ip = local.assign_public_ip
  egress_vpc_ports = [5432]

  # Alembic and the partition CLI load the same Settings: base settings + the migrator role.
  environment = merge(local.app_env, { SOS_SERVICE_NAME = "migrate" })
  secrets = merge(local.app_base_secrets, {
    SOS_MIGRATOR_DATABASE_URL = "${local.db_secret["migrator"]}:url::"
  })
  secret_arns          = concat(local.app_base_secret_arns, [local.db_secret["migrator"]])
  secrets_kms_key_arns = [local.kms_data]
  log_kms_key_arn      = local.kms_logs
  tags                 = var.tags
}

# Runs infra/db/bootstrap.sql as the RDS master user: creates roles, schemas and extensions (ADR-0013).
# Idempotent; re-run after rotating a role password (bump db_roles[*].version).
module "db_bootstrap" {
  source = "../ecs_service"

  cpu_architecture = var.cpu_architecture
  name             = "${local.name}-db-bootstrap"
  create_service   = false
  cluster_arn      = module.cluster.arn
  cluster_name     = module.cluster.name
  image            = local.image.api
  user             = var.container_user
  vpc_id           = module.network.vpc_id
  vpc_cidr         = module.network.vpc_cidr_block
  subnet_ids       = local.task_subnets
  assign_public_ip = local.assign_public_ip
  egress_vpc_ports = [5432]
  entry_point      = ["/bin/sh", "-c"]
  command = [join(" ", [
    "exec psql -X -v ON_ERROR_STOP=1",
    "-v app_password=\"$SOS_APP_PASSWORD\"",
    "-v migrator_password=\"$SOS_MIGRATOR_PASSWORD\"",
    "-v platform_password=\"$SOS_PLATFORM_PASSWORD\"",
    "-v readonly_password=\"$SOS_READONLY_PASSWORD\"",
    "-f ${var.db_bootstrap_sql_path}",
  ])]

  environment = {
    PGHOST        = module.rds.address
    PGPORT        = "5432"
    PGDATABASE    = "schoolos"
    PGSSLMODE     = "verify-full"
    PGSSLROOTCERT = var.db_ssl_root_cert_path
  }
  secrets = {
    PGUSER                = "${local.master_user}:username::"
    PGPASSWORD            = "${local.master_user}:password::"
    SOS_APP_PASSWORD      = "${local.db_secret["app"]}:password::"
    SOS_MIGRATOR_PASSWORD = "${local.db_secret["migrator"]}:password::"
    SOS_PLATFORM_PASSWORD = "${local.db_secret["platform"]}:password::"
    SOS_READONLY_PASSWORD = "${local.db_secret["readonly"]}:password::"
  }
  secret_arns = [
    local.master_user, local.db_secret["app"], local.db_secret["migrator"],
    local.db_secret["platform"], local.db_secret["readonly"],
  ]
  secrets_kms_key_arns = [local.kms_data]
  log_kms_key_arn      = local.kms_logs
  tags                 = var.tags
}

# Fleet upgrades (SSM Run Command on dedicated hosts, docs/10 §15.5) write their output here.
resource "aws_cloudwatch_log_group" "fleet_deploy" {
  count = var.env == "prod" ? 1 : 0

  name              = "/schoolos/dedicated/deploy"
  retention_in_days = 400
  kms_key_id        = local.kms_logs
  tags              = var.tags
}

# --- Observability + CI ------------------------------------------------------------------------

module "observability" {
  source = "../observability"

  name_prefix                = local.name
  environment                = var.env
  kms_key_arn                = local.kms_logs
  alarm_emails               = var.alarm_emails
  alb_arn_suffix             = module.alb.alb_arn_suffix
  target_group_arn_suffix    = module.alb.web_target_group_arn_suffix
  rds_instance_id            = module.rds.instance_id
  rds_allocated_storage_gb   = var.rds_allocated_storage_gb
  redis_replication_group_id = module.redis.replication_group_id
  monthly_budget_usd         = var.monthly_budget_usd
  # Security alarms read these services' structured logs (docs/07 §15).
  api_log_group_name    = module.api.log_group_name
  worker_log_group_name = module.worker.log_group_name
  web_log_group_name    = module.web.log_group_name
  tags                  = var.tags
}

module "ci" {
  source = "../ci_oidc"

  name_prefix                = local.name
  github_owner               = var.github_owner
  github_repository          = var.github_repository
  create_oidc_provider       = var.create_github_oidc_provider
  existing_oidc_provider_arn = var.existing_github_oidc_provider_arn
  deploy_environment         = var.github_deploy_environment
  allow_main_branch          = var.github_allow_main_branch
  ecr_repository_arns        = values(module.ecr.repository_arns)
  ecs_cluster_arn            = module.cluster.arn
  passable_role_arns = flatten([
    for m in [module.web, module.api, module.worker, module.worker_pdf, module.beat, module.migrate, module.db_bootstrap] :
    [m.task_role_arn, m.execution_role_arn]
  ])
  enable_artifacts_publish = true
  artifacts_bucket_arn     = module.s3.artifacts_bucket_arn
  artifacts_kms_key_arn    = local.kms_data
  create_plan_role         = var.create_plan_role
  plan_can_read_secrets    = var.plan_can_read_secrets
  plan_environment         = var.github_plan_environment
  secrets_kms_key_arn      = local.kms_data
  create_apply_role        = var.create_apply_role
  apply_environment        = var.github_apply_environment
  state_bucket_arn         = var.state_bucket_arn
  state_kms_key_arn        = var.state_kms_key_arn
  denied_data_bucket_arns  = [module.s3.files_bucket_arn, module.s3.audit_bucket_arn]
  tags                     = var.tags
}
