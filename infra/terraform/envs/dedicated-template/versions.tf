terraform {
  required_version = ">= 1.11.0, < 2.0.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "6.66.0"
    }
  }

  # One state file per school. The key comes from the per-school backend config:
  #   terraform init -reconfigure -backend-config=schools/<school_code>.backend.hcl
  # (see backend.hcl.example: key = "schoolos/dedicated/<school_code>/terraform.tfstate").
  backend "s3" {
    region       = "ap-south-1"
    encrypt      = true
    use_lockfile = true
  }
}
