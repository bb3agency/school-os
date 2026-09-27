# Shared-tier object storage (docs/04 §8.2, SEC-011, NFR-PRV-001).
#
# Lifecycle note: the per-tenant layout puts the tenant ID before the category
# (t/<tenant_id>/exports/...), and S3 lifecycle filters only match a literal prefix. The expiring
# categories are therefore selected by object tag, which the app sets on upload:
#   sos-lifecycle=export-7d         -> t/<tenant_id>/exports/<export_id>/<file> (set on upload by
#                                      app/documents/storage.py put(lifecycle=...))
#   sos-lifecycle=tenant-export-2d  -> t/<tenant_id>/tenant-export/<job_id>.zip
#   sos-lifecycle=import-raw-90d    -> t/<tenant_id>/imports/<batch_id>/raw.<ext> (optional, retention setting)
# Documents (t/<tenant_id>/docs/...) are never expired by lifecycle; retention purges are app jobs.

data "aws_caller_identity" "current" {}
data "aws_partition" "current" {}

locals {
  suffix = var.bucket_suffix == "" ? "" : "-${var.bucket_suffix}"
  names = {
    files     = "${var.name_prefix}-files${local.suffix}"
    audit     = "${var.name_prefix}-audit-archive${local.suffix}"
    logs      = "${var.name_prefix}-logs${local.suffix}"
    artifacts = "${var.name_prefix}-artifacts${local.suffix}"
  }

  # Browser uploads (SEC-016, docs/07 §10): the web app posts files straight to a presigned POST URL
  # on this bucket. Only POST is allowed cross-origin: previews use <img src> and downloads are
  # navigations to presigned GET URLs, neither of which needs CORS; the web never reads an object
  # (or the ETag) with fetch/XHR. XHR with an upload-progress listener always sends a preflight;
  # multipart/form-data is a CORS-safelisted Content-Type, so content-type is the only header
  # allowed, and only as a safety margin. Browsers cap preflight caching at <= 2 h.
  files_cors_rules = length(var.files_upload_origins) == 0 ? [] : [
    {
      allowed_origins = var.files_upload_origins
      allowed_methods = ["POST"]
      allowed_headers = ["content-type"]
      expose_headers  = []
      max_age_seconds = 3600
    },
  ]
}

# --- Logs bucket (ALB access logs + S3 server access logs) --------------------

data "aws_iam_policy_document" "logs_delivery" {
  # ALB access logs (regions launched after 2022 such as ap-south-2 use the service principal;
  # ap-south-1 also accepts it).
  statement {
    sid       = "AlbLogDelivery"
    actions   = ["s3:PutObject"]
    resources = ["arn:${data.aws_partition.current.partition}:s3:::${local.names.logs}/alb/AWSLogs/${data.aws_caller_identity.current.account_id}/*"]
    principals {
      type        = "Service"
      identifiers = ["logdelivery.elasticloadbalancing.amazonaws.com"]
    }
  }

  statement {
    sid       = "S3ServerAccessLogs"
    actions   = ["s3:PutObject"]
    resources = ["arn:${data.aws_partition.current.partition}:s3:::${local.names.logs}/s3/*"]
    principals {
      type        = "Service"
      identifiers = ["logging.s3.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [data.aws_caller_identity.current.account_id]
    }
  }
}

module "logs" {
  source = "../s3_bucket"

  name                   = local.names.logs
  kms_key_arn            = null
  sse_s3_justification   = "ALB access logs and S3 server access logs can only be delivered to SSE-S3 buckets."
  additional_policy_json = data.aws_iam_policy_document.logs_delivery.json
  force_destroy          = var.force_destroy
  lifecycle_rules = [
    {
      id                                 = "expire-logs"
      expiration_days                    = var.logs_retention_days
      noncurrent_version_expiration_days = 30
      abort_incomplete_multipart_days    = 7
    },
  ]
  tags = merge(var.tags, { data_class = "C1-operational-logs" })
}

# --- Files bucket -------------------------------------------------------------

module "files" {
  source = "../s3_bucket"

  name                   = local.names.files
  kms_key_arn            = var.data_kms_key_arn
  access_logging_enabled = true
  access_log_bucket      = module.logs.id
  force_destroy          = var.force_destroy
  cors_rules             = local.files_cors_rules
  lifecycle_rules = [
    {
      # docs/05 §13: exports are kept 7 days. The tag is set in the PUT (documents.storage); the
      # noncurrent version (left by the purge job's delete) goes after 1 day, not the 90-day window.
      id                                 = "exports-7d"
      tags                               = { "sos-lifecycle" = "export-7d" }
      expiration_days                    = 7
      noncurrent_version_expiration_days = 1
    },
    {
      id              = "tenant-export-2d"
      tags            = { "sos-lifecycle" = "tenant-export-2d" }
      expiration_days = 2
    },
    {
      id              = "import-raw-90d"
      tags            = { "sos-lifecycle" = "import-raw-90d" }
      expiration_days = 90
    },
    {
      # PRV-016: images that showed a full Aadhaar number are tagged before the app deletes
      # them (documents.storage discard), so no copy outlives the 90-day recovery window.
      id                                 = "discarded-1d"
      tags                               = { "sos-lifecycle" = "discarded" }
      expiration_days                    = 1
      noncurrent_version_expiration_days = 1
    },
    {
      id                                 = "noncurrent-and-multipart"
      noncurrent_version_expiration_days = var.files_noncurrent_version_days
      abort_incomplete_multipart_days    = 7
    },
  ]
  tags = var.tags
}

# --- Audit archive (Object Lock) -----------------------------------------------

module "audit_archive" {
  source = "../s3_bucket"

  name                   = local.names.audit
  kms_key_arn            = var.audit_kms_key_arn
  access_logging_enabled = true
  access_log_bucket      = module.logs.id
  force_destroy          = false
  object_lock = {
    mode  = var.audit_object_lock_mode
    years = var.audit_object_lock_days == null ? var.audit_object_lock_years : null
    days  = var.audit_object_lock_days
  }
  lifecycle_rules = [
    {
      id                              = "multipart"
      abort_incomplete_multipart_days = 7
    },
  ]
  tags = merge(var.tags, { data_class = "C2-audit" })
}

# --- Release artifacts (dedicated-tier bundles published by CI) ----------------

module "artifacts" {
  source = "../s3_bucket"

  name                   = local.names.artifacts
  kms_key_arn            = var.data_kms_key_arn
  access_logging_enabled = true
  access_log_bucket      = module.logs.id
  force_destroy          = var.force_destroy
  lifecycle_rules = [
    {
      id                                 = "noncurrent"
      noncurrent_version_expiration_days = 365
      abort_incomplete_multipart_days    = 7
    },
  ]
  tags = merge(var.tags, { data_class = "C0-public-code" })
}
