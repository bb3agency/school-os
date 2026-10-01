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

# --- Account security baseline (SEC-023, pilot-ready gate) ---------------------------------------
# CloudTrail (all regions) into an Object Lock COMPLIANCE bucket for 400 days (CERT-In: 180 days in
# India; DPDP: 1 year), GuardDuty, Config and Security Hub in ap-south-1 and ap-south-2, alerts to
# on-call. Dedicated-tier hosts live in this account (docs/10 §15), so the same baseline covers them;
# their buckets (sos-ded-*) are in the S3 data events. Hard-wired here and asserted by tests.

module "security" {
  source = "../../modules/security_baseline"

  env                = "prod"
  name_prefix        = "sos-prod"
  object_lock_mode   = "COMPLIANCE"
  log_retention_days = 400
  data_event_bucket_arns = [
    "arn:aws:s3:::${module.platform.buckets.files}",
    "arn:aws:s3:::${module.platform.buckets.audit}",
  ]
  data_event_bucket_name_prefixes = ["sos-ded-"]
  access_log_bucket               = module.platform.buckets.logs
  delete_exempt_principal_arns    = []

  alert_emails                       = coalesce(var.security_alert_emails, var.alarm_emails)
  securityhub_alert_labels           = var.securityhub_alert_labels
  securityhub_control_exceptions     = var.securityhub_control_exceptions
  guardduty_runtime_agent_management = var.guardduty_runtime_agent_management
}
