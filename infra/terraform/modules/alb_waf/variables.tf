variable "name" {
  description = "Name prefix, e.g. sos-prod."
  type        = string
}

variable "vpc_id" {
  description = "VPC ID."
  type        = string
}

variable "vpc_cidr" {
  description = "VPC CIDR (ALB egress is limited to it)."
  type        = string
}

variable "public_subnet_ids" {
  description = "Public subnets for the ALB."
  type        = list(string)
}

variable "domain_names" {
  description = "Hostnames served (first = primary), e.g. [\"app.schoolos.in\", \"admin.schoolos.in\"]."
  type        = list(string)
}

variable "route53_zone_id" {
  description = "Hosted zone for ACM validation + alias records (null = add the output DNS records at your DNS provider)."
  type        = string
  default     = null
}

variable "web_port" {
  description = "Web (Next.js) container port."
  type        = number
  default     = 3000
}

variable "web_health_check_path" {
  description = "Web health endpoint."
  type        = string
  default     = "/healthz"
}

variable "expose_fleet_heartbeat" {
  description = "Route POST /api/v1/fleet/heartbeat to the API (dedicated hosts call the control plane). Shared tier only."
  type        = bool
  default     = false
}

variable "api_port" {
  description = "API container port."
  type        = number
  default     = 8000
}

variable "api_health_check_path" {
  description = "API health endpoint."
  type        = string
  default     = "/healthz"
}

variable "idle_timeout" {
  description = "ALB idle timeout (Ask streams responses)."
  type        = number
  default     = 120
}

variable "deletion_protection" {
  description = "ALB deletion protection."
  type        = bool
  default     = true
}

variable "access_logs_bucket" {
  description = "S3 bucket for ALB access logs (SSE-S3; prefix alb/)."
  type        = string
}

variable "ssl_policy" {
  description = "TLS policy: TLS 1.3 plus TLS 1.2 with AEAD forward-secret suites only (the -Res- variant: no CBC), or TLS 1.3 only."
  type        = string
  default     = "ELBSecurityPolicy-TLS13-1-2-Res-2021-06"

  validation {
    condition     = can(regex("^ELBSecurityPolicy-TLS13-1-(2-Res-|3-)", var.ssl_policy))
    error_message = "ssl_policy must be ELBSecurityPolicy-TLS13-1-2-Res-* (TLS 1.2 without CBC suites, 1.3 enabled) or ELBSecurityPolicy-TLS13-1-3-* (TLS 1.3 only)."
  }
}

variable "waf_rate_limit_per_5min" {
  description = "Requests per 5 minutes per client IP before blocking. Schools share one office IP, so keep this generous."
  type        = number
  default     = 3000
}

variable "waf_auth_rate_limit_per_5min" {
  description = "Stricter per-IP limit for authentication paths (/bff/auth/)."
  type        = number
  default     = 300
}

variable "waf_auth_path_prefix" {
  description = "URI prefix of the BFF authentication routes that get the stricter rate limit."
  type        = string
  default     = "/bff/auth/"
}

variable "waf_machine_rate_limit_per_5min" {
  description = "Per-IP limit for the machine paths (fleet heartbeat, Tally edge agent) in one evaluation window (P2-07)."
  type        = number
  default     = 300
}

variable "waf_machine_path_prefixes" {
  description = "URI prefixes of the machine paths that get the machine rate limit (at least two: WAF or_statement)."
  type        = list(string)
  default     = ["/api/v1/fleet/", "/api/v1/edge/"]

  validation {
    condition     = length(var.waf_machine_path_prefixes) >= 2
    error_message = "List at least two prefixes (the scope-down is an OR statement)."
  }
}

# Public surface rate rules (SEC-036). Each limit must stay at or below waf_rate_limit_per_5min:
# rate-limit-ip counts the same requests and would block first, making a higher limit dead config.
# Cross-variable validation needs Terraform >= 1.9 (versions.tf requires >= 1.11).

variable "waf_public_link_rate_limit_per_5min" {
  description = "Per-IP limit for the parent link pages (waf_public_link_path_prefix) in one evaluation window (SEC-036). A school's parents mostly use mobile data, so one address rarely carries many of them."
  type        = number
  default     = 300

  validation {
    condition     = var.waf_public_link_rate_limit_per_5min >= 10 && var.waf_public_link_rate_limit_per_5min <= var.waf_rate_limit_per_5min
    error_message = "waf_public_link_rate_limit_per_5min must be at least 10 (WAF minimum) and at most waf_rate_limit_per_5min."
  }
}

variable "waf_public_link_path_prefix" {
  description = "URI prefix of the parent link pages (notice, receipt, dues, opt-in, and their /p/pay and /p/optin posts)."
  type        = string
  default     = "/p/"

  validation {
    condition     = startswith(var.waf_public_link_path_prefix, "/") && endswith(var.waf_public_link_path_prefix, "/") && lower(var.waf_public_link_path_prefix) == var.waf_public_link_path_prefix
    error_message = "waf_public_link_path_prefix must start and end with / and be lower case (the rule lowercases the path)."
  }
}

variable "waf_verify_rate_limit_per_5min" {
  description = "Per-IP limit for the certificate verification pages (waf_verify_path_prefix) in one evaluation window (SEC-036)."
  type        = number
  default     = 300

  validation {
    condition     = var.waf_verify_rate_limit_per_5min >= 10 && var.waf_verify_rate_limit_per_5min <= var.waf_rate_limit_per_5min
    error_message = "waf_verify_rate_limit_per_5min must be at least 10 (WAF minimum) and at most waf_rate_limit_per_5min."
  }
}

variable "waf_verify_path_prefix" {
  description = "URI prefix of the certificate verification pages (/v/check and /v/{code}, served on the verify host)."
  type        = string
  default     = "/v/"

  validation {
    condition     = startswith(var.waf_verify_path_prefix, "/") && endswith(var.waf_verify_path_prefix, "/") && lower(var.waf_verify_path_prefix) == var.waf_verify_path_prefix
    error_message = "waf_verify_path_prefix must start and end with / and be lower case (the rule lowercases the path)."
  }
}

variable "waf_disclosure_rate_limit_per_5min" {
  description = "Per-IP limit for the CBSE disclosure pages (waf_disclosure_path_prefix) in one evaluation window (SEC-036, NFR-SEC-008)."
  type        = number
  default     = 300

  validation {
    condition     = var.waf_disclosure_rate_limit_per_5min >= 10 && var.waf_disclosure_rate_limit_per_5min <= var.waf_rate_limit_per_5min
    error_message = "waf_disclosure_rate_limit_per_5min must be at least 10 (WAF minimum) and at most waf_rate_limit_per_5min."
  }
}

variable "waf_disclosure_path_prefix" {
  description = "URI prefix of the public CBSE disclosure pages (/d/{key}, ADR-0051), served on the parent origin."
  type        = string
  default     = "/d/"

  validation {
    condition     = startswith(var.waf_disclosure_path_prefix, "/") && endswith(var.waf_disclosure_path_prefix, "/") && lower(var.waf_disclosure_path_prefix) == var.waf_disclosure_path_prefix
    error_message = "waf_disclosure_path_prefix must start and end with / and be lower case (the rule lowercases the path)."
  }
}

variable "waf_webhook_rate_limit_per_5min" {
  description = "Per-IP limit for provider webhooks (waf_webhook_path_prefix) in one evaluation window (SEC-036). Razorpay and Meta deliver every school's events from shared addresses, so keep it high; both providers retry after a 429."
  type        = number
  default     = 2000

  validation {
    condition     = var.waf_webhook_rate_limit_per_5min >= 10 && var.waf_webhook_rate_limit_per_5min <= var.waf_rate_limit_per_5min
    error_message = "waf_webhook_rate_limit_per_5min must be at least 10 (WAF minimum) and at most waf_rate_limit_per_5min."
  }
}

variable "waf_webhook_path_prefix" {
  description = "URI prefix of the provider webhooks (Razorpay and Meta)."
  type        = string
  default     = "/api/v1/webhooks/"

  validation {
    condition     = startswith(var.waf_webhook_path_prefix, "/") && endswith(var.waf_webhook_path_prefix, "/") && lower(var.waf_webhook_path_prefix) == var.waf_webhook_path_prefix
    error_message = "waf_webhook_path_prefix must start and end with / and be lower case (the rule lowercases the path)."
  }
}

variable "waf_rate_window_sec" {
  description = "Evaluation window of the rate-based rules in seconds (WAF allows 60, 120, 300 or 600); also the Retry-After of their 429."
  type        = number
  default     = 300

  validation {
    condition     = contains([60, 120, 300, 600], var.waf_rate_window_sec)
    error_message = "waf_rate_window_sec must be 60, 120, 300 or 600."
  }
}

variable "waf_log_retention_days" {
  description = "WAF log retention."
  type        = number
  default     = 400
}

variable "log_kms_key_arn" {
  description = "CMK for the WAF log group."
  type        = string
}

variable "tags" {
  description = "Extra tags."
  type        = map(string)
  default     = {}
}
