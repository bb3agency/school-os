variable "name_prefix" {
  description = "Name prefix, e.g. sos-prod."
  type        = string
}

variable "environment" {
  description = "Environment name used as the Environment dimension of custom metrics (staging|prod)."
  type        = string
}

variable "kms_key_arn" {
  description = "CMK for the SNS topic and log groups (key policy must allow cloudwatch.amazonaws.com)."
  type        = string
}

variable "alarm_emails" {
  description = "Email addresses subscribed to alarms (each must confirm the subscription)."
  type        = list(string)
  default     = []
}

variable "log_retention_days" {
  description = "Retention for SchoolOS log groups (400 days)."
  type        = number
  default     = 400
}

variable "alb_arn_suffix" {
  description = "ALB ARN suffix (null disables ALB alarms)."
  type        = string
  default     = null
}

variable "enable_alb_alarms" {
  description = "Create ALB alarms (bool known at plan time)."
  type        = bool
  default     = true
}

variable "target_group_arn_suffix" {
  description = "Web target group ARN suffix."
  type        = string
  default     = null
}

variable "rds_instance_id" {
  description = "RDS instance identifier (null disables RDS alarms)."
  type        = string
  default     = null
}

variable "enable_rds_alarms" {
  description = "Create RDS alarms."
  type        = bool
  default     = true
}

variable "rds_allocated_storage_gb" {
  description = "Allocated storage used to derive the free-storage threshold."
  type        = number
  default     = 50
}

variable "redis_replication_group_id" {
  description = "ElastiCache replication group ID (null disables cache alarms)."
  type        = string
  default     = null
}

variable "enable_redis_alarms" {
  description = "Create cache alarms."
  type        = bool
  default     = true
}

variable "http_5xx_threshold" {
  description = "5xx responses (ELB + target) per 5 minutes that page."
  type        = number
  default     = 10
}

variable "latency_p95_seconds" {
  description = "Target response time p95 threshold (seconds)."
  type        = number
  default     = 2
}

variable "rds_cpu_percent" {
  description = "RDS CPU threshold."
  type        = number
  default     = 80
}

variable "queue_age_seconds" {
  description = "Oldest queued Celery task age threshold."
  type        = number
  default     = 900
}

variable "custom_metric_namespace" {
  description = "Namespace for application metrics (published by the app via EMF)."
  type        = string
  default     = "SchoolOS"
}

variable "security_metric_namespace" {
  description = "Namespace of the metrics derived from security log events. It must differ from custom_metric_namespace: the api and worker roles may PutMetricData there, and must not be able to silence a security alarm."
  type        = string
  default     = "SchoolOS/Security"

  validation {
    condition     = var.security_metric_namespace != var.custom_metric_namespace
    error_message = "security_metric_namespace must differ from custom_metric_namespace."
  }
}

variable "api_log_group_name" {
  description = "CloudWatch log group of the api service (structured JSON lines, app/core/logging.py)."
  type        = string
}

variable "worker_log_group_name" {
  description = "CloudWatch log group of the Celery worker that runs audit.verify_all_chains."
  type        = string
}

variable "web_log_group_name" {
  description = "CloudWatch log group of the web/BFF service (src/server/log.ts JSON lines)."
  type        = string
}

variable "api_auth_failures_per_5min" {
  description = "401 answers from the API in 5 minutes that alarm (token replay, stolen service token, credential stuffing behind the BFF)."
  type        = number
  default     = 50
}

variable "rate_limited_per_5min" {
  description = "Requests refused by API or BFF rate limits in 5 minutes that alarm (P2-07)."
  type        = number
  default     = 200
}

variable "auth_failures_per_5min" {
  description = "Rejected tokens or refused sign-ins counted by the API backoff in 5 minutes that alarm (ASVS 2.2.1)."
  type        = number
  default     = 30
}

variable "sign_in_failures_per_5min" {
  description = "Refused sign-in or step-up callbacks in the BFF in 5 minutes that alarm (P2-07)."
  type        = number
  default     = 30
}

variable "heartbeat_rejections_per_15min" {
  description = "Rejected fleet heartbeats (bad signature, replay, schema) in 15 minutes that alarm."
  type        = number
  default     = 5
}

variable "monthly_budget_usd" {
  description = "AWS Budgets monthly limit for this account (null disables). Alerts at 50/80/100 %."
  type        = number
  default     = null
}

variable "tags" {
  description = "Extra tags."
  type        = map(string)
  default     = {}
}
