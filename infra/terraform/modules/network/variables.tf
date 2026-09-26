variable "name" {
  description = "Name prefix, e.g. sos-prod."
  type        = string
}

variable "cidr_block" {
  description = "VPC CIDR (docs/10 §3)."
  type        = string
  default     = "10.20.0.0/16"
}

variable "azs" {
  description = "Availability zones (two, in ap-south-1)."
  type        = list(string)
  default     = ["ap-south-1a", "ap-south-1b"]

  validation {
    condition     = length(var.azs) >= 2
    error_message = "At least two AZs are required."
  }
}

variable "public_subnet_cidrs" {
  description = "Public subnets (ALB, NAT, dedicated hosts)."
  type        = list(string)
  default     = ["10.20.0.0/24", "10.20.1.0/24"]
}

variable "app_subnet_cidrs" {
  description = "Private app subnets (ECS tasks)."
  type        = list(string)
  default     = ["10.20.10.0/24", "10.20.11.0/24"]
}

variable "data_subnet_cidrs" {
  description = "Isolated data subnets (RDS, ElastiCache). No route to the internet."
  type        = list(string)
  default     = ["10.20.20.0/24", "10.20.21.0/24"]
}

variable "nat_mode" {
  description = "none | single (one NAT in the first AZ, Stage 0 cost option) | per_az."
  type        = string
  default     = "single"

  validation {
    condition     = contains(["none", "single", "per_az"], var.nat_mode)
    error_message = "nat_mode must be none, single or per_az."
  }
}

variable "interface_endpoints" {
  description = "Interface VPC endpoint service short names to create in app subnets (add when cost-justified), e.g. [\"ecr.api\", \"ecr.dkr\", \"secretsmanager\", \"kms\", \"logs\", \"sts\"]."
  type        = list(string)
  default     = []
}

variable "flow_logs_enabled" {
  description = "Enable VPC flow logs to CloudWatch Logs."
  type        = bool
  default     = true
}

variable "flow_log_traffic_type" {
  description = "ACCEPT | REJECT | ALL. VPC flow logs cannot be sampled natively; REJECT-only with a 10-minute aggregation window is the 'sampled' cost-control default (07 §13)."
  type        = string
  default     = "REJECT"

  validation {
    condition     = contains(["ACCEPT", "REJECT", "ALL"], var.flow_log_traffic_type)
    error_message = "flow_log_traffic_type must be ACCEPT, REJECT or ALL."
  }
}

variable "flow_log_retention_days" {
  description = "Flow log retention (400 days per docs/10 §5)."
  type        = number
  default     = 400
}

variable "log_kms_key_arn" {
  description = "KMS key for the flow-log log group."
  type        = string
}

variable "tags" {
  description = "Extra tags."
  type        = map(string)
  default     = {}
}
