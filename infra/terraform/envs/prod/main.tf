# Production shared tier (ap-south-1) + DR copies (ap-south-2).
# Deletion protection is hard-wired on here (RDS, ALB, Cognito) and asserted by tests/prod.tftest.hcl.

module "platform" {
  source = "../../modules/shared_platform"

  env             = "prod"
  release_version = var.release_version

  # Supplier block on GST invoices (FR-PLT-016): the registered legal name and GSTIN.
  billing_supplier_legal_name = var.billing_supplier_legal_name
  billing_supplier_gstin      = var.billing_supplier_gstin
  billing_supplier_state_code = var.billing_supplier_state_code

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
  rds_deletion_protection   = true
  rds_backup_retention_days = var.rds_backup_retention_days
  redis_node_type           = var.redis_node_type
  redis_num_nodes           = var.redis_num_nodes

  audit_object_lock_mode  = "COMPLIANCE"
  audit_object_lock_years = 3
  force_destroy_buckets   = false
  alb_deletion_protection = true
  enable_execute_command  = false

  services               = var.services
  expose_fleet_heartbeat = true

  alarm_emails       = var.alarm_emails
  monthly_budget_usd = var.monthly_budget_usd

  github_deploy_environment   = "production"
  github_allow_main_branch    = false
  create_github_oidc_provider = var.create_github_oidc_provider
  # PR plans never touch prod; prod plan/apply runs in the protected "production-infra" environment.
  create_plan_role         = false
  plan_can_read_secrets    = false
  create_apply_role        = true
  github_apply_environment = "production-infra"
  state_bucket_arn         = var.state_bucket_arn
  state_kms_key_arn        = var.state_kms_key_arn
}

# --- DR region (ap-south-2) ----------------------------------------------------------------------

module "kms_dr" {
  source    = "../../modules/kms"
  providers = { aws = aws.dr }

  name_prefix = "sos-prod-dr"
  keys = {
    backup = { description = "SchoolOS prod: replicated RDS backups in ap-south-2" }
  }
}

module "rds_dr" {
  source    = "../../modules/rds_backup_replication"
  providers = { aws = aws.dr }

  source_db_instance_arn = module.platform.rds_arn
  kms_key_arn            = module.kms_dr.key_arns["backup"]
  retention_days         = var.dr_backup_retention_days
}
