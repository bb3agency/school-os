# Bootstrap: creates the Terraform remote-state bucket (+ CMK) for one AWS account. Run once per account
# (staging, prod) with local state, by a human using IAM Identity Center admin credentials:
#   terraform init && terraform apply -var-file=<account>.tfvars
# Then keep terraform.tfstate of this stack safe (or `terraform init -migrate-state` into the new bucket
# with key "schoolos/bootstrap/terraform.tfstate"). Env stacks lock state natively in S3 (use_lockfile).

terraform {
  required_version = ">= 1.11.0, < 2.0.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "6.66.0"
    }
  }
}

variable "aws_region" {
  description = "State region (guarded)."
  type        = string
  default     = "ap-south-1"

  validation {
    condition     = var.aws_region == "ap-south-1"
    error_message = "State lives in ap-south-1."
  }
}

variable "aws_account_id" {
  description = "Account to bootstrap."
  type        = string

  validation {
    condition     = can(regex("^[0-9]{12}$", var.aws_account_id))
    error_message = "aws_account_id must be 12 digits."
  }
}

variable "account_label" {
  description = "staging | prod (tag + alias)."
  type        = string

  validation {
    condition     = contains(["staging", "prod"], var.account_label)
    error_message = "account_label must be staging or prod."
  }
}

variable "owner" {
  description = "Mandatory tag: owner."
  type        = string
}

variable "cost_center" {
  description = "Mandatory tag: cost centre."
  type        = string
}

provider "aws" {
  region              = var.aws_region
  allowed_account_ids = [var.aws_account_id]

  default_tags {
    tags = {
      project     = "schoolos"
      env         = var.account_label
      owner       = var.owner
      data_class  = "C1-internal"
      cost_center = var.cost_center
    }
  }
}

module "kms" {
  source = "../modules/kms"

  name_prefix = "sos-${var.account_label}-tfstate"
  keys = {
    state = { description = "SchoolOS ${var.account_label}: Terraform state" }
  }
}

module "state_bucket" {
  source = "../modules/s3_bucket"

  name        = "sos-tfstate-${var.aws_account_id}"
  kms_key_arn = module.kms.key_arns["state"]
  lifecycle_rules = [
    {
      id                                 = "noncurrent"
      noncurrent_version_expiration_days = 365
      abort_incomplete_multipart_days    = 7
    },
  ]
}

output "state_bucket" {
  description = "backend.hcl: bucket."
  value       = module.state_bucket.id
}

output "state_bucket_arn" {
  description = "Env tfvars: state_bucket_arn."
  value       = module.state_bucket.arn
}

output "state_kms_key_arn" {
  description = "backend.hcl: kms_key_id; env tfvars: state_kms_key_arn."
  value       = module.kms.key_arns["state"]
}

output "posture" {
  description = "State bucket posture (asserted by tests)."
  value = {
    sse                 = module.state_bucket.sse_algorithm
    versioning          = module.state_bucket.versioning_status
    public_access_block = module.state_bucket.public_access_block
  }
}
