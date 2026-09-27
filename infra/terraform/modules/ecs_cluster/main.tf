# ECS cluster (Fargate, plus optional EC2 capacity providers) with Container Insights, audited ECS Exec and a Service Connect namespace
# so the web BFF reaches the API at http://api:8000 on the private network only (TB2).

variable "name" {
  description = "Cluster name, e.g. sos-prod."
  type        = string
}

variable "kms_key_arn" {
  description = "CMK for ECS Exec session encryption and its log group."
  type        = string
}

variable "log_retention_days" {
  description = "Retention for the ECS Exec audit log group."
  type        = number
  default     = 400
}

variable "fargate_spot_weight" {
  description = "Default capacity provider weight for FARGATE_SPOT (0 = on-demand only)."
  type        = number
  default     = 0
}

variable "ec2_capacity_providers" {
  description = "EC2 capacity providers to associate with the cluster (e.g. the pdf capacity, ADR-0025). Services opt in by name; the default strategy stays Fargate."
  type        = list(string)
  default     = []
}

variable "tags" {
  description = "Extra tags."
  type        = map(string)
  default     = {}
}

resource "aws_cloudwatch_log_group" "exec" {
  name              = "/schoolos/${var.name}/ecs-exec"
  retention_in_days = var.log_retention_days
  kms_key_id        = var.kms_key_arn
  tags              = var.tags
}

resource "aws_service_discovery_http_namespace" "this" {
  name        = "${var.name}.internal"
  description = "Service Connect namespace for ${var.name}"
  tags        = var.tags
}

resource "aws_ecs_cluster" "this" {
  name = var.name

  setting {
    name  = "containerInsights"
    value = "enabled"
  }

  configuration {
    execute_command_configuration {
      kms_key_id = var.kms_key_arn
      logging    = "OVERRIDE"
      log_configuration {
        cloud_watch_encryption_enabled = true
        cloud_watch_log_group_name     = aws_cloudwatch_log_group.exec.name
      }
    }
  }

  service_connect_defaults {
    namespace = aws_service_discovery_http_namespace.this.arn
  }

  tags = var.tags
}

resource "aws_ecs_cluster_capacity_providers" "this" {
  cluster_name       = aws_ecs_cluster.this.name
  capacity_providers = concat(["FARGATE", "FARGATE_SPOT"], var.ec2_capacity_providers)

  default_capacity_provider_strategy {
    capacity_provider = "FARGATE"
    weight            = 1
    base              = 1
  }

  dynamic "default_capacity_provider_strategy" {
    for_each = var.fargate_spot_weight > 0 ? [1] : []
    content {
      capacity_provider = "FARGATE_SPOT"
      weight            = var.fargate_spot_weight
    }
  }
}

output "arn" {
  description = "Cluster ARN."
  value       = aws_ecs_cluster.this.arn
}

output "name" {
  description = "Cluster name."
  value       = aws_ecs_cluster.this.name
}

output "ec2_capacity_providers" {
  description = "EC2 capacity providers associated with the cluster (reference these, not the provider module, so services wait for the association)."
  value       = [for cp in aws_ecs_cluster_capacity_providers.this.capacity_providers : cp if !contains(["FARGATE", "FARGATE_SPOT"], cp)]
}

output "service_connect_namespace_arn" {
  description = "Service Connect namespace ARN."
  value       = aws_service_discovery_http_namespace.this.arn
}
