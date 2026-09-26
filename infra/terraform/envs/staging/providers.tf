provider "aws" {
  region              = var.aws_region
  allowed_account_ids = [var.aws_account_id]

  default_tags {
    tags = local.mandatory_tags
  }
}

# Backups / DR copies only (NFR-PRV-001: data stays in India).
provider "aws" {
  alias               = "dr"
  region              = var.dr_region
  allowed_account_ids = [var.aws_account_id]

  default_tags {
    tags = local.mandatory_tags
  }
}

locals {
  mandatory_tags = {
    project     = "schoolos"
    env         = "staging"
    owner       = var.owner
    data_class  = var.data_class
    cost_center = var.cost_center
  }
}
