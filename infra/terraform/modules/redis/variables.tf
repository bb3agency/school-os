variable "name" {
  description = "Replication group ID, e.g. sos-prod-valkey."
  type        = string
}

variable "vpc_id" {
  description = "VPC ID."
  type        = string
}

variable "subnet_ids" {
  description = "Data-tier subnets."
  type        = list(string)
}

variable "allowed_security_groups" {
  description = "Security groups allowed on 6379, keyed by static names."
  type        = map(string)
  default     = {}
}

variable "engine_version" {
  description = "Valkey engine version (8.x)."
  type        = string
  default     = "8.1"

  validation {
    condition     = can(regex("^8\\.", var.engine_version))
    error_message = "SchoolOS uses Valkey 8.x."
  }
}

variable "node_type" {
  description = "Cache node type."
  type        = string
  default     = "cache.t4g.micro"
}

variable "num_cache_clusters" {
  description = "Nodes in the replication group (1 = no replica; 2+ enables automatic failover)."
  type        = number
  default     = 1
}

variable "kms_key_arn" {
  description = "CMK for at-rest encryption and the connection secrets."
  type        = string
}

variable "log_kms_key_arn" {
  description = "CMK for the slow-log group. Its key policy must allow CloudWatch Logs (the kms module's allow_cloudwatch_logs); null uses kms_key_arn."
  type        = string
  default     = null
}

variable "auth_token_version" {
  description = "Bump to rotate every Valkey user password (new passwords are generated and written to ElastiCache and Secrets Manager; they never enter state)."
  type        = number
  default     = 1
}

variable "snapshot_retention_days" {
  description = "Daily snapshot retention (broker state is transient; small value is fine)."
  type        = number
  default     = 3
}

variable "log_retention_days" {
  description = "Slow-log retention."
  type        = number
  default     = 400
}

variable "secret_name" {
  description = "Secrets Manager name prefix for the per-user connection secrets (<name>/<user>, JSON: host, port, username, password, url)."
  type        = string
}

variable "tags" {
  description = "Extra tags."
  type        = map(string)
  default     = {}
}
