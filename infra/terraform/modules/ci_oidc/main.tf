# GitHub Actions -> AWS via OIDC (07 §13-14): no long-lived keys. Roles are bound to one repository and
# to a GitHub Environment (manual approval for prod) or, for staging only, the main branch.

data "aws_caller_identity" "current" {}
data "aws_partition" "current" {}

locals {
  repo         = "${var.github_owner}/${var.github_repository}"
  provider_arn = var.create_oidc_provider ? aws_iam_openid_connect_provider.github[0].arn : var.existing_oidc_provider_arn
  deploy_subjects = concat(
    ["repo:${local.repo}:environment:${var.deploy_environment}"],
    var.allow_main_branch ? ["repo:${local.repo}:ref:refs/heads/main"] : [],
  )
}

resource "aws_iam_openid_connect_provider" "github" {
  count = var.create_oidc_provider ? 1 : 0

  url            = "https://token.actions.githubusercontent.com"
  client_id_list = ["sts.amazonaws.com"]
  tags           = var.tags
}

# --- Deploy role ------------------------------------------------------------------------

data "aws_iam_policy_document" "deploy_trust" {
  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]
    principals {
      type        = "Federated"
      identifiers = [local.provider_arn]
    }
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:sub"
      values   = local.deploy_subjects
    }
  }
}

resource "aws_iam_role" "deploy" {
  name                 = "${var.name_prefix}-gha-deploy"
  description          = "GitHub Actions deploy for ${local.repo} (${var.deploy_environment})"
  assume_role_policy   = data.aws_iam_policy_document.deploy_trust.json
  max_session_duration = var.max_session_seconds
  tags                 = var.tags
}

data "aws_iam_policy_document" "deploy" {
  statement {
    sid       = "EcrAuth"
    actions   = ["ecr:GetAuthorizationToken"]
    resources = ["*"]
  }

  statement {
    sid = "EcrPush"
    actions = [
      "ecr:BatchCheckLayerAvailability", "ecr:BatchGetImage", "ecr:CompleteLayerUpload",
      "ecr:DescribeImages", "ecr:DescribeImageScanFindings", "ecr:GetDownloadUrlForLayer",
      "ecr:InitiateLayerUpload", "ecr:PutImage", "ecr:UploadLayerPart",
    ]
    resources = var.ecr_repository_arns
  }

  statement {
    sid       = "EcsTaskDefinitions"
    actions   = ["ecs:RegisterTaskDefinition", "ecs:DescribeTaskDefinition", "ecs:ListTaskDefinitions"]
    resources = ["*"]
  }

  statement {
    sid       = "EcsDeployAndOneOffTasks"
    actions   = ["ecs:UpdateService", "ecs:DescribeServices", "ecs:RunTask", "ecs:DescribeTasks", "ecs:ListTasks", "ecs:StopTask"]
    resources = ["*"]
    condition {
      test     = "ArnEquals"
      variable = "ecs:cluster"
      values   = [var.ecs_cluster_arn]
    }
  }

  statement {
    sid       = "PassTaskRoles"
    actions   = ["iam:PassRole"]
    resources = var.passable_role_arns
    condition {
      test     = "StringEquals"
      variable = "iam:PassedToService"
      values   = ["ecs-tasks.amazonaws.com"]
    }
  }

  statement {
    sid       = "ReadOneOffTaskLogs"
    actions   = ["logs:GetLogEvents", "logs:FilterLogEvents", "logs:DescribeLogStreams"]
    resources = ["arn:${data.aws_partition.current.partition}:logs:*:${data.aws_caller_identity.current.account_id}:log-group:/schoolos/ecs/*"]
  }

  dynamic "statement" {
    for_each = var.enable_artifacts_publish ? [1] : []
    content {
      sid       = "PublishDedicatedBundles"
      actions   = ["s3:PutObject", "s3:GetObject", "s3:ListBucket"]
      resources = [var.artifacts_bucket_arn, "${var.artifacts_bucket_arn}/dedicated/*"]
    }
  }

  dynamic "statement" {
    for_each = var.enable_artifacts_publish ? [1] : []
    content {
      sid       = "ArtifactsKms"
      actions   = ["kms:GenerateDataKey", "kms:Decrypt"]
      resources = [var.artifacts_kms_key_arn]
    }
  }

  dynamic "statement" {
    for_each = length(var.denied_data_bucket_arns) > 0 ? [1] : []
    content {
      sid       = "NeverReadSchoolData"
      effect    = "Deny"
      actions   = ["s3:GetObject", "s3:GetObjectVersion", "s3:PutObject", "s3:DeleteObject"]
      resources = [for b in var.denied_data_bucket_arns : "${b}/*"]
    }
  }
}

resource "aws_iam_role_policy" "deploy" {
  name   = "deploy"
  role   = aws_iam_role.deploy.id
  policy = data.aws_iam_policy_document.deploy.json
}

# --- Plan role (pull requests) ------------------------------------------------------------

data "aws_iam_policy_document" "plan_trust" {
  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]
    principals {
      type        = "Federated"
      identifiers = [local.provider_arn]
    }
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:sub"
      values   = ["repo:${local.repo}:pull_request", "repo:${local.repo}:ref:refs/heads/main"]
    }
  }
}

resource "aws_iam_role" "plan" {
  count = var.create_plan_role ? 1 : 0

  name                 = "${var.name_prefix}-gha-plan"
  description          = "GitHub Actions terraform plan (read-only) for ${local.repo}"
  assume_role_policy   = data.aws_iam_policy_document.plan_trust.json
  max_session_duration = var.max_session_seconds
  tags                 = var.tags
}

resource "aws_iam_role_policy_attachment" "plan_readonly" {
  count = var.create_plan_role ? 1 : 0

  role       = aws_iam_role.plan[0].name
  policy_arn = "arn:${data.aws_partition.current.partition}:iam::aws:policy/ReadOnlyAccess"
}

data "aws_iam_policy_document" "plan" {
  dynamic "statement" {
    for_each = var.state_bucket_arn == null ? [] : [1]
    content {
      sid       = "StateRead"
      actions   = ["s3:GetObject", "s3:ListBucket"]
      resources = [var.state_bucket_arn, "${var.state_bucket_arn}/*"]
    }
  }

  # The plan role trusts every same-repo pull request, so it may write only the S3-native lock
  # files (use_lockfile), never the state itself (SEC-009: a PR cannot rewrite the state that apply trusts).
  dynamic "statement" {
    for_each = var.state_bucket_arn == null ? [] : [1]
    content {
      sid       = "StateLockOnly"
      actions   = ["s3:PutObject", "s3:DeleteObject"]
      resources = ["${var.state_bucket_arn}/*.tflock"]
    }
  }

  dynamic "statement" {
    for_each = var.state_kms_key_arn == null ? [] : [1]
    content {
      sid       = "StateKms"
      actions   = ["kms:Decrypt", "kms:GenerateDataKey"]
      resources = [var.state_kms_key_arn]
    }
  }

  # terraform refresh reads aws_secretsmanager_secret_version via GetSecretValue. Only allowed where
  # the account holds synthetic data (staging); prod plans run under the environment-gated apply role.
  dynamic "statement" {
    for_each = var.plan_can_read_secrets ? [1] : []
    content {
      sid       = "RefreshSecretVersions"
      actions   = ["secretsmanager:GetSecretValue", "kms:Decrypt"]
      resources = compact(["arn:${data.aws_partition.current.partition}:secretsmanager:*:${data.aws_caller_identity.current.account_id}:secret:*", var.secrets_kms_key_arn])
    }
  }

  dynamic "statement" {
    for_each = length(var.denied_data_bucket_arns) > 0 ? [1] : []
    content {
      sid       = "NeverReadSchoolObjects"
      effect    = "Deny"
      actions   = ["s3:GetObject", "s3:GetObjectVersion"]
      resources = [for b in var.denied_data_bucket_arns : "${b}/*"]
    }
  }
}

resource "aws_iam_role_policy" "plan" {
  count = var.create_plan_role ? 1 : 0

  name   = "plan"
  role   = aws_iam_role.plan[0].id
  policy = data.aws_iam_policy_document.plan.json
}

# --- Apply role (environment-gated; optional) -------------------------------------------------

data "aws_iam_policy_document" "apply_trust" {
  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]
    principals {
      type        = "Federated"
      identifiers = [local.provider_arn]
    }
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:sub"
      values   = ["repo:${local.repo}:environment:${var.apply_environment}"]
    }
  }
}

resource "aws_iam_role" "apply" {
  count = var.create_apply_role ? 1 : 0

  name                 = "${var.name_prefix}-gha-terraform-apply"
  description          = "terraform plan/apply from GitHub Environment ${var.apply_environment} (required reviewers)"
  assume_role_policy   = data.aws_iam_policy_document.apply_trust.json
  max_session_duration = var.max_session_seconds
  tags                 = var.tags
}

# Terraform manages IAM, KMS and networking, so the apply role is administrative. It is only reachable
# from the protected GitHub Environment (required reviewers, main branch only); school object data is
# still explicitly denied.
resource "aws_iam_role_policy_attachment" "apply_admin" {
  count = var.create_apply_role ? 1 : 0

  role       = aws_iam_role.apply[0].name
  policy_arn = "arn:${data.aws_partition.current.partition}:iam::aws:policy/AdministratorAccess"
}

data "aws_iam_policy_document" "apply_deny" {
  statement {
    sid       = "NeverReadSchoolObjects"
    effect    = "Deny"
    actions   = ["s3:GetObject", "s3:GetObjectVersion"]
    resources = length(var.denied_data_bucket_arns) > 0 ? [for b in var.denied_data_bucket_arns : "${b}/*"] : ["arn:${data.aws_partition.current.partition}:s3:::sos-none-placeholder/*"]
  }
}

resource "aws_iam_role_policy" "apply_deny" {
  count = var.create_apply_role ? 1 : 0

  name   = "deny-school-data"
  role   = aws_iam_role.apply[0].id
  policy = data.aws_iam_policy_document.apply_deny.json
}

output "apply_role_arn" {
  description = "Environment-gated terraform apply role (null when not created)."
  value       = var.create_apply_role ? aws_iam_role.apply[0].arn : null
}

output "oidc_provider_arn" {
  description = "GitHub OIDC provider ARN."
  value       = local.provider_arn
}

output "deploy_role_arn" {
  description = "Role for the deploy workflow (aws-actions/configure-aws-credentials role-to-assume)."
  value       = aws_iam_role.deploy.arn
}

output "plan_role_arn" {
  description = "Role for terraform plan on pull requests."
  value       = var.create_plan_role ? aws_iam_role.plan[0].arn : null
}

output "deploy_subjects" {
  description = "OIDC subjects allowed to assume the deploy role."
  value       = local.deploy_subjects
}
