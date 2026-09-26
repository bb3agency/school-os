# Dedicated tier: one isolated host per school (premium plan). Instantiate once per school:
#   terraform init -reconfigure -backend-config=schools/<school_code>.backend.hcl
#   terraform apply -var-file=schools/<school_code>.tfvars
# Decommission (crypto-shred): see deploy/dedicated/README.md "Decommission".

data "aws_caller_identity" "current" {}

locals {
  name           = "sos-ded-${var.school_code}"
  ecr_account    = coalesce(var.ecr_account_id, var.aws_account_id)
  ecr_region     = var.aws_region
  ecr_repos      = ["api", "worker", "web"]
  callback_hosts = compact([var.domain, var.custom_domain])
}

# --- Keys (per school) ---------------------------------------------------------------------

module "kms" {
  source = "../../modules/kms"

  name_prefix = local.name
  keys = {
    data = {
      description           = "SchoolOS dedicated ${var.school_code}: EBS, files, secrets, DEK wrapping, logs"
      allow_cloudwatch_logs = true
    }
  }
}

module "kms_backup" {
  source    = "../../modules/kms"
  providers = { aws = aws.backup }

  name_prefix = local.name
  keys = {
    backup = { description = "SchoolOS dedicated ${var.school_code}: backups in ap-south-2" }
  }
}

# --- Backups bucket (ap-south-2) -------------------------------------------------------------

module "backup_bucket" {
  source    = "../../modules/s3_bucket"
  providers = { aws = aws.backup }

  name        = "${local.name}-backup-${data.aws_caller_identity.current.account_id}"
  kms_key_arn = module.kms_backup.key_arns["backup"]
  object_lock = var.backup_object_lock_days > 0 ? { mode = "GOVERNANCE", days = var.backup_object_lock_days } : null
  lifecycle_rules = [
    { id = "daily", prefix = "daily/", expiration_days = var.daily_backup_retention_days },
    { id = "monthly", prefix = "monthly/", expiration_days = var.monthly_backup_retention_days },
    { id = "drills", prefix = "restore-drills/", expiration_days = 90 },
    { id = "noncurrent", noncurrent_version_expiration_days = 7, abort_incomplete_multipart_days = 7 },
  ]
  tags = { school_code = var.school_code }
}

# --- Network -----------------------------------------------------------------------------------

module "network" {
  source = "../../modules/network"

  name                = local.name
  cidr_block          = var.vpc_cidr
  public_subnet_cidrs = [cidrsubnet(var.vpc_cidr, 8, 0), cidrsubnet(var.vpc_cidr, 8, 1)]
  app_subnet_cidrs    = [cidrsubnet(var.vpc_cidr, 8, 10), cidrsubnet(var.vpc_cidr, 8, 11)]
  data_subnet_cidrs   = [cidrsubnet(var.vpc_cidr, 8, 20), cidrsubnet(var.vpc_cidr, 8, 21)]
  nat_mode            = "none"
  log_kms_key_arn     = module.kms.key_arns["data"]
}

# --- Identity (per-school user pool; no platform pool on dedicated hosts) --------------------------

module "cognito" {
  source = "../../modules/cognito"

  name_prefix          = local.name
  tenant_domain_prefix = local.name
  tenant_callback_urls = [for h in local.callback_hosts : "https://${h}/api/auth/callback"]
  tenant_logout_urls   = [for h in local.callback_hosts : "https://${h}/"]
  create_platform_pool = false
  secrets_kms_key_arn  = module.kms.key_arns["data"]
  secret_name_prefix   = "sos/dedicated/${var.school_code}"
  logs_kms_key_arn     = module.kms.key_arns["data"]
}

# --- Host ----------------------------------------------------------------------------------------

module "host" {
  source = "../../modules/dedicated_host"

  school_code            = var.school_code
  deployment_id          = var.deployment_id
  vpc_id                 = module.network.vpc_id
  subnet_id              = module.network.public_subnet_ids[0]
  instance_type          = var.instance_type
  data_volume_gb         = var.data_volume_gb
  kms_key_arn            = module.kms.key_arns["data"]
  public_host            = var.domain
  custom_domain          = var.custom_domain
  acme_email             = var.acme_email
  release_version        = var.release_version
  ecr_registry           = "${local.ecr_account}.dkr.ecr.${local.ecr_region}.amazonaws.com"
  ecr_repository_arns    = [for r in local.ecr_repos : "arn:aws:ecr:${local.ecr_region}:${local.ecr_account}:repository/schoolos/${r}"]
  bundle_s3_prefix       = "s3://${var.artifacts_bucket}/dedicated"
  bundle_sha256          = var.bundle_sha256
  artifacts_bucket_arn   = "arn:aws:s3:::${var.artifacts_bucket}"
  artifacts_kms_key_arn  = var.artifacts_kms_key_arn
  backup_bucket_name     = module.backup_bucket.id
  backup_bucket_arn      = module.backup_bucket.arn
  backup_kms_key_arn     = module.kms_backup.key_arns["backup"]
  backup_region          = var.backup_region
  oidc_issuer            = module.cognito.tenant_issuer
  oidc_client_id         = module.cognito.tenant_client_id
  oidc_client_secret_arn = module.cognito.tenant_client_secret_arn
  control_plane_url      = var.control_plane_url
  walg_enabled           = var.walg_enabled
  termination_protection = var.termination_protection
  route53_zone_id        = var.route53_zone_id
}
