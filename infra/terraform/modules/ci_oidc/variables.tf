variable "name_prefix" {
  description = "Role name prefix, e.g. sos-prod."
  type        = string
}

variable "github_owner" {
  description = "GitHub organisation/user that owns the repository."
  type        = string
  default     = "bb3agency"
}

variable "github_repository" {
  description = "Repository name."
  type        = string
  default     = "school-os"
}

variable "create_oidc_provider" {
  description = "Create the account-wide GitHub OIDC provider (only one may exist per account)."
  type        = bool
  default     = true
}

variable "existing_oidc_provider_arn" {
  description = "Existing provider ARN when create_oidc_provider = false."
  type        = string
  default     = null
}

variable "deploy_environment" {
  description = "GitHub Environment whose jobs may assume the deploy role (e.g. staging, production)."
  type        = string
}

variable "allow_main_branch" {
  description = "Also allow jobs on refs/heads/main without an environment (staging auto-deploy). Keep false for prod."
  type        = bool
  default     = false
}

variable "ecr_repository_arns" {
  description = "ECR repositories the deploy role may push to."
  type        = list(string)
}

variable "ecs_cluster_arn" {
  description = "ECS cluster the deploy role may deploy to."
  type        = string
}

variable "passable_role_arns" {
  description = "Task/execution role ARNs the deploy role may pass to ECS. Never a role that can read the RDS master secret (db-bootstrap)."
  type        = list(string)
}

variable "one_off_task_families" {
  description = "Task definition families the deploy role may start with ecs:RunTask (the migrate task). Never db-bootstrap: it holds the RDS master password."
  type        = list(string)

  validation {
    condition     = length(var.one_off_task_families) > 0 && alltrue([for f in var.one_off_task_families : can(regex("^[A-Za-z0-9_-]{1,255}$", f)) && !strcontains(f, "db-bootstrap")])
    error_message = "List at least one task definition family (no wildcards), and never db-bootstrap."
  }
}

variable "enable_artifacts_publish" {
  description = "Allow the deploy role to publish dedicated-tier bundles to artifacts_bucket_arn."
  type        = bool
  default     = false
}

variable "artifacts_bucket_arn" {
  description = "Bucket for dedicated-tier release bundles (null = no access)."
  type        = string
  default     = null
}

variable "artifacts_kms_key_arn" {
  description = "CMK of the artifacts bucket."
  type        = string
  default     = null
}

variable "create_plan_role" {
  description = "Create a read-only role for `terraform plan` on pull requests."
  type        = bool
  default     = true
}

variable "plan_can_read_secrets" {
  description = "Let the plan role refresh secret versions (GetSecretValue). Only for accounts with synthetic data (staging), and only when plan_environment gates the role (audit W3-04: a same-repo pull request must never read secrets)."
  type        = bool
  default     = false

  validation {
    condition     = !var.plan_can_read_secrets || var.plan_environment != null
    error_message = "plan_can_read_secrets needs plan_environment: a role every pull request can assume must not read secrets (audit W3-04)."
  }
}

variable "plan_environment" {
  description = "GitHub Environment (with required reviewers) the plan role trusts instead of every pull request and main. Null = pull requests and main, read-only and without secrets."
  type        = string
  default     = null

  validation {
    condition     = var.plan_environment == null || can(regex("^[A-Za-z0-9._-]{1,64}$", coalesce(var.plan_environment, "x")))
    error_message = "plan_environment: a GitHub Environment name."
  }
}

variable "secrets_kms_key_arn" {
  description = "CMK that encrypts secrets (plan role decrypt, only with plan_can_read_secrets)."
  type        = string
  default     = null
}

variable "create_apply_role" {
  description = "Create an administrative terraform role assumable only from GitHub Environment apply_environment."
  type        = bool
  default     = false
}

variable "apply_environment" {
  description = "GitHub Environment (with required reviewers) allowed to assume the apply role."
  type        = string
  default     = "infra"
}

variable "state_bucket_arn" {
  description = "Terraform state bucket (plan role reads state and writes the lock file)."
  type        = string
  default     = null
}

variable "state_kms_key_arn" {
  description = "CMK of the state bucket."
  type        = string
  default     = null
}

variable "denied_data_bucket_arns" {
  description = "Buckets holding school data that CI roles must never read (explicit deny)."
  type        = list(string)
  default     = []
}

variable "max_session_seconds" {
  description = "Max session duration for CI roles."
  type        = number
  default     = 3600
}

variable "tags" {
  description = "Extra tags."
  type        = map(string)
  default     = {}
}
