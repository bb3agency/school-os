# Staging shared tier: same composition as prod, small sizes, synthetic data only (invariant 11).
# Differences from prod are deliberate and listed here:
#   - no deletion protection, buckets force-destroyable, ECS Exec enabled for debugging
#   - audit archive Object Lock COMPLIANCE for 30 days (not 3 years) so the account can be torn down
#   - PR plan role may refresh secret versions (staging secrets guard synthetic data only)

module "platform" {
  source = "../../modules/shared_platform"

  env             = "staging"
  release_version = var.release_version

  app_domain             = var.app_domain
  admin_domain           = var.admin_domain
  route53_zone_id        = var.route53_zone_id
  cognito_domain_prefix  = var.cognito_domain_prefix
  ses_email_identity_arn = var.ses_email_identity_arn
  from_email_address     = var.from_email_address

  nat_mode            = var.nat_mode
  interface_endpoints = var.interface_endpoints

  rds_instance_class        = var.rds_instance_class
  rds_allocated_storage_gb  = var.rds_allocated_storage_gb
  rds_multi_az              = var.rds_multi_az
  rds_deletion_protection   = false
  rds_backup_retention_days = var.rds_backup_retention_days
  redis_node_type           = var.redis_node_type
  redis_num_nodes           = var.redis_num_nodes

  audit_object_lock_mode  = "COMPLIANCE"
  audit_object_lock_days  = 30
  force_destroy_buckets   = true
  alb_deletion_protection = false
  enable_execute_command  = true

  services               = var.services
  expose_fleet_heartbeat = true

  alarm_emails       = var.alarm_emails
  monthly_budget_usd = var.monthly_budget_usd

  github_deploy_environment   = "staging"
  github_allow_main_branch    = false
  create_github_oidc_provider = var.create_github_oidc_provider
  create_plan_role            = true
  plan_can_read_secrets       = true
  create_apply_role           = true
  github_apply_environment    = "staging-infra"
  state_bucket_arn            = var.state_bucket_arn
  state_kms_key_arn           = var.state_kms_key_arn
}

# --- DR region (ap-south-2) ----------------------------------------------------------------------

module "kms_dr" {
  source    = "../../modules/kms"
  providers = { aws = aws.dr }

  name_prefix             = "sos-staging-dr"
  deletion_window_in_days = 7
  keys = {
    backup = { description = "SchoolOS staging: replicated RDS backups in ap-south-2" }
  }
}

module "rds_dr" {
  source    = "../../modules/rds_backup_replication"
  count     = var.dr_backup_replication_enabled ? 1 : 0
  providers = { aws = aws.dr }

  source_db_instance_arn = module.platform.rds_arn
  kms_key_arn            = module.kms_dr.key_arns["backup"]
  retention_days         = var.dr_backup_retention_days
}
