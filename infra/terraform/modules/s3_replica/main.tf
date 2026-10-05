# Independent, locked copy of the files bucket in the backup region (audit W3-06 (b); docs/10 §9,
# docs/08 §7; NFR-AVL-002, NFR-PRV-001). Call it with the backup-region provider; the replication
# rule is written to the source bucket in source_region (provider v6 per-resource region):
#   module "files_replica" {
#     source    = "../../modules/s3_replica"
#     providers = { aws = aws.dr }
#     ...
#   }
#
# - S3 replication of every new object version under t/ (each school's files) to a replica bucket in
#   ap-south-2 with versioning, SSE-KMS under a CMK of that region, and Object Lock GOVERNANCE for
#   retention_days (>= the 90-day recovery window). Delete markers are replicated; version deletes
#   never are (S3 does not replicate them), and every replica version is locked, so neither a
#   compromised app role nor the lifecycle rules of the source can remove the copy early.
# - The replication role trusts only s3.amazonaws.com for this source bucket in this account: no app
#   task role, host role or person can assume it.
# - Nobody may bypass the governance lock (bucket policy) unless listed in
#   governance_bypass_principal_arns, e.g. a break-glass erasure role (two-person, CloudTrail).
# - Lifecycle in the replica mirrors the source's tag rules (discarded, exports, full exports), so
#   such copies go as soon as their lock ends; other noncurrent versions after retention_days.
# - Only versions written after the configuration exists are replicated; copy existing objects once
#   with S3 Batch Replication (docs/10 §9).

data "aws_caller_identity" "current" {}
data "aws_partition" "current" {}
data "aws_region" "replica" {}

locals {
  partition   = data.aws_partition.current.partition
  account_id  = data.aws_caller_identity.current.account_id
  source_arn  = "arn:${local.partition}:s3:::${var.source_bucket_id}"
  replica_arn = "arn:${local.partition}:s3:::${var.name}"
  role_name   = "${var.name}-repl"
}

# --- Replica bucket (backup region) ----------------------------------------------------------------

data "aws_iam_policy_document" "replica" {
  # The governance lock is only as strong as who may bypass it: nobody, unless named here.
  statement {
    sid       = "DenyGovernanceBypass"
    effect    = "Deny"
    actions   = ["s3:BypassGovernanceRetention"]
    resources = ["${local.replica_arn}/*"]
    principals {
      type        = "*"
      identifiers = ["*"]
    }
    dynamic "condition" {
      for_each = length(var.governance_bypass_principal_arns) > 0 ? [1] : []
      content {
        test     = "ArnNotLike"
        variable = "aws:PrincipalArn"
        values   = var.governance_bypass_principal_arns
      }
    }
  }
}

module "replica" {
  source = "../s3_bucket"

  name                   = var.name
  kms_key_arn            = var.replica_kms_key_arn
  force_destroy          = var.force_destroy
  object_lock            = { mode = "GOVERNANCE", days = var.retention_days }
  additional_policy_json = data.aws_iam_policy_document.replica.json
  lifecycle_rules = [
    # The source's tag rules: such objects leave the replica once their lock ends (docs/08 §7).
    { id = "discarded-1d", tags = { "sos-lifecycle" = "discarded" }, expiration_days = 1, noncurrent_version_expiration_days = 1 },
    { id = "exports-7d", tags = { "sos-lifecycle" = "export-7d" }, expiration_days = 7, noncurrent_version_expiration_days = 1 },
    { id = "tenant-export-2d", tags = { "sos-lifecycle" = "tenant-export-2d" }, expiration_days = 2, noncurrent_version_expiration_days = 1 },
    # A deleted or overwritten file stays recoverable from the replica for the lock period.
    { id = "noncurrent-and-multipart", noncurrent_version_expiration_days = var.retention_days, abort_incomplete_multipart_days = 7 },
  ]
  tags = merge(var.tags, { data_class = "C3-children-personal", purpose = "files-replica" })
}

# --- Replication role (assumable by S3 only) -------------------------------------------------------

data "aws_iam_policy_document" "assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["s3.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [local.account_id]
    }
    condition {
      test     = "ArnLike"
      variable = "aws:SourceArn"
      values   = [local.source_arn]
    }
  }
}

resource "aws_iam_role" "replication" {
  name               = local.role_name
  assume_role_policy = data.aws_iam_policy_document.assume.json
  tags               = var.tags
}

data "aws_iam_policy_document" "replication" {
  statement {
    sid       = "SourceBucket"
    actions   = ["s3:GetReplicationConfiguration", "s3:ListBucket"]
    resources = [local.source_arn]
  }

  statement {
    sid = "SourceVersions"
    actions = [
      "s3:GetObjectVersionForReplication", "s3:GetObjectVersionAcl", "s3:GetObjectVersionTagging",
      "s3:GetObjectRetention", "s3:GetObjectLegalHold",
    ]
    resources = ["${local.source_arn}/${var.prefix}*"]
  }

  statement {
    sid       = "ReplicaWrite"
    actions   = ["s3:ReplicateObject", "s3:ReplicateDelete", "s3:ReplicateTags"]
    resources = ["${local.replica_arn}/${var.prefix}*"]
  }

  statement {
    sid       = "DecryptSource"
    actions   = ["kms:Decrypt"]
    resources = [var.source_kms_key_arn]
    condition {
      test     = "StringEquals"
      variable = "kms:ViaService"
      values   = ["s3.${var.source_region}.amazonaws.com"]
    }
  }

  statement {
    sid       = "EncryptReplica"
    actions   = ["kms:Encrypt", "kms:GenerateDataKey"]
    resources = [var.replica_kms_key_arn]
    condition {
      test     = "StringEquals"
      variable = "kms:ViaService"
      values   = ["s3.${data.aws_region.replica.region}.amazonaws.com"]
    }
  }
}

resource "aws_iam_role_policy" "replication" {
  name   = "files-replication"
  role   = aws_iam_role.replication.id
  policy = data.aws_iam_policy_document.replication.json
}

# --- Replication rule (on the source bucket) ---------------------------------------------------------

resource "aws_s3_bucket_replication_configuration" "this" {
  region = var.source_region
  bucket = var.source_bucket_id
  role   = aws_iam_role.replication.arn

  rule {
    id       = "files-to-backup-region"
    status   = "Enabled"
    priority = 1

    filter {
      prefix = var.prefix
    }

    delete_marker_replication {
      status = "Enabled"
    }

    source_selection_criteria {
      sse_kms_encrypted_objects {
        status = "Enabled"
      }
    }

    destination {
      bucket        = module.replica.arn
      storage_class = "STANDARD"

      encryption_configuration {
        replica_kms_key_id = var.replica_kms_key_arn
      }
    }
  }

  depends_on = [aws_iam_role_policy.replication]
}
