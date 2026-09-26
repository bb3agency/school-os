terraform {
  # Write-only attributes and ephemeral resources (used across SchoolOS modules) need >= 1.11.
  required_version = ">= 1.11.0, < 2.0.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "6.66.0"
    }
  }
}
