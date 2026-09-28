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
  description = "TLS policy (TLS 1.2+ with 1.3)."
  type        = string
  default     = "ELBSecurityPolicy-TLS13-1-2-2021-06"
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
