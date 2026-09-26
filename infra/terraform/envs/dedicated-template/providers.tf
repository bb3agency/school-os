provider "aws" {
  region              = var.aws_region
  allowed_account_ids = [var.aws_account_id]

  default_tags {
    tags = local.mandatory_tags
  }
}

provider "aws" {
  alias               = "backup"
  region              = var.backup_region
  allowed_account_ids = [var.aws_account_id]

  default_tags {
    tags = local.mandatory_tags
  }
}

locals {
  mandatory_tags = {
    project     = "schoolos"
    env         = "prod-dedicated"
    owner       = var.owner
    data_class  = "C3-children-personal"
    cost_center = var.cost_center
    school_code = var.school_code
  }
}
