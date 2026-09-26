variable "name" {
  description = "Service/task family name, e.g. sos-prod-api."
  type        = string
}

variable "create_service" {
  description = "false = task definition only (one-off tasks such as migrations and db bootstrap, started with ecs run-task)."
  type        = bool
  default     = true
}

variable "cluster_arn" {
  description = "ECS cluster ARN."
  type        = string
}

variable "cluster_name" {
  description = "ECS cluster name (autoscaling resource ID)."
  type        = string
}

variable "image" {
  description = "Full image reference (<repo>:<version>). Tags are immutable in ECR."
  type        = string
}

variable "cpu" {
  description = "Task CPU units."
  type        = number
  default     = 512
}

variable "memory" {
  description = "Task memory (MiB)."
  type        = number
  default     = 1024
}

variable "cpu_architecture" {
  description = "ARM64 (Graviton, preferred) or X86_64."
  type        = string
  default     = "ARM64"

  validation {
    condition     = contains(["ARM64", "X86_64"], var.cpu_architecture)
    error_message = "cpu_architecture must be ARM64 or X86_64."
  }
}

variable "ephemeral_storage_gib" {
  description = "Fargate ephemeral storage (21-200 GiB). Null = platform default (20 GiB)."
  type        = number
  default     = null
}

variable "container_name" {
  description = "Main container name."
  type        = string
  default     = "app"
}

variable "container_port" {
  description = "Container port (null for workers)."
  type        = number
  default     = null
}

variable "port_name" {
  description = "Port mapping name (Service Connect)."
  type        = string
  default     = "http"
}

variable "command" {
  description = "Container command override."
  type        = list(string)
  default     = null
}

variable "entry_point" {
  description = "Container entrypoint override."
  type        = list(string)
  default     = null
}

variable "environment" {
  description = "Plain (non-secret) environment variables."
  type        = map(string)
  default     = {}
}

variable "secrets" {
  description = "Environment variables injected from Secrets Manager: NAME => valueFrom (secret ARN, optionally with :jsonKey::)."
  type        = map(string)
  default     = {}
}

variable "secret_arns" {
  description = "Base secret ARNs the execution role may read (must cover every secrets valueFrom)."
  type        = list(string)
  default     = []
}

variable "secrets_kms_key_arns" {
  description = "CMKs that encrypt the injected secrets (execution role gets kms:Decrypt)."
  type        = list(string)
  default     = []
}

variable "user" {
  description = "Container user (non-root). Images must own their writable paths for this UID."
  type        = string
  default     = "10001:10001"

  validation {
    condition     = !can(regex("^(0|root)(:|$)", var.user))
    error_message = "Containers must not run as root."
  }
}

variable "writable_paths" {
  description = "Writable scratch paths on a read-only root filesystem. Fargate has no tmpfs, so each is a task-scoped ephemeral volume (wiped when the task stops)."
  type        = list(string)
  default     = ["/tmp"]
}

variable "health_check" {
  description = "Container health check."
  type = object({
    command      = list(string)
    interval     = optional(number, 30)
    timeout      = optional(number, 5)
    retries      = optional(number, 3)
    start_period = optional(number, 30)
  })
  default = null
}

variable "stop_timeout" {
  description = "Seconds between SIGTERM and SIGKILL (Celery warm shutdown needs time)."
  type        = number
  default     = 30
}

variable "log_retention_days" {
  description = "Application log retention (400 days, docs/10 §5)."
  type        = number
  default     = 400
}

variable "log_kms_key_arn" {
  description = "CMK for the log group."
  type        = string
}

variable "attach_task_role_policy" {
  description = "Attach task_role_policy_json to the task role (bool known at plan time)."
  type        = bool
  default     = false
}

variable "task_role_policy_json" {
  description = "Inline IAM policy for the task role (AWS access of the application itself)."
  type        = string
  default     = null
}

variable "vpc_id" {
  description = "VPC ID."
  type        = string
}

variable "vpc_cidr" {
  description = "VPC CIDR (egress to data tier / internal services)."
  type        = string
}

variable "subnet_ids" {
  description = "Subnets for tasks (private app subnets)."
  type        = list(string)
}

variable "assign_public_ip" {
  description = "Assign public IPs (only for the no-NAT pilot option; SGs must then allow only ALB inbound)."
  type        = bool
  default     = false
}

variable "ingress_from_security_groups" {
  description = "Security groups allowed to reach container_port, keyed by static names (e.g. alb, web)."
  type        = map(string)
  default     = {}
}

variable "egress_https_anywhere" {
  description = "Allow outbound 443 to the internet (AWS APIs without endpoints, LLM/embeddings/OCR/IdP APIs)."
  type        = bool
  default     = true
}

variable "egress_vpc_ports" {
  description = "TCP ports allowed outbound to the VPC CIDR (e.g. 5432, 6379, 8000)."
  type        = list(number)
  default     = []
}

variable "desired_count" {
  description = "Initial task count. Afterwards Terraform ignores drift (autoscaling or `aws ecs update-service` own the running count)."
  type        = number
  default     = 1
}

variable "load_balancer_target_group_arn" {
  description = "ALB target group for container_port (null = not behind the ALB)."
  type        = string
  default     = null
}

variable "attach_load_balancer" {
  description = "Whether to register with load_balancer_target_group_arn (bool known at plan time)."
  type        = bool
  default     = false
}

variable "service_connect" {
  description = "Service Connect. server=true publishes container_port as discovery_name:client_alias_port; false = client only."
  type = object({
    namespace_arn     = string
    server            = optional(bool, false)
    discovery_name    = optional(string)
    client_alias_port = optional(number)
  })
  default = null
}

variable "autoscaling" {
  description = "Target-tracking CPU autoscaling (null disables)."
  type = object({
    min_capacity = number
    max_capacity = number
    cpu_target   = optional(number, 60)
  })
  default = null
}

variable "enable_execute_command" {
  description = "ECS Exec (audited). Keep false in prod except during an approved break-glass."
  type        = bool
  default     = false
}

variable "tags" {
  description = "Extra tags."
  type        = map(string)
  default     = {}
}
