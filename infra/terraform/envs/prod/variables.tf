variable "aws_region" {
  description = "Primary region. Guarded: SchoolOS data lives only in ap-south-1 (NFR-PRV-001)."
  type        = string
  default     = "ap-south-1"

  validation {
    condition     = var.aws_region == "ap-south-1"
    error_message = "Primary region must be ap-south-1 (Mumbai)."
  }
}

variable "dr_region" {
  description = "Backup/DR region. Guarded to ap-south-2 (Hyderabad)."
  type        = string
  default     = "ap-south-2"

  validation {
    condition     = var.dr_region == "ap-south-2"
    error_message = "Backup region must be ap-south-2 (Hyderabad)."
  }
}

variable "aws_account_id" {
  description = "The prod AWS account ID (Terraform refuses to run against any other account)."
  type        = string

  validation {
    condition     = can(regex("^[0-9]{12}$", var.aws_account_id))
    error_message = "aws_account_id must be 12 digits."
  }
}

variable "owner" {
  description = "Mandatory tag: accountable owner (team or email)."
  type        = string
}

variable "cost_center" {
  description = "Mandatory tag: cost centre."
  type        = string
}

variable "data_class" {
  description = "Mandatory tag: highest data class stored (prod holds children's personal data)."
  type        = string
  default     = "C3-children-personal"

  validation {
    condition     = contains(["C0-public", "C1-internal", "C2-confidential", "C3-children-personal", "synthetic"], var.data_class)
    error_message = "data_class must be one of C0-public, C1-internal, C2-confidential, C3-children-personal, synthetic."
  }
}

variable "release_version" {
  description = "SchoolOS release (image tag) to run."
  type        = string
}

variable "app_domain" {
  description = "School-facing hostname."
  type        = string
}

variable "admin_domain" {
  description = "Platform admin hostname."
  type        = string
}

variable "route53_zone_id" {
  description = "Hosted zone for the domains (null = external DNS)."
  type        = string
  default     = null
}

variable "cognito_domain_prefix" {
  description = "Globally unique Cognito hosted-domain prefix."
  type        = string
}

variable "ses_email_identity_arn" {
  description = "Verified SES identity for Cognito emails (recommended in prod)."
  type        = string
  default     = null
}

variable "from_email_address" {
  description = "From address for Cognito emails."
  type        = string
  default     = null
}

variable "alarm_emails" {
  description = "Alarm recipients."
  type        = list(string)
}

variable "monthly_budget_usd" {
  description = "Monthly AWS budget (alerts at 50/80/100 %)."
  type        = number
  default     = null
}

variable "nat_mode" {
  description = "none | single | per_az."
  type        = string
  default     = "single"
}

variable "interface_endpoints" {
  description = "Interface VPC endpoints."
  type        = list(string)
  default     = []
}

variable "rds_instance_class" {
  description = "RDS instance class."
  type        = string
  default     = "db.t4g.medium"
}

variable "rds_allocated_storage_gb" {
  description = "RDS storage."
  type        = number
  default     = 100
}

variable "rds_multi_az" {
  description = "RDS Multi-AZ (Stage 1+)."
  type        = bool
  default     = false
}

variable "rds_backup_retention_days" {
  description = "PITR window (14 Stage 0, 35 Stage 1+)."
  type        = number
  default     = 14
}

variable "redis_node_type" {
  description = "ElastiCache node type."
  type        = string
  default     = "cache.t4g.small"
}

variable "redis_num_nodes" {
  description = "ElastiCache nodes."
  type        = number
  default     = 1
}

variable "services" {
  description = "Per-service sizing (see modules/shared_platform)."
  type = map(object({
    cpu           = number
    memory        = number
    desired_count = number
    autoscaling = optional(object({
      min_capacity = number
      max_capacity = number
      cpu_target   = optional(number, 60)
    }))
  }))
  default = {
    web    = { cpu = 512, memory = 1024, desired_count = 2, autoscaling = { min_capacity = 2, max_capacity = 6 } }
    api    = { cpu = 1024, memory = 2048, desired_count = 2, autoscaling = { min_capacity = 2, max_capacity = 8 } }
    worker = { cpu = 1024, memory = 3072, desired_count = 1, autoscaling = { min_capacity = 1, max_capacity = 4 } }
    beat   = { cpu = 256, memory = 512, desired_count = 1 }
  }
}

variable "dr_backup_retention_days" {
  description = "Retention of replicated RDS backups in ap-south-2."
  type        = number
  default     = 30
}

variable "create_github_oidc_provider" {
  description = "Create the account's GitHub OIDC provider."
  type        = bool
  default     = true
}

variable "state_bucket_arn" {
  description = "Terraform state bucket ARN (bootstrap output)."
  type        = string
}

variable "state_kms_key_arn" {
  description = "Terraform state CMK ARN (bootstrap output)."
  type        = string
}

variable "billing_supplier_legal_name" {
  description = "Supplier legal name on GST invoices (validated by modules/shared_platform)."
  type        = string
}

variable "billing_supplier_gstin" {
  description = "Supplier GSTIN on GST invoices (validated by modules/shared_platform)."
  type        = string
}

variable "billing_supplier_state_code" {
  description = "Supplier GST state code (37 = Andhra Pradesh)."
  type        = string
  default     = "37"
}

variable "billing_supplier_address" {
  description = "Supplier registered address on invoice PDFs (validated by modules/shared_platform)."
  type        = string
}

variable "platform_invoice_bucket" {
  description = "Optional separate bucket for invoice PDFs; null = the files bucket under platform/invoices/."
  type        = string
  default     = null
}

variable "email_provider" {
  description = "Staff invitation email: off (default) or ses (validated by modules/shared_platform)."
  type        = string
  default     = "off"
}

variable "email_domain" {
  description = "SES sending domain (Easy DKIM); null = no SES identity."
  type        = string
  default     = null
}

variable "email_route53_zone_id" {
  description = "Hosted zone for the DKIM CNAMEs (null = publish them by hand from output ses)."
  type        = string
  default     = null
}

variable "email_from" {
  description = "Sender, e.g. \"SchoolOS <no-reply@mail.example.in>\" (address in email_domain)."
  type        = string
  default     = null
}

variable "securityhub_control_exceptions" {
  description = "Security Hub controls disabled with a recorded reason after the first-run triage (docs/10 §5.1); list of { standard, control_id, reason }."
  type = list(object({
    standard   = string
    control_id = string
    reason     = string
  }))
  default = []
}

variable "security_alert_emails" {
  description = "Security alert recipients (GuardDuty/Security Hub/tampering, SEC-023). Null = alarm_emails. The on-call path is docs/11 §6-7."
  type        = list(string)
  default     = null
}

variable "securityhub_alert_labels" {
  description = "Security Hub severity labels that alert (GuardDuty >= 7 always alerts)."
  type        = list(string)
  default     = ["CRITICAL"]
}

variable "guardduty_runtime_agent_management" {
  description = "GuardDuty Runtime Monitoring agents (ECS_FARGATE_AGENT_MANAGEMENT, EC2_AGENT_MANAGEMENT). Empty = Runtime Monitoring off (owner decision: per vCPU-hour cost)."
  type        = list(string)
  default     = []
}

variable "anthropic_zdr_confirmed" {
  description = "SOS_ANTHROPIC_ZDR_CONFIRMED (validated by modules/shared_platform): true only once the Anthropic ZDR agreement and DPA are signed; needed while a models.yaml role uses anthropic."
  type        = bool
  default     = false
}

variable "public_contact_email" {
  description = "SOS_PUBLIC_CONTACT_EMAIL for the marketing pages (validated by modules/shared_platform); empty = hidden."
  type        = string
  default     = ""
}

variable "public_company_name" {
  description = "SOS_PUBLIC_COMPANY_NAME for the marketing pages; empty = hidden."
  type        = string
  default     = ""
}

variable "public_company_address" {
  description = "SOS_PUBLIC_COMPANY_ADDRESS for the About page (lines separated by |); empty = hidden."
  type        = string
  default     = ""
}

variable "public_whatsapp_number" {
  description = "SOS_PUBLIC_WHATSAPP_NUMBER for \"Ask on WhatsApp\" (validated by modules/shared_platform); empty = hidden."
  type        = string
  default     = ""
}
