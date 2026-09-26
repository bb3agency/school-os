variable "school_code" {
  description = "Short stable school identifier used in names, e.g. svhs-guntur."
  type        = string

  validation {
    condition     = can(regex("^[a-z0-9][a-z0-9-]{1,30}[a-z0-9]$", var.school_code))
    error_message = "school_code: 3-32 lowercase letters, digits or hyphens."
  }
}

variable "deployment_id" {
  description = "Control-plane deployment UUID (platform.deployments.id), shown once at provisioning. Tagged on the host as schoolos:deployment-id for fleet targeting."
  type        = string

  validation {
    condition     = can(regex("^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", var.deployment_id))
    error_message = "deployment_id must be a lowercase UUID."
  }
}

variable "vpc_id" {
  description = "VPC for the host."
  type        = string
}

variable "subnet_id" {
  description = "Public subnet for the host (reachable on 80/443 via its Elastic IP)."
  type        = string
}

variable "instance_type" {
  description = "Instance type. Graviton (t4g/m7g/...) uses the arm64 Ubuntu image, others amd64."
  type        = string
  default     = "t4g.medium"
}

variable "root_volume_gb" {
  description = "Encrypted gp3 root volume (OS, Docker images)."
  type        = number
  default     = 30
}

variable "data_volume_gb" {
  description = "Encrypted gp3 data volume mounted at /var/lib/schoolos (PostgreSQL, Valkey, Caddy state)."
  type        = number
  default     = 100
}

variable "kms_key_arn" {
  description = "Per-school CMK (EBS, files bucket, secrets, app DEK wrapping). Crypto-shred = schedule its deletion."
  type        = string
}

variable "public_host" {
  description = "Platform hostname for this school, e.g. svhs-guntur.schoolos.in (SOS_PUBLIC_HOST)."
  type        = string
}

variable "custom_domain" {
  description = "Optional school-owned hostname, e.g. office.svhs.edu.in. The school points an A record at the Elastic IP; Caddy obtains the certificate."
  type        = string
  default     = ""
}

variable "acme_email" {
  description = "Contact email for Let's Encrypt/ACME account."
  type        = string
}

variable "release_version" {
  description = "Initial SchoolOS release (CalVer image tag, e.g. 2026.10.1). Later upgrades use scripts/upgrade.sh."
  type        = string
}

variable "ecr_registry" {
  description = "ECR registry host, e.g. 111122223333.dkr.ecr.ap-south-1.amazonaws.com."
  type        = string
}

variable "ecr_repository_arns" {
  description = "ECR repositories the host may pull from (api, worker, web)."
  type        = list(string)
}

variable "bundle_s3_prefix" {
  description = "s3:// prefix where CI publishes release bundles: <prefix>/<version>/schoolos-dedicated.tar.gz (+ .sha256). upgrade.sh fetches later versions from the same prefix."
  type        = string

  validation {
    condition     = can(regex("^s3://[a-z0-9.-]+/[A-Za-z0-9._/-]+[^/]$", var.bundle_s3_prefix))
    error_message = "bundle_s3_prefix must look like s3://bucket/dedicated (no trailing slash)."
  }
}

variable "bundle_sha256" {
  description = "Expected SHA-256 of the initial bundle (empty = verify against the published .sha256 object only)."
  type        = string
  default     = ""
}

variable "install_dir" {
  description = "Where the active release of deploy/dedicated is linked on the host."
  type        = string
  default     = "/opt/schoolos/deploy/dedicated"
}

variable "fleet_deploy_log_group" {
  description = "Shared CloudWatch log group for fleet SSM Run Command output (created by the shared prod stack)."
  type        = string
  default     = "/schoolos/dedicated/deploy"
}

variable "audit_object_lock_years" {
  description = "COMPLIANCE-mode retention of the per-school audit archive (docs/05 §13: 3 years)."
  type        = number
  default     = 3
}

variable "log_retention_days" {
  description = "Retention of the host's CloudWatch log group (400 days: CERT-In/DPDP)."
  type        = number
  default     = 400
}

variable "artifacts_bucket_arn" {
  description = "Bucket that holds release bundles."
  type        = string
}

variable "artifacts_kms_key_arn" {
  description = "CMK of the artifacts bucket (decrypt only)."
  type        = string
}

variable "backup_bucket_name" {
  description = "Backup bucket in ap-south-2."
  type        = string
}

variable "backup_bucket_arn" {
  description = "Backup bucket ARN."
  type        = string
}

variable "backup_kms_key_arn" {
  description = "CMK in ap-south-2 for backups."
  type        = string
}

variable "backup_region" {
  description = "Backup region."
  type        = string
  default     = "ap-south-2"

  validation {
    condition     = var.backup_region == "ap-south-2"
    error_message = "Backups stay in India: ap-south-2 (NFR-PRV-001)."
  }
}

variable "oidc_issuer" {
  description = "OIDC issuer for this school's staff (SOS_OIDC_ISSUER)."
  type        = string
}

variable "oidc_client_id" {
  description = "BFF OIDC client ID."
  type        = string
}

variable "oidc_client_secret_arn" {
  description = "Secrets Manager ARN of the BFF client secret."
  type        = string
}

variable "control_plane_url" {
  description = "Control-plane base URL for the outbound heartbeat, e.g. https://app.schoolos.in."
  type        = string
}

variable "operator_secret_keys" {
  description = "Keys of the operator-supplied JSON secret (placeholders __SET_ME__ until set)."
  type        = list(string)
  default     = ["SOS_ANTHROPIC_API_KEY", "SOS_FLEET_HMAC_KEY"]
}

variable "generated_secret_version" {
  description = "Bump to regenerate every generated credential (then run scripts/fetch-secrets.sh and the documented rotation steps)."
  type        = number
  default     = 1
}

variable "walg_enabled" {
  description = "Enable WAL-G continuous archiving to the backup bucket (RPO <= 15 min instead of 24 h)."
  type        = bool
  default     = false
}

variable "ebs_snapshots_enabled" {
  description = "Daily crash-consistent EBS snapshots of the data volume via Data Lifecycle Manager (7 retained)."
  type        = bool
  default     = true
}

variable "termination_protection" {
  description = "EC2 API termination protection."
  type        = bool
  default     = true
}

variable "route53_zone_id" {
  description = "Hosted zone for public_host (null = create the A record elsewhere)."
  type        = string
  default     = null
}

variable "tags" {
  description = "Extra tags."
  type        = map(string)
  default     = {}
}
