variable "env" {
  description = "staging | prod. The baseline exists only in AWS accounts: never local or ci (docs/10 §1)."
  type        = string

  validation {
    condition     = contains(["staging", "prod"], var.env)
    error_message = "security_baseline runs only in the staging and prod AWS accounts."
  }
}

variable "dr_region" {
  description = "Backup/DR region of the same account (GuardDuty, Config, Security Hub also run there). The trail and alerting live in the provider's region (ap-south-1)."
  type        = string
  default     = "ap-south-2"

  validation {
    condition     = var.dr_region == "ap-south-2"
    error_message = "The backup region is ap-south-2 (Hyderabad; NFR-PRV-001)."
  }
}

variable "name_prefix" {
  description = "Name prefix, e.g. sos-prod."
  type        = string
}

# --- CloudTrail log archive ---------------------------------------------------------------

variable "log_retention_days" {
  description = "Object Lock default retention and lifecycle expiry of CloudTrail logs and security evidence. CERT-In (Apr 2022) requires ICT logs for 180 days in India; DPDP Rules require 1 year; docs/10 §5 sets 400 days."
  type        = number
  default     = 400

  validation {
    condition     = var.log_retention_days >= 180
    error_message = "CERT-In directions require at least 180 days of logs kept in India."
  }
}

variable "object_lock_mode" {
  description = "Object Lock mode of the CloudTrail bucket: COMPLIANCE (nobody, root included, can delete or shorten before expiry; prod) or GOVERNANCE (a principal with s3:BypassGovernanceRetention can; staging teardown)."
  type        = string

  validation {
    condition     = contains(["COMPLIANCE", "GOVERNANCE"], var.object_lock_mode)
    error_message = "object_lock_mode must be COMPLIANCE or GOVERNANCE."
  }
}

variable "data_event_bucket_arns" {
  description = "Buckets whose object-level reads and writes CloudTrail records (S3 data events): the buckets holding school data (files, audit archive)."
  type        = list(string)

  validation {
    condition     = length(var.data_event_bucket_arns) > 0 && alltrue([for a in var.data_event_bucket_arns : can(regex("^arn:aws[a-z-]*:s3:::[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$", a))])
    error_message = "Give at least one bucket ARN (arn:aws:s3:::<bucket>, no key)."
  }
}

variable "data_event_bucket_name_prefixes" {
  description = "Bucket-name prefixes whose objects are also logged (e.g. sos-ded- for every dedicated-tier host's buckets in this account)."
  type        = list(string)
  default     = []

  validation {
    condition     = alltrue([for p in var.data_event_bucket_name_prefixes : can(regex("^[a-z0-9][a-z0-9.-]{2,}$", p))])
    error_message = "Bucket-name prefixes are lowercase and at least 3 characters (no wildcards)."
  }
}

variable "access_log_bucket" {
  description = "Bucket receiving S3 server access logs of the log buckets (the environment's logs bucket; its policy allows logging.s3.amazonaws.com on s3/*)."
  type        = string
}

variable "delete_exempt_principal_arns" {
  description = "Principals exempt from the log buckets' deny-delete statement (e.g. a staging teardown role). Empty in prod: nobody may delete log objects or bypass governance retention."
  type        = list(string)
  default     = []
}

# --- Detection ----------------------------------------------------------------------------

variable "guardduty_features_primary" {
  description = "GuardDuty protection plans in ap-south-1 (see modules/security_detection)."
  type        = map(bool)
  default = {
    S3_DATA_EVENTS         = true
    EBS_MALWARE_PROTECTION = true
    RDS_LOGIN_EVENTS       = true
    LAMBDA_NETWORK_LOGS    = true
    RUNTIME_MONITORING     = false
    EKS_AUDIT_LOGS         = false
  }
}

variable "guardduty_features_dr" {
  description = "GuardDuty protection plans in ap-south-2 (backups only: RDS backup copies, dedicated-host WAL-G/pg_dump buckets)."
  type        = map(bool)
  default = {
    S3_DATA_EVENTS         = true
    EBS_MALWARE_PROTECTION = false
    RDS_LOGIN_EVENTS       = false
    LAMBDA_NETWORK_LOGS    = false
    RUNTIME_MONITORING     = false
    EKS_AUDIT_LOGS         = false
  }
}

variable "guardduty_runtime_agent_management" {
  description = "Runtime Monitoring agents in ap-south-1 (ECS_FARGATE_AGENT_MANAGEMENT, EC2_AGENT_MANAGEMENT); a non-empty list turns RUNTIME_MONITORING on. Owner decision (per vCPU-hour cost)."
  type        = list(string)
  default     = []
}

variable "config_recording_frequency" {
  description = "AWS Config recording frequency: CONTINUOUS or DAILY."
  type        = string
  default     = "CONTINUOUS"
}

variable "config_rules" {
  description = "AWS managed Config rules in ap-south-1 (name => identifier) for the SEC-023 controls: CloudTrail on, encryption, public access, root MFA."
  type        = map(string)
  default = {
    cloudtrail-enabled              = "CLOUD_TRAIL_ENABLED"
    cloudtrail-multi-region         = "MULTI_REGION_CLOUD_TRAIL_ENABLED"
    cloudtrail-log-validation       = "CLOUD_TRAIL_LOG_FILE_VALIDATION_ENABLED"
    cloudtrail-encryption           = "CLOUD_TRAIL_ENCRYPTION_ENABLED"
    root-mfa                        = "ROOT_ACCOUNT_MFA_ENABLED"
    root-no-access-key              = "IAM_ROOT_ACCESS_KEY_CHECK"
    iam-console-mfa                 = "MFA_ENABLED_FOR_IAM_CONSOLE_ACCESS"
    s3-account-public-access-blocks = "S3_ACCOUNT_LEVEL_PUBLIC_ACCESS_BLOCKS_PERIODIC"
    s3-bucket-public-access         = "S3_BUCKET_LEVEL_PUBLIC_ACCESS_PROHIBITED"
    s3-tls-only                     = "S3_BUCKET_SSL_REQUESTS_ONLY"
    s3-encryption                   = "S3_BUCKET_SERVER_SIDE_ENCRYPTION_ENABLED"
    rds-encrypted                   = "RDS_STORAGE_ENCRYPTED"
    rds-not-public                  = "RDS_INSTANCE_PUBLIC_ACCESS_CHECK"
    rds-snapshots-not-public        = "RDS_SNAPSHOTS_PUBLIC_PROHIBITED"
    ebs-encryption-by-default       = "EC2_EBS_ENCRYPTION_BY_DEFAULT"
    ebs-volumes-encrypted           = "ENCRYPTED_VOLUMES"
    ec2-imdsv2                      = "EC2_IMDSV2_CHECK"
    no-ssh-from-internet            = "INCOMING_SSH_DISABLED"
    kms-rotation                    = "CMK_BACKING_KEY_ROTATION_ENABLED"
    guardduty-enabled               = "GUARDDUTY_ENABLED_CENTRALIZED"
    securityhub-enabled             = "SECURITYHUB_ENABLED"
    vpc-flow-logs                   = "VPC_FLOW_LOGS_ENABLED"
  }

  validation {
    condition = length(setsubtract(toset([
      "CLOUD_TRAIL_ENABLED", "S3_BUCKET_LEVEL_PUBLIC_ACCESS_PROHIBITED", "ROOT_ACCOUNT_MFA_ENABLED", "S3_BUCKET_SERVER_SIDE_ENCRYPTION_ENABLED",
    ]), toset(values(var.config_rules)))) == 0
    error_message = "Keep at least the CloudTrail-enabled, public-access, root-MFA and encryption rules (SEC-023)."
  }
}

variable "securityhub_standards" {
  description = "Security Hub standards (ARN part after 'standards/') in both regions."
  type        = list(string)
  default = [
    "aws-foundational-security-best-practices/v/1.0.0",
    "cis-aws-foundations-benchmark/v/3.0.0",
  ]
}

variable "account_public_access_block" {
  description = "Turn on S3 Block Public Access for the whole account (docs/07 §13: deny public S3)."
  type        = bool
  default     = true
}

variable "ebs_encryption_by_default" {
  description = "Encrypt new EBS volumes by default in both regions (dedicated hosts already set their own CMK)."
  type        = bool
  default     = true
}

# --- Alerting (docs/11 §6: GuardDuty high finding = P1 security, runbook R5) ------------------

variable "alert_emails" {
  description = "Security alert recipients (on-call). Confirm each SNS subscription email after apply."
  type        = list(string)

  validation {
    condition     = length(var.alert_emails) > 0
    error_message = "At least one security alert recipient is required (docs/11 §6)."
  }
}

variable "guardduty_alert_min_severity" {
  description = "Lowest GuardDuty severity that pages (7.0 = High; docs/11 §6)."
  type        = number
  default     = 7

  validation {
    condition     = var.guardduty_alert_min_severity >= 1 && var.guardduty_alert_min_severity <= 7
    error_message = "Use 1-7; High findings (>= 7) must always alert."
  }
}

variable "securityhub_alert_labels" {
  description = "Security Hub severity labels (non-GuardDuty findings: failed controls, Config) that alert. CRITICAL by default; add HIGH once the first-run backlog is triaged."
  type        = list(string)
  default     = ["CRITICAL"]

  validation {
    condition     = length(var.securityhub_alert_labels) > 0 && length(setsubtract(var.securityhub_alert_labels, ["CRITICAL", "HIGH", "MEDIUM", "LOW"])) == 0
    error_message = "Labels: CRITICAL, HIGH, MEDIUM, LOW (at least one)."
  }
}

variable "kms_deletion_window_in_days" {
  description = "Waiting period for scheduled deletion of the security-logs CMK."
  type        = number
  default     = 30
}

variable "tags" {
  description = "Extra tags."
  type        = map(string)
  default     = {}
}
