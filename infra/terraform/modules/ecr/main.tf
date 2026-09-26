# ECR repositories for SchoolOS images (docs/10 §6): immutable tags, scan on push, KMS encryption.

variable "name_prefix" {
  description = "Repository prefix, e.g. schoolos (repos become schoolos/api, schoolos/worker, schoolos/web)."
  type        = string
  default     = "schoolos"
}

variable "repositories" {
  description = "Repository short names."
  type        = list(string)
  default     = ["api", "worker", "web"]
}

variable "kms_key_arn" {
  description = "CMK for image encryption."
  type        = string
}

variable "keep_images" {
  description = "Number of images kept per repository (older ones expire)."
  type        = number
  default     = 200
}

variable "pull_principal_arns" {
  description = "Extra IAM principals (e.g. dedicated-host instance roles in another account) allowed to pull."
  type        = list(string)
  default     = []
}

variable "tags" {
  description = "Extra tags."
  type        = map(string)
  default     = {}
}

resource "aws_ecr_repository" "this" {
  for_each = toset(var.repositories)

  name                 = "${var.name_prefix}/${each.key}"
  image_tag_mutability = "IMMUTABLE"
  force_delete         = false

  image_scanning_configuration {
    scan_on_push = true
  }

  encryption_configuration {
    encryption_type = "KMS"
    kms_key         = var.kms_key_arn
  }

  tags = var.tags
}

resource "aws_ecr_lifecycle_policy" "this" {
  for_each   = aws_ecr_repository.this
  repository = each.value.name

  policy = jsonencode({
    rules = [
      {
        rulePriority = 1
        description  = "Expire untagged images after 7 days"
        selection = {
          tagStatus   = "untagged"
          countType   = "sinceImagePushed"
          countUnit   = "days"
          countNumber = 7
        }
        action = { type = "expire" }
      },
      {
        rulePriority = 2
        description  = "Keep the most recent ${var.keep_images} images"
        selection = {
          tagStatus   = "any"
          countType   = "imageCountMoreThan"
          countNumber = var.keep_images
        }
        action = { type = "expire" }
      },
    ]
  })
}

data "aws_iam_policy_document" "pull" {
  count = length(var.pull_principal_arns) > 0 ? 1 : 0

  statement {
    sid     = "CrossAccountPull"
    actions = ["ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer", "ecr:BatchCheckLayerAvailability"]
    principals {
      type        = "AWS"
      identifiers = var.pull_principal_arns
    }
  }
}

resource "aws_ecr_repository_policy" "pull" {
  for_each = length(var.pull_principal_arns) > 0 ? aws_ecr_repository.this : {}

  repository = each.value.name
  policy     = data.aws_iam_policy_document.pull[0].json
}

output "repository_urls" {
  description = "Map of short name to repository URL."
  value       = { for k, v in aws_ecr_repository.this : k => v.repository_url }
}

output "repository_arns" {
  description = "Map of short name to repository ARN."
  value       = { for k, v in aws_ecr_repository.this : k => v.arn }
}

output "posture" {
  description = "Security posture summary (asserted by tests)."
  value = {
    for k, v in aws_ecr_repository.this : k => {
      immutable    = v.image_tag_mutability == "IMMUTABLE"
      scan_on_push = one(v.image_scanning_configuration).scan_on_push
      encryption   = one(v.encryption_configuration).encryption_type
    }
  }
}
