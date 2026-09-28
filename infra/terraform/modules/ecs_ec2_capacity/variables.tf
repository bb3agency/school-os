variable "name" {
  description = "Name prefix for the capacity provider, Auto Scaling group, launch template and roles, e.g. sos-prod-pdf. Must not start with aws, ecs or fargate."
  type        = string

  validation {
    condition     = can(regex("^[a-z0-9][a-z0-9-]{2,40}$", var.name)) && !can(regex("^(aws|ecs|fargate)", var.name))
    error_message = "name: 3-41 lower-case letters, digits or dashes, not starting with aws, ecs or fargate."
  }
}

variable "cluster_name" {
  description = "ECS cluster the instances join (ECS_CLUSTER)."
  type        = string
}

variable "cluster_arn" {
  description = "ECS cluster ARN (scopes the instance role's ECS agent permissions)."
  type        = string
}

variable "vpc_id" {
  description = "VPC ID."
  type        = string
}

variable "subnet_ids" {
  description = "Subnets for the instances (private app subnets; tasks use awsvpc ENIs in the subnets their service names)."
  type        = list(string)
}

variable "associate_public_ip_address" {
  description = "Public IPs on the instances (only for the no-NAT pilot option; awsvpc tasks on EC2 never get one)."
  type        = bool
  default     = false
}

variable "instance_type" {
  description = "Instance type. Graviton (t4g/c7g/m7g...) selects the arm64 ECS-optimized AMI, anything else x86_64. Must match the task cpu_architecture and the image: CI publishes linux/amd64 images only, hence t3.medium (ADR-0025 amendment)."
  type        = string
  default     = "t3.medium"
}

variable "min_size" {
  description = "Minimum instances (1 keeps PDF exports warm; 0 lets ECS scale in to nothing, with a cold start of a few minutes)."
  type        = number
  default     = 1
}

variable "max_size" {
  description = "Maximum instances (room for rolling deployments and bursts)."
  type        = number
  default     = 2

  validation {
    condition     = var.max_size >= 1
    error_message = "max_size must be at least 1."
  }
}

variable "root_volume_gb" {
  description = "Root volume (ECS-optimized AL2023 needs >= 30 GiB; images and container layers only, no school data)."
  type        = number
  default     = 30

  validation {
    condition     = var.root_volume_gb >= 30
    error_message = "root_volume_gb must be at least 30 (the ECS-optimized AMI snapshot size)."
  }
}

variable "ebs_kms_key_arn" {
  description = "CMK that encrypts the root volumes (SEC-011). The module grants it to its own Auto Scaling service-linked role."
  type        = string
}

variable "instance_attribute" {
  description = "ECS container-instance attribute set on every instance (services pin placement to it with memberOf)."
  type = object({
    name  = string
    value = string
  })
  default = {
    name  = "schoolos.seccomp"
    value = "chromium-sandbox"
  }
}

variable "max_user_namespaces" {
  description = "user.max_user_namespaces on the instances (Chromium's sandbox creates a few per render)."
  type        = number
  default     = 15000

  validation {
    condition     = var.max_user_namespaces > 0
    error_message = "The Chromium sandbox needs user namespaces (max_user_namespaces > 0)."
  }
}

variable "target_capacity" {
  description = "ECS managed scaling target (percent of instance capacity in use)."
  type        = number
  default     = 100
}

variable "tags" {
  description = "Extra tags."
  type        = map(string)
  default     = {}
}
