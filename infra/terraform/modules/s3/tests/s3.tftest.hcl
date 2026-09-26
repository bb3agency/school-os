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
}

variables {
  name_prefix       = "sos-test"
  bucket_suffix     = "111122223333"
  data_kms_key_arn  = "arn:aws:kms:ap-south-1:111122223333:key/00000000-0000-0000-0000-000000000001"
  audit_kms_key_arn = "arn:aws:kms:ap-south-1:111122223333:key/00000000-0000-0000-0000-000000000002"
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
    condition     = module.artifacts.sse_algorithm == "aws:kms"
    error_message = "Artifacts bucket must use SSE-KMS."
  }

  assert {
    condition     = module.logs.sse_algorithm == "AES256"
    error_message = "Log-delivery bucket uses SSE-S3 (ALB/S3 access logs cannot target SSE-KMS)."
  }
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
