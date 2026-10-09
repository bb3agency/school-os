# Customer-managed KMS keys (SEC-011): separate CMKs for data, audit archive and backups,
# automatic annual rotation, least-privilege key policies. Asymmetric SIGN_VERIFY keys (audit archive
# signatures, FR-AUD-004) are supported too; AWS KMS does not rotate those automatically.

data "aws_caller_identity" "current" {}
data "aws_partition" "current" {}
data "aws_region" "current" {}

locals {
  account_id = data.aws_caller_identity.current.account_id
  partition  = data.aws_partition.current.partition
  region     = data.aws_region.current.region

  # Automatic rotation exists only for symmetric encryption keys.
  rotating = { for k, v in var.keys : k => v.key_spec == "SYMMETRIC_DEFAULT" }
}

data "aws_iam_policy_document" "key" {
  for_each = var.keys

  # Standard statement: lets IAM policies in this account grant key usage. Without it the key
  # becomes unmanageable. Usage is then granted per role (task roles, instance roles) via IAM.
  statement {
    sid       = "EnableIAMPolicies"
    effect    = "Allow"
    actions   = ["kms:*"]
    resources = ["*"]
    principals {
      type        = "AWS"
      identifiers = ["arn:${local.partition}:iam::${local.account_id}:root"]
    }
  }

  dynamic "statement" {
    for_each = length(var.admin_role_arns) > 0 ? [1] : []
    content {
      sid    = "KeyAdministrators"
      effect = "Allow"
      actions = [
        "kms:Create*", "kms:Describe*", "kms:Enable*", "kms:List*", "kms:Put*", "kms:Update*",
        "kms:Revoke*", "kms:Disable*", "kms:Get*", "kms:Delete*", "kms:TagResource",
        "kms:UntagResource", "kms:ScheduleKeyDeletion", "kms:CancelKeyDeletion", "kms:RotateKeyOnDemand",
      ]
      resources = ["*"]
      principals {
        type        = "AWS"
        identifiers = var.admin_role_arns
      }
    }
  }

  dynamic "statement" {
    for_each = each.value.allow_cloudwatch_logs ? [1] : []
    content {
      sid       = "AllowCloudWatchLogs"
      effect    = "Allow"
      actions   = ["kms:Encrypt*", "kms:Decrypt*", "kms:ReEncrypt*", "kms:GenerateDataKey*", "kms:Describe*"]
      resources = ["*"]
      principals {
        type        = "Service"
        identifiers = ["logs.${local.region}.amazonaws.com"]
      }
      condition {
        test     = "ArnLike"
        variable = "kms:EncryptionContext:aws:logs:arn"
        values   = ["arn:${local.partition}:logs:${local.region}:${local.account_id}:*"]
      }
    }
  }

  dynamic "statement" {
    for_each = length(each.value.service_principals) > 0 ? [1] : []
    content {
      sid       = "AllowAwsServicesForThisAccount"
      effect    = "Allow"
      actions   = ["kms:Decrypt", "kms:GenerateDataKey*"]
      resources = ["*"]
      principals {
        type        = "Service"
        identifiers = each.value.service_principals
      }
      condition {
        test     = "StringEquals"
        variable = "aws:SourceAccount"
        values   = [local.account_id]
      }
    }
  }
}

# Symmetric keys rotate; AWS KMS cannot rotate asymmetric (audit signing) keys automatically.
# nosemgrep: terraform.aws.security.aws-kms-no-rotation.aws-kms-no-rotation
resource "aws_kms_key" "this" {
  for_each = var.keys

  description              = each.value.description
  customer_master_key_spec = each.value.key_spec
  key_usage                = each.value.key_usage
  enable_key_rotation      = local.rotating[each.key]
  rotation_period_in_days  = local.rotating[each.key] ? var.rotation_period_in_days : null
  deletion_window_in_days  = var.deletion_window_in_days
  multi_region             = false
  policy                   = data.aws_iam_policy_document.key[each.key].json
  tags                     = merge(var.tags, { key_purpose = each.key })
}

resource "aws_kms_alias" "this" {
  for_each = var.keys

  name          = "alias/${var.name_prefix}-${each.key}"
  target_key_id = aws_kms_key.this[each.key].key_id
}
