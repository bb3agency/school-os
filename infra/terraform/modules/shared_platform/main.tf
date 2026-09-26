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
  master_user = module.rds.master_user_secret_arn

  app_env = {
    SOS_ENV                    = var.env
    SOS_DEPLOYMENT_MODE        = "shared"
    AWS_REGION                 = local.region
    SOS_S3_BUCKET_FILES        = module.s3.files_bucket
    SOS_S3_BUCKET_AUDIT        = module.s3.audit_bucket
    SOS_OIDC_ISSUER            = module.cognito.tenant_issuer
    SOS_OIDC_AUDIENCE          = module.cognito.tenant_client_id
    SOS_PLATFORM_OIDC_ISSUER   = module.cognito.platform_issuer
    SOS_PLATFORM_OIDC_AUDIENCE = module.cognito.platform_client_id
    SOS_KEY_WRAPPER            = "kms"
    SOS_KMS_KEY_ARN            = local.kms_data
    SOS_LOG_LEVEL              = var.log_level
  }

  app_secrets = merge(
    {
      SOS_DATABASE_URL          = "${local.db_secret["app"]}:url::"
      SOS_PLATFORM_DATABASE_URL = "${local.db_secret["platform"]}:url::"
      SOS_REDIS_URL             = "${module.redis.secret_arn}:url::"
      SOS_SERVICE_TOKEN_KEY     = local.rnd_secret["service_token_key"]
    },
    { for env_name, short in var.operator_secret_env : env_name => local.op_secret[short] },
  )

  app_secret_arns = concat(
    [local.db_secret["app"], local.db_secret["platform"], module.redis.secret_arn, local.rnd_secret["service_token_key"]],
    [for short in values(var.operator_secret_env) : local.op_secret[short]],
  )
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
  tags                    = var.tags
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
  secret_name        = "${local.secret_ns}/valkey"
  allowed_security_groups = {
    web    = module.web.security_group_id
    api    = module.api.security_group_id
    worker = module.worker.security_group_id
    beat   = module.beat.security_group_id
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

module "cognito" {
  source = "../cognito"

  name_prefix            = local.name
  tenant_domain_prefix   = "${var.cognito_domain_prefix}-schools"
  tenant_callback_urls   = ["https://${var.app_domain}${var.bff_callback_path}"]
  tenant_logout_urls     = ["https://${var.app_domain}/"]
  create_platform_pool   = true
  platform_domain_prefix = "${var.cognito_domain_prefix}-ops"
  platform_callback_urls = ["https://${var.admin_domain}${var.platform_callback_path}"]
  platform_logout_urls   = ["https://${var.admin_domain}/"]
  deletion_protection    = var.rds_deletion_protection ? "ACTIVE" : "INACTIVE"
  ses_email_identity_arn = var.ses_email_identity_arn
  from_email_address     = var.from_email_address
  secrets_kms_key_arn    = local.kms_data
  secret_name_prefix     = local.secret_ns
  logs_kms_key_arn       = local.kms_logs
  tags                   = var.tags
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

  name        = local.name
  kms_key_arn = local.kms_logs
  tags        = var.tags
}

# --- Task role policies ------------------------------------------------------------------------

data "aws_iam_policy_document" "api" {
  statement {
    sid       = "FilesList"
    actions   = ["s3:ListBucket"]
    resources = [module.s3.files_bucket_arn]
    condition {
      test     = "StringLike"
      variable = "s3:prefix"
      values   = ["t/*"]
    }
  }

  statement {
    sid       = "FilesObjects"
    actions   = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject", "s3:GetObjectTagging", "s3:PutObjectTagging", "s3:AbortMultipartUpload"]
    resources = ["${module.s3.files_bucket_arn}/t/*"]
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
}

data "aws_iam_policy_document" "worker" {
  source_policy_documents = [data.aws_iam_policy_document.api.json]

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
}

# --- Services -------------------------------------------------------------------------------

module "web" {
  source = "../ecs_service"

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
  }
  secrets = {
    SESSION_SECRET              = local.rnd_secret["session_secret"]
    SOS_SERVICE_TOKEN_KEY       = local.rnd_secret["service_token_key"]
    OIDC_CLIENT_SECRET          = module.cognito.tenant_client_secret_arn
    PLATFORM_OIDC_CLIENT_SECRET = module.cognito.platform_client_secret_arn
    REDIS_URL                   = "${module.redis.secret_arn}:url::"
  }
  secret_arns = [
    local.rnd_secret["session_secret"], local.rnd_secret["service_token_key"],
    module.cognito.tenant_client_secret_arn, module.cognito.platform_client_secret_arn, module.redis.secret_arn,
  ]
  secrets_kms_key_arns = [local.kms_data]
  log_kms_key_arn      = local.kms_logs
  health_check         = { command = ["CMD-SHELL", "node -e \"fetch('http://127.0.0.1:3000/healthz').then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))\""] }
  tags                 = var.tags
}

module "api" {
  source = "../ecs_service"

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

  environment             = local.app_env
  secrets                 = local.app_secrets
  secret_arns             = local.app_secret_arns
  secrets_kms_key_arns    = [local.kms_data]
  attach_task_role_policy = true
  task_role_policy_json   = data.aws_iam_policy_document.api.json
  log_kms_key_arn         = local.kms_logs
  health_check            = { command = ["CMD-SHELL", "python -c \"import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=3).status == 200 else 1)\""] }
  tags                    = var.tags
}

module "worker" {
  source = "../ecs_service"

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

  environment             = local.app_env
  secrets                 = local.app_secrets
  secret_arns             = local.app_secret_arns
  secrets_kms_key_arns    = [local.kms_data]
  attach_task_role_policy = true
  task_role_policy_json   = data.aws_iam_policy_document.worker.json
  log_kms_key_arn         = local.kms_logs
  tags                    = var.tags
}

module "beat" {
  source = "../ecs_service"

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

  environment          = { SOS_ENV = var.env, SOS_DEPLOYMENT_MODE = "shared", AWS_REGION = local.region, SOS_LOG_LEVEL = var.log_level }
  secrets              = { SOS_REDIS_URL = "${module.redis.secret_arn}:url::" }
  secret_arns          = [module.redis.secret_arn]
  secrets_kms_key_arns = [local.kms_data]
  log_kms_key_arn      = local.kms_logs
  tags                 = var.tags
}

# --- One-off tasks (aws ecs run-task) ------------------------------------------------------------

module "migrate" {
  source = "../ecs_service"

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

  environment = { SOS_ENV = var.env, SOS_LOG_LEVEL = var.log_level }
  secrets = {
    SOS_MIGRATOR_DATABASE_URL = "${local.db_secret["migrator"]}:url::"
  }
  secret_arns          = [local.db_secret["migrator"]]
  secrets_kms_key_arns = [local.kms_data]
  log_kms_key_arn      = local.kms_logs
  tags                 = var.tags
}

# Runs infra/db/bootstrap.sql as the RDS master user: creates roles, schemas and extensions (ADR-0013).
# Idempotent; re-run after rotating a role password (bump db_roles[*].version).
module "db_bootstrap" {
  source = "../ecs_service"

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
  tags                       = var.tags
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
    for m in [module.web, module.api, module.worker, module.beat, module.migrate, module.db_bootstrap] :
    [m.task_role_arn, m.execution_role_arn]
  ])
  enable_artifacts_publish = true
  artifacts_bucket_arn     = module.s3.artifacts_bucket_arn
  artifacts_kms_key_arn    = local.kms_data
  create_plan_role         = var.create_plan_role
  plan_can_read_secrets    = var.plan_can_read_secrets
  secrets_kms_key_arn      = local.kms_data
  create_apply_role        = var.create_apply_role
  apply_environment        = var.github_apply_environment
  state_bucket_arn         = var.state_bucket_arn
  state_kms_key_arn        = var.state_kms_key_arn
  denied_data_bucket_arns  = [module.s3.files_bucket_arn, module.s3.audit_bucket_arn]
  tags                     = var.tags
}
