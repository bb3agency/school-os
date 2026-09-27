variable "name_prefix" {
  description = "Name prefix, e.g. sos-prod. Regional names get the region appended where AWS needs global uniqueness (IAM)."
  type        = string
}

variable "is_primary" {
  description = "True in the home region (ap-south-1): records global resource types (IAM) and runs the account-wide Config rules. Exactly one region per account is primary."
  type        = bool
}

# --- GuardDuty ----------------------------------------------------------------------------

variable "guardduty_features" {
  description = <<-EOT
    GuardDuty protection plans for this region, feature name => enabled. Every listed feature is set
    explicitly (ENABLED or DISABLED) so the detector never drifts to AWS defaults.
      S3_DATA_EVENTS          S3 Protection (object-level API anomalies on every bucket in the region)
      EBS_MALWARE_PROTECTION  GuardDuty-initiated malware scans of EC2 volumes (dedicated-tier hosts)
      RDS_LOGIN_EVENTS        RDS Protection (login anomalies; supported engines only)
      LAMBDA_NETWORK_LOGS     Lambda Protection (Cognito pre-token Lambda)
      RUNTIME_MONITORING      Runtime Monitoring (agent; see guardduty_runtime_agent_management)
      EKS_AUDIT_LOGS          EKS audit logs (SchoolOS runs no EKS; keep false)
  EOT
  type        = map(bool)
  default = {
    S3_DATA_EVENTS         = true
    EBS_MALWARE_PROTECTION = true
    RDS_LOGIN_EVENTS       = true
    LAMBDA_NETWORK_LOGS    = true
    RUNTIME_MONITORING     = false
    EKS_AUDIT_LOGS         = false
  }

  validation {
    condition = length(setsubtract(keys(var.guardduty_features), [
      "S3_DATA_EVENTS", "EBS_MALWARE_PROTECTION", "RDS_LOGIN_EVENTS", "LAMBDA_NETWORK_LOGS", "RUNTIME_MONITORING", "EKS_AUDIT_LOGS",
    ])) == 0
    error_message = "Unknown GuardDuty feature name."
  }

  validation {
    condition     = try(var.guardduty_features["S3_DATA_EVENTS"], false)
    error_message = "S3 Protection (S3_DATA_EVENTS) must stay enabled (SEC-023)."
  }

  validation {
    condition     = !try(var.guardduty_features["EKS_AUDIT_LOGS"], false)
    error_message = "SchoolOS runs no EKS (CLAUDE.md §11); keep EKS_AUDIT_LOGS disabled."
  }
}

variable "guardduty_runtime_agent_management" {
  description = "Runtime Monitoring agent management when RUNTIME_MONITORING is enabled: ECS_FARGATE_AGENT_MANAGEMENT (shared tier) and/or EC2_AGENT_MANAGEMENT (dedicated hosts). Billed per vCPU-hour; owner decision."
  type        = list(string)
  default     = []

  validation {
    condition     = length(setsubtract(var.guardduty_runtime_agent_management, ["ECS_FARGATE_AGENT_MANAGEMENT", "EC2_AGENT_MANAGEMENT"])) == 0
    error_message = "Agent management values: ECS_FARGATE_AGENT_MANAGEMENT, EC2_AGENT_MANAGEMENT."
  }
}

variable "guardduty_export" {
  description = "Export GuardDuty findings to S3 (kept beyond the 90 days GuardDuty keeps them): bucket ARN, key prefix and the CMK that encrypts them. Null disables the export."
  type = object({
    bucket_arn  = string
    prefix      = string
    kms_key_arn = string
  })
  default = null
}

# --- AWS Config ---------------------------------------------------------------------------

variable "config_role_arn" {
  description = "IAM role AWS Config assumes to record and deliver (created once per account by security_baseline)."
  type        = string
}

variable "config_bucket_name" {
  description = "Bucket that receives Config snapshots and history."
  type        = string
}

variable "config_s3_key_prefix" {
  description = "Key prefix for Config delivery."
  type        = string
  default     = "config"
}

variable "config_kms_key_arn" {
  description = "CMK Config uses for delivered objects."
  type        = string
}

variable "config_recording_frequency" {
  description = "CONTINUOUS (every change, default) or DAILY (cheaper, coarser evidence)."
  type        = string
  default     = "CONTINUOUS"

  validation {
    condition     = contains(["CONTINUOUS", "DAILY"], var.config_recording_frequency)
    error_message = "config_recording_frequency must be CONTINUOUS or DAILY."
  }
}

variable "config_rules" {
  description = "AWS managed Config rules (rule name => source identifier), created only in the primary region. Security Hub standards add their own service-linked rules on top."
  type        = map(string)
  default     = {}
}

# --- Security Hub -------------------------------------------------------------------------

variable "securityhub_standards" {
  description = "Security Hub standards to subscribe, as the part of the standards ARN after 'standards/'."
  type        = list(string)
  default = [
    "aws-foundational-security-best-practices/v/1.0.0",
    "cis-aws-foundations-benchmark/v/3.0.0",
  ]

  validation {
    condition     = contains(var.securityhub_standards, "aws-foundational-security-best-practices/v/1.0.0") && length([for s in var.securityhub_standards : s if startswith(s, "cis-aws-foundations-benchmark/")]) > 0
    error_message = "SEC-023 needs AWS Foundational Security Best Practices and a CIS AWS Foundations Benchmark version."
  }
}

# --- Event forwarding ---------------------------------------------------------------------

variable "forward_to_event_bus_arn" {
  description = "Non-primary regions: the primary region's default event bus. GuardDuty findings and security-service API calls are forwarded there so one set of alert rules and one SNS topic covers every region. Null in the primary region."
  type        = string
  default     = null
}

variable "tamper_event_sources" {
  description = "CloudTrail eventSource values whose API calls are forwarded (with tamper_event_names)."
  type        = list(string)
  default     = []
}

variable "tamper_event_names" {
  description = "CloudTrail eventName values forwarded to the primary bus (the primary region's tamper rule matches the same list)."
  type        = list(string)
  default     = []
}

variable "tags" {
  description = "Extra tags."
  type        = map(string)
  default     = {}
}
