# Staging shared tier: same composition as prod, small sizes, synthetic data only (invariant 11).
# Differences from prod are deliberate and listed here:
#   - no deletion protection, buckets force-destroyable, ECS Exec enabled for debugging
#   - audit archive Object Lock COMPLIANCE for 30 days (not 3 years) so the account can be torn down
#   - PR plan role may refresh secret versions (staging secrets guard synthetic data only)

module "platform" {
  source = "../../modules/shared_platform"

  env             = "staging"
  release_version = var.release_version

  # Staging invoices are synthetic: the tfvars example uses a made-up supplier that is not valid
  # for tax invoices (the app only refuses the dev placeholder).
  billing_supplier_legal_name = var.billing_supplier_legal_name
  billing_supplier_gstin      = var.billing_supplier_gstin
  billing_supplier_state_code = var.billing_supplier_state_code
  billing_supplier_address    = var.billing_supplier_address
  platform_invoice_bucket     = var.platform_invoice_bucket
  email_provider              = var.email_provider
  email_domain                = var.email_domain
  email_route53_zone_id       = var.email_route53_zone_id
  email_from                  = var.email_from

  # Claude safety lock (docs/10 §11): true only once the Anthropic ZDR agreement and DPA are signed.
  anthropic_zdr_confirmed = var.anthropic_zdr_confirmed

  # Public marketing site (docs/17 §5.6): empty = hidden.
  public_contact_email   = var.public_contact_email
  public_company_name    = var.public_company_name
  public_company_address = var.public_company_address
  public_whatsapp_number = var.public_whatsapp_number

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

  # Audit W3-04: the plan role trusts every same-repo pull request, so it never reads secrets (a
  # refresh shows no secret versions; their values are write-only anyway).
  github_deploy_environment   = "staging"
  github_allow_main_branch    = false
  create_github_oidc_provider = var.create_github_oidc_provider
  create_plan_role            = true
  plan_can_read_secrets       = false
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
    files  = { description = "SchoolOS staging: locked copy of the files bucket in ap-south-2" }
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

# Audit W3-06 (b): the locked copy of the files bucket, as in prod (on with the DR path). Its versions
# stay locked for retention_days, so tearing staging down waits for the lock (synthetic data only).
module "files_replica" {
  source    = "../../modules/s3_replica"
  count     = var.dr_backup_replication_enabled ? 1 : 0
  providers = { aws = aws.dr }

  name                = "sos-staging-files-replica-${var.aws_account_id}"
  source_region       = var.aws_region
  source_bucket_id    = module.platform.buckets.files
  source_kms_key_arn  = module.platform.kms_key_arns["data"]
  replica_kms_key_arn = module.kms_dr.key_arns["files"]
  retention_days      = var.files_replica_retention_days
  force_destroy       = true
}

# --- Account security baseline (SEC-023) ---------------------------------------------------------
# Same controls as prod so staging exercises them; differences: Object Lock GOVERNANCE (a named
# teardown role may be exempted), 180-day retention (CERT-In minimum), no dedicated-host buckets, and
# it can be switched off (enable_security_baseline) for a short-lived staging account.

module "security" {
  source = "../../modules/security_baseline"
  count  = var.enable_security_baseline ? 1 : 0

  env                = "staging"
  name_prefix        = "sos-staging"
  object_lock_mode   = "GOVERNANCE"
  log_retention_days = var.security_log_retention_days
  data_event_bucket_arns = [
    "arn:aws:s3:::${module.platform.buckets.files}",
    "arn:aws:s3:::${module.platform.buckets.audit}",
  ]
  access_log_bucket            = module.platform.buckets.logs
  delete_exempt_principal_arns = var.security_log_delete_exempt_principal_arns
  kms_deletion_window_in_days  = 7

  alert_emails                   = coalesce(var.security_alert_emails, var.alarm_emails)
  securityhub_alert_labels       = var.securityhub_alert_labels
  securityhub_control_exceptions = var.securityhub_control_exceptions
}
