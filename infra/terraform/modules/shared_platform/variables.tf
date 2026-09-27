variable "env" {
  description = "Environment: staging or prod."
  type        = string

  validation {
    condition     = contains(["staging", "prod"], var.env)
    error_message = "env must be staging or prod."
  }
}

variable "release_version" {
  description = "Image tag deployed to every service (CalVer, e.g. 2026.10.1). Deploys = terraform apply with a new value."
  type        = string
}

variable "app_domain" {
  description = "School-facing hostname, e.g. app.schoolos.in (staging: app.staging.schoolos.in)."
  type        = string
}

variable "admin_domain" {
  description = "Platform admin hostname, e.g. admin.schoolos.in."
  type        = string
}

variable "route53_zone_id" {
  description = "Route 53 hosted zone for both domains (null = manage DNS elsewhere; see outputs)."
  type        = string
  default     = null
}

variable "cognito_domain_prefix" {
  description = "Globally unique prefix for Cognito hosted domains (\"-schools\" and \"-ops\" are appended)."
  type        = string
}

variable "bff_callback_path" {
  description = "BFF OIDC callback path for school staff."
  type        = string
  default     = "/api/auth/callback"
}

variable "platform_callback_path" {
  description = "BFF OIDC callback path for platform operators."
  type        = string
  default     = "/api/auth/platform/callback"
}

variable "ses_email_identity_arn" {
  description = "Verified SES identity for Cognito emails (null = Cognito default sender)."
  type        = string
  default     = null
}

variable "from_email_address" {
  description = "From address for Cognito emails when SES is used."
  type        = string
  default     = null
}

# --- Network ---------------------------------------------------------------------------

variable "nat_mode" {
  description = "none | single | per_az. With none, tasks run in public subnets with public IPs and SGs that only admit the ALB (Stage 0 cost option, docs/10 §3)."
  type        = string
  default     = "single"
}

variable "interface_endpoints" {
  description = "Interface VPC endpoints to add when cost-justified."
  type        = list(string)
  default     = []
}

# --- Data tier --------------------------------------------------------------------------

variable "rds_instance_class" {
  description = "RDS instance class."
  type        = string
  default     = "db.t4g.medium"
}

variable "rds_allocated_storage_gb" {
  description = "RDS storage."
  type        = number
  default     = 50
}

variable "rds_max_allocated_storage_gb" {
  description = "RDS storage autoscaling ceiling."
  type        = number
  default     = 200
}

variable "rds_multi_az" {
  description = "RDS Multi-AZ."
  type        = bool
  default     = false
}

variable "rds_deletion_protection" {
  description = "RDS deletion protection."
  type        = bool
  default     = true
}

variable "rds_backup_retention_days" {
  description = "PITR window."
  type        = number
  default     = 14
}

variable "redis_node_type" {
  description = "ElastiCache node type."
  type        = string
  default     = "cache.t4g.micro"
}

variable "redis_num_nodes" {
  description = "ElastiCache nodes (2+ enables failover)."
  type        = number
  default     = 1
}

variable "audit_object_lock_mode" {
  description = "Audit archive Object Lock mode."
  type        = string
  default     = "COMPLIANCE"
}

variable "audit_object_lock_years" {
  description = "Audit archive retention in years."
  type        = number
  default     = 3
}

variable "audit_object_lock_days" {
  description = "Audit archive retention in days (overrides years; staging)."
  type        = number
  default     = null
}

variable "force_destroy_buckets" {
  description = "Allow destroying non-empty files/log buckets (staging only)."
  type        = bool
  default     = false
}

# --- Compute ----------------------------------------------------------------------------

variable "services" {
  description = "Sizing per long-running service."
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
    web    = { cpu = 512, memory = 1024, desired_count = 1 }
    api    = { cpu = 512, memory = 1024, desired_count = 1 }
    worker = { cpu = 1024, memory = 3072, desired_count = 1 }
    beat   = { cpu = 256, memory = 512, desired_count = 1 }
  }

  validation {
    condition     = alltrue([for k in ["web", "api", "worker", "beat"] : contains(keys(var.services), k)])
    error_message = "services must define web, api, worker and beat."
  }

  validation {
    condition     = try(var.services["beat"].desired_count, 1) == 1 && try(var.services["beat"].autoscaling, null) == null
    error_message = "Celery beat must run exactly one instance."
  }
}

variable "worker_queues" {
  description = "Celery queues consumed by the Fargate worker service: every queue of sos_worker.celery_app.QUEUES except pdf, which only the worker-pdf service on the sandbox capacity consumes (ADR-0025). Beat jobs such as audit archiving, billing and the outbox run on maintenance."
  type        = string
  default     = "ingest,embed,ocr,dq,exports,maintenance"

  validation {
    condition     = !contains([for q in split(",", var.worker_queues) : trimspace(q)], "pdf")
    error_message = "The Fargate worker must not consume pdf: Chromium's sandbox cannot run on Fargate (ADR-0025); worker-pdf consumes it."
  }
}

variable "pdf_worker" {
  description = "The worker-pdf service (queue pdf) and its EC2 capacity whose Docker daemon allows Chromium's sandbox (ADR-0025 option A). A Graviton instance type means ARM64 tasks (the worker image must be built for it). concurrency = Celery processes (one Chromium each)."
  type = object({
    instance_type = optional(string, "t4g.medium")
    min_instances = optional(number, 1)
    max_instances = optional(number, 2)
    cpu           = optional(number, 1024)
    memory        = optional(number, 1536)
    desired_count = optional(number, 1)
    concurrency   = optional(number, 2)
  })
  default = {}

  validation {
    condition     = var.pdf_worker.max_instances >= 1 && var.pdf_worker.min_instances <= var.pdf_worker.max_instances && var.pdf_worker.concurrency >= 1
    error_message = "pdf_worker: 1 <= max_instances, min_instances <= max_instances, concurrency >= 1."
  }
}

variable "container_user" {
  description = "Non-root UID:GID the application images run as."
  type        = string
  default     = "10001:10001"
}

variable "db_ssl_root_cert_path" {
  description = "Path of the RDS CA bundle inside the api/worker images (sslmode=verify-full)."
  type        = string
  default     = "/etc/ssl/certs/rds-global-bundle.pem"
}

variable "db_bootstrap_sql_path" {
  description = "Path of infra/db/bootstrap.sql inside the api image (used by the db-bootstrap one-off task)."
  type        = string
  default     = "/app/infra/db/bootstrap.sql"
}

variable "migrate_command" {
  description = "Shell command of the one-off migration task (runs as sos_migrator via SOS_MIGRATOR_DATABASE_URL)."
  type        = string
  default     = "alembic -c /app/alembic.ini upgrade head && python -m app.audit.partitions --months-ahead 12"
}

variable "celery_app" {
  description = "Celery application module for worker and beat."
  type        = string
  default     = "sos_worker.celery_app"
}

variable "worker_image_repository" {
  description = "ECR repository (api | worker) whose image runs the worker and beat services. CI publishes the `worker` target of apps/api/Dockerfile (api + headless Chromium for the pdf queue) to the worker repository; `api` only for an environment whose release has no worker image (PDF exports then fail)."
  type        = string
  default     = "worker"

  validation {
    condition     = contains(["api", "worker"], var.worker_image_repository)
    error_message = "worker_image_repository must be api or worker."
  }
}

variable "operator_secrets" {
  description = "Operator-supplied secrets: short name => description. Injected into api/worker as the env names in operator_secret_env."
  type        = map(string)
  default = {
    anthropic_api_key  = "Anthropic API key (organisation key, ZDR org) for the knowledge gateway"
    embeddings_api_key = "Embeddings provider API key"
  }
}

variable "operator_secret_env" {
  description = "Env var name => operator secret short name, injected into api and worker."
  type        = map(string)
  default = {
    SOS_ANTHROPIC_API_KEY  = "anthropic_api_key"
    SOS_EMBEDDINGS_API_KEY = "embeddings_api_key"
  }
}

variable "log_level" {
  description = "SOS_LOG_LEVEL."
  type        = string
  default     = "INFO"
}

# --- Billing supplier (GST invoices, FR-PLT-016) ------------------------------------------
# Only the shared tier runs the control plane and issues invoices. The app refuses to start in
# staging/prod with the dev placeholder supplier, so these have no defaults.

variable "billing_supplier_legal_name" {
  description = "Supplier legal name printed on GST invoices (SOS_BILLING_SUPPLIER_LEGAL_NAME)."
  type        = string

  validation {
    condition     = length(trimspace(var.billing_supplier_legal_name)) >= 3 && var.billing_supplier_legal_name != "SchoolOS Synthetic Supplier (dev)"
    error_message = "billing_supplier_legal_name must be the registered legal name, not the dev placeholder."
  }
}

variable "billing_supplier_gstin" {
  description = "Supplier GSTIN (SOS_BILLING_SUPPLIER_GSTIN): 15 characters, starting with the state code."
  type        = string

  validation {
    condition     = can(regex("^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]$", var.billing_supplier_gstin))
    error_message = "billing_supplier_gstin must be a valid Indian GSTIN (e.g. 37ABCDE1234F1Z5)."
  }

  validation {
    condition     = var.billing_supplier_gstin != "37AAAAA0000A1Z5"
    error_message = "billing_supplier_gstin is the dev placeholder; the app refuses it in staging/prod."
  }

  validation {
    condition     = substr(var.billing_supplier_gstin, 0, 2) == var.billing_supplier_state_code
    error_message = "The GSTIN's first two digits must equal billing_supplier_state_code."
  }
}

variable "billing_supplier_state_code" {
  description = "Supplier GST state code (SOS_BILLING_SUPPLIER_STATE_CODE); 37 = Andhra Pradesh."
  type        = string
  default     = "37"

  validation {
    condition     = can(regex("^[0-9]{2}$", var.billing_supplier_state_code))
    error_message = "billing_supplier_state_code is two digits, e.g. 37."
  }
}

variable "enable_execute_command" {
  description = "ECS Exec on services (staging only)."
  type        = bool
  default     = false
}

# --- Edge -------------------------------------------------------------------------------

variable "alb_deletion_protection" {
  description = "ALB deletion protection."
  type        = bool
  default     = true
}

variable "waf_rate_limit_per_5min" {
  description = "WAF per-IP rate limit."
  type        = number
  default     = 3000
}

variable "expose_fleet_heartbeat" {
  description = "Route POST /api/v1/fleet/heartbeat from dedicated hosts to the API."
  type        = bool
  default     = true
}

# --- Operations -------------------------------------------------------------------------

variable "alarm_emails" {
  description = "Emails that receive alarms."
  type        = list(string)
  default     = []
}

variable "monthly_budget_usd" {
  description = "AWS Budgets monthly limit (null disables)."
  type        = number
  default     = null
}

# --- CI ---------------------------------------------------------------------------------

variable "github_owner" {
  description = "GitHub owner."
  type        = string
  default     = "bb3agency"
}

variable "github_repository" {
  description = "GitHub repository."
  type        = string
  default     = "school-os"
}

variable "github_deploy_environment" {
  description = "GitHub Environment allowed to deploy (staging | production)."
  type        = string
}

variable "github_allow_main_branch" {
  description = "Allow main-branch jobs without an environment to deploy (staging only)."
  type        = bool
  default     = false
}

variable "create_github_oidc_provider" {
  description = "Create the account's GitHub OIDC provider."
  type        = bool
  default     = true
}

variable "existing_github_oidc_provider_arn" {
  description = "Existing provider ARN when not creating one."
  type        = string
  default     = null
}

variable "create_plan_role" {
  description = "Create the PR plan role."
  type        = bool
  default     = true
}

variable "plan_can_read_secrets" {
  description = "PR plan role may refresh secret versions (staging only)."
  type        = bool
  default     = false
}

variable "create_apply_role" {
  description = "Create the environment-gated terraform apply role."
  type        = bool
  default     = false
}

variable "github_apply_environment" {
  description = "GitHub Environment for the apply role."
  type        = string
  default     = "infra"
}

variable "state_bucket_arn" {
  description = "Terraform state bucket ARN (from the bootstrap stack)."
  type        = string
  default     = null
}

variable "state_kms_key_arn" {
  description = "Terraform state CMK ARN (from the bootstrap stack)."
  type        = string
  default     = null
}

variable "tags" {
  description = "Extra tags."
  type        = map(string)
  default     = {}
}
