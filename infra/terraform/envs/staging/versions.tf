terraform {
  required_version = ">= 1.11.0, < 2.0.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "6.66.0"
    }
  }

  # State: S3 bucket from infra/terraform/bootstrap, native S3 locking (no DynamoDB).
  # terraform init -backend-config=backend.hcl   (copy backend.hcl.example)
  backend "s3" {
    key          = "schoolos/staging/terraform.tfstate"
    region       = "ap-south-1"
    encrypt      = true
    use_lockfile = true
  }
}
