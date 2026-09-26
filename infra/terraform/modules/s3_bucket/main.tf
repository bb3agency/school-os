# Hardened private S3 bucket used by every SchoolOS stack (SEC-011, NFR-PRV-001).
# Baseline: Block Public Access (all four), ACLs disabled (BucketOwnerEnforced), default encryption,
# versioning, TLS-only + TLS>=1.2 bucket policy, optional Object Lock / access logging / lifecycle.

resource "aws_s3_bucket" "this" {
  bucket              = var.name
  force_destroy       = var.force_destroy
  object_lock_enabled = var.object_lock != null
  tags                = var.tags
}

resource "aws_s3_bucket_public_access_block" "this" {
  bucket                  = aws_s3_bucket.this.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_ownership_controls" "this" {
  bucket = aws_s3_bucket.this.id
  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

# SSE-S3 is only allowed with an explicit sse_s3_justification (enforced by the precondition below);
# it is used solely for log-delivery buckets because ALB access logs and S3 server access logs cannot be
# delivered to SSE-KMS buckets. Every bucket holding school data passes a CMK (asserted by tests).
#trivy:ignore:AVD-AWS-0132
resource "aws_s3_bucket_server_side_encryption_configuration" "this" {
  bucket = aws_s3_bucket.this.id

  lifecycle {
    precondition {
      condition     = var.kms_key_arn != null || var.sse_s3_justification != null
      error_message = "kms_key_arn is required unless sse_s3_justification explains why SSE-S3 must be used."
    }
  }

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm     = var.kms_key_arn == null ? "AES256" : "aws:kms"
      kms_master_key_id = var.kms_key_arn
    }
    bucket_key_enabled = var.kms_key_arn != null
  }
}

resource "aws_s3_bucket_versioning" "this" {
  bucket = aws_s3_bucket.this.id
  versioning_configuration {
    # Object Lock requires versioning.
    status = var.versioning_enabled || var.object_lock != null ? "Enabled" : "Suspended"
  }
}

resource "aws_s3_bucket_object_lock_configuration" "this" {
  count  = var.object_lock == null ? 0 : 1
  bucket = aws_s3_bucket.this.id

  rule {
    default_retention {
      mode  = var.object_lock.mode
      days  = try(var.object_lock.days, null)
      years = try(var.object_lock.years, null)
    }
  }

  depends_on = [aws_s3_bucket_versioning.this]
}

resource "aws_s3_bucket_logging" "this" {
  count         = var.access_logging_enabled ? 1 : 0
  bucket        = aws_s3_bucket.this.id
  target_bucket = var.access_log_bucket
  target_prefix = coalesce(var.access_log_prefix, "s3/${var.name}/")
}

resource "aws_s3_bucket_lifecycle_configuration" "this" {
  count  = length(var.lifecycle_rules) == 0 ? 0 : 1
  bucket = aws_s3_bucket.this.id

  dynamic "rule" {
    for_each = var.lifecycle_rules
    content {
      id     = rule.value.id
      status = "Enabled"

      # Single tag, no prefix
      dynamic "filter" {
        for_each = length(rule.value.tags) == 1 && rule.value.prefix == null ? [1] : []
        content {
          tag {
            key   = keys(rule.value.tags)[0]
            value = values(rule.value.tags)[0]
          }
        }
      }

      # Tags (+ optional prefix)
      dynamic "filter" {
        for_each = length(rule.value.tags) > 1 || (length(rule.value.tags) == 1 && rule.value.prefix != null) ? [1] : []
        content {
          and {
            prefix = rule.value.prefix
            tags   = rule.value.tags
          }
        }
      }

      # Prefix only (empty prefix = whole bucket)
      dynamic "filter" {
        for_each = length(rule.value.tags) == 0 ? [1] : []
        content {
          prefix = rule.value.prefix == null ? "" : rule.value.prefix
        }
      }

      dynamic "expiration" {
        for_each = rule.value.expiration_days != null ? [1] : []
        content {
          days = rule.value.expiration_days
        }
      }

      dynamic "expiration" {
        for_each = rule.value.expiration_days == null && coalesce(rule.value.expired_object_delete_marker, false) ? [1] : []
        content {
          expired_object_delete_marker = true
        }
      }

      dynamic "noncurrent_version_expiration" {
        for_each = rule.value.noncurrent_version_expiration_days != null ? [1] : []
        content {
          noncurrent_days = rule.value.noncurrent_version_expiration_days
        }
      }

      dynamic "abort_incomplete_multipart_upload" {
        for_each = rule.value.abort_incomplete_multipart_days != null ? [1] : []
        content {
          days_after_initiation = rule.value.abort_incomplete_multipart_days
        }
      }

      dynamic "transition" {
        for_each = rule.value.transition_days != null ? [1] : []
        content {
          days          = rule.value.transition_days
          storage_class = coalesce(rule.value.transition_storage_class, "STANDARD_IA")
        }
      }
    }
  }

  depends_on = [aws_s3_bucket_versioning.this]
}

data "aws_iam_policy_document" "baseline" {
  source_policy_documents = var.additional_policy_json == null ? [] : [var.additional_policy_json]

  statement {
    sid     = "DenyInsecureTransport"
    effect  = "Deny"
    actions = ["s3:*"]
    resources = [
      aws_s3_bucket.this.arn,
      "${aws_s3_bucket.this.arn}/*",
    ]
    principals {
      type        = "*"
      identifiers = ["*"]
    }
    condition {
      test     = "Bool"
      variable = "aws:SecureTransport"
      values   = ["false"]
    }
  }

  statement {
    sid     = "DenyOutdatedTLS"
    effect  = "Deny"
    actions = ["s3:*"]
    resources = [
      aws_s3_bucket.this.arn,
      "${aws_s3_bucket.this.arn}/*",
    ]
    principals {
      type        = "*"
      identifiers = ["*"]
    }
    condition {
      test     = "NumericLessThan"
      variable = "s3:TlsVersion"
      values   = ["1.2"]
    }
  }
}

resource "aws_s3_bucket_policy" "this" {
  bucket = aws_s3_bucket.this.id
  policy = data.aws_iam_policy_document.baseline.json

  depends_on = [aws_s3_bucket_public_access_block.this]
}
