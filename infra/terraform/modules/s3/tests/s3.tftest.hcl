# SEC-011, NFR-PRV-001: every shared-tier bucket is private, encrypted and TLS-only;
# the audit archive has Object Lock in COMPLIANCE mode with 3-year default retention.

mock_provider "aws" {
  mock_data "aws_caller_identity" {
    defaults = {
      account_id = "111122223333"
    }
  }
  mock_data "aws_partition" {
    defaults = {
      partition = "aws"
    }
  }
  mock_data "aws_iam_policy_document" {
    defaults = {
      json = "{\"Version\":\"2012-10-17\",\"Statement\":[]}"
    }
  }
  mock_data "aws_region" {
    defaults = {
      region = "ap-south-1"
    }
  }
}

variables {
  name_prefix           = "sos-test"
  bucket_suffix         = "111122223333"
  data_kms_key_arn      = "arn:aws:kms:ap-south-1:111122223333:key/00000000-0000-0000-0000-000000000001"
  audit_kms_key_arn     = "arn:aws:kms:ap-south-1:111122223333:key/00000000-0000-0000-0000-000000000002"
  artifacts_kms_key_arn = "arn:aws:kms:ap-south-1:111122223333:key/00000000-0000-0000-0000-000000000003"
}

run "buckets_block_public_access" {
  command = plan

  assert {
    condition = alltrue([
      module.files.public_access_block.block_public_acls,
      module.files.public_access_block.block_public_policy,
      module.files.public_access_block.ignore_public_acls,
      module.files.public_access_block.restrict_public_buckets,
      module.audit_archive.public_access_block.block_public_acls,
      module.audit_archive.public_access_block.restrict_public_buckets,
      module.logs.public_access_block.block_public_policy,
      module.artifacts.public_access_block.restrict_public_buckets,
    ])
    error_message = "All buckets must block public access (SEC-011)."
  }
}

run "data_buckets_use_cmk" {
  command = plan

  assert {
    condition     = module.files.sse_algorithm == "aws:kms" && module.files.kms_key_arn == var.data_kms_key_arn
    error_message = "Files bucket must use SSE-KMS with the data CMK."
  }

  assert {
    condition     = module.audit_archive.sse_algorithm == "aws:kms" && module.audit_archive.kms_key_arn == var.audit_kms_key_arn
    error_message = "Audit archive must use SSE-KMS with the audit CMK."
  }

  assert {
    condition     = module.artifacts.sse_algorithm == "aws:kms" && module.artifacts.kms_key_arn == var.artifacts_kms_key_arn && output.artifacts_kms_key_arn == var.artifacts_kms_key_arn
    error_message = "Artifacts bucket must use SSE-KMS with its own CMK (every dedicated host may decrypt it; audit 2026-10-05)."
  }

  assert {
    condition     = module.logs.sse_algorithm == "AES256"
    error_message = "Log-delivery bucket uses SSE-S3 (ALB/S3 access logs cannot target SSE-KMS)."
  }
}

# Audit 2026-10-05 hardening (key separation): every dedicated host may decrypt the artifacts key, so
# it must never be the data key that protects the files bucket, RDS and Secrets Manager.
run "artifacts_key_is_not_the_data_key" {
  command = plan

  variables {
    artifacts_kms_key_arn = "arn:aws:kms:ap-south-1:111122223333:key/00000000-0000-0000-0000-000000000001"
  }

  expect_failures = [var.artifacts_kms_key_arn]
}

run "audit_archive_object_lock_compliance_3y" {
  command = plan

  assert {
    condition     = module.audit_archive.object_lock_enabled
    error_message = "Audit archive must be created with Object Lock enabled."
  }

  assert {
    condition     = module.audit_archive.object_lock_mode == "COMPLIANCE" && module.audit_archive.object_lock_years == 3
    error_message = "Audit archive default retention must be COMPLIANCE for 3 years."
  }
}

run "versioning_and_bucket_names" {
  command = plan

  assert {
    condition     = module.files.versioning_status == "Enabled" && module.audit_archive.versioning_status == "Enabled"
    error_message = "Versioning must be enabled."
  }

  assert {
    condition     = output.files_bucket == "sos-test-files-111122223333" && output.audit_bucket == "sos-test-audit-archive-111122223333"
    error_message = "Bucket names follow sos-<env>-<purpose>-<account>."
  }
}

run "sse_s3_requires_justification" {
  command = plan

  module {
    source = "../s3_bucket"
  }

  variables {
    name        = "sos-test-nojust"
    kms_key_arn = null
  }

  expect_failures = [aws_s3_bucket_server_side_encryption_configuration.this]
}

# SEC-016, docs/07 §10: without upload origins no bucket has a CORS configuration.
run "no_cors_without_upload_origins" {
  command = plan

  assert {
    condition = alltrue([
      for rules in [module.files.cors_rules, module.audit_archive.cors_rules, module.logs.cors_rules, module.artifacts.cors_rules] : length(rules) == 0
    ])
    error_message = "No bucket gets CORS unless upload origins are configured."
  }
}

# SEC-016, SEC-010: the app origin may POST (presigned upload) to the files bucket and nothing else;
# FILES_ORIGIN is the regional virtual-hosted origin the API presigns with.
run "files_bucket_allows_presigned_post_from_the_app_only" {
  command = plan

  variables {
    files_upload_origins = ["https://app.example.test"]
  }

  assert {
    condition     = length(module.files.cors_rules) == 1
    error_message = "The files bucket has exactly one CORS rule."
  }

  assert {
    condition = (
      module.files.cors_rules[0].allowed_origins == toset(["https://app.example.test"])
      && module.files.cors_rules[0].allowed_methods == toset(["POST"])
      && module.files.cors_rules[0].allowed_headers == toset(["content-type"])
      && length(module.files.cors_rules[0].expose_headers) == 0
      && module.files.cors_rules[0].max_age_seconds == 3600
    )
    error_message = "Files bucket CORS: the app origin, POST only, content-type only, nothing exposed."
  }

  assert {
    condition = alltrue([
      for rules in [module.audit_archive.cors_rules, module.logs.cors_rules, module.artifacts.cors_rules] : length(rules) == 0
    ])
    error_message = "Only the files bucket has CORS (never the audit, logs or artifacts bucket)."
  }

  assert {
    condition     = output.files_browser_origin == "https://sos-test-files-111122223333.s3.ap-south-1.amazonaws.com"
    error_message = "FILES_ORIGIN is the regional virtual-hosted origin of the files bucket."
  }
}

run "cors_origin_must_not_have_a_path" {
  command = plan

  module {
    source = "../s3_bucket"
  }

  variables {
    name        = "sos-test-cors-path"
    kms_key_arn = "arn:aws:kms:ap-south-1:111122223333:key/00000000-0000-0000-0000-000000000001"
    cors_rules  = [{ allowed_origins = ["https://app.example.test/"], allowed_methods = ["POST"] }]
  }

  expect_failures = [var.cors_rules]
}

run "cors_origin_must_not_be_a_wildcard" {
  command = plan

  module {
    source = "../s3_bucket"
  }

  variables {
    name        = "sos-test-cors-wild"
    kms_key_arn = "arn:aws:kms:ap-south-1:111122223333:key/00000000-0000-0000-0000-000000000001"
    cors_rules  = [{ allowed_origins = ["https://*.example.test"], allowed_methods = ["POST"] }]
  }

  expect_failures = [var.cors_rules]
}

run "cors_origin_must_be_https" {
  command = plan

  module {
    source = "../s3_bucket"
  }

  variables {
    name        = "sos-test-cors-http"
    kms_key_arn = "arn:aws:kms:ap-south-1:111122223333:key/00000000-0000-0000-0000-000000000001"
    cors_rules  = [{ allowed_origins = ["http://app.example.test"], allowed_methods = ["POST"] }]
  }

  expect_failures = [var.cors_rules]
}

# Audit W3-06, PRV-016: the worker tags every automatic deletion (and every image that showed a full
# Aadhaar number) sos-lifecycle=discarded before deleting it; the files bucket must then expire the
# object and its noncurrent version after one day instead of keeping it for the 90-day window.
run "files_bucket_discards_tagged_objects_after_one_day" {
  command = plan

  assert {
    condition = (
      length(module.files.lifecycle_rules["discarded-1d"].tags) == 1 &&
      module.files.lifecycle_rules["discarded-1d"].tags["sos-lifecycle"] == "discarded" &&
      module.files.lifecycle_rules["discarded-1d"].expiration_days == 1 &&
      module.files.lifecycle_rules["discarded-1d"].noncurrent_days == 1
    )
    error_message = "The files bucket expires sos-lifecycle=discarded objects and their noncurrent versions after 1 day (W3-06, PRV-016)."
  }

  assert {
    condition     = module.files.lifecycle_rules["noncurrent-and-multipart"].noncurrent_days == 90
    error_message = "Every other deletion stays recoverable for the 90-day window (W3-06)."
  }
}

# Audit 2026-10-05 hardening (confused deputy): ALB access logs land only under this account's
# AWSLogs prefix, and a delivery that names its source account must be this one.
run "alb_log_delivery_pins_the_account" {
  command = plan

  assert {
    condition = one([
      for s in data.aws_iam_policy_document.logs_delivery.statement : anytrue([
        for c in s.condition : c.variable == "aws:SourceAccount" && toset(c.values) == toset(["111122223333"])
      ]) && toset(s.resources) == toset(["arn:aws:s3:::sos-test-logs-111122223333/alb/AWSLogs/111122223333/*"])
      if s.sid == "AlbLogDelivery"
    ])
    error_message = "The ALB log delivery statement pins this account (resource prefix and aws:SourceAccount)."
  }
}
