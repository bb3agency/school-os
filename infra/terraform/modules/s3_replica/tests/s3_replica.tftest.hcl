# Audit W3-06 (b): an independent, locked copy of the files bucket in ap-south-2. SSE-KMS under the
# backup region's CMK, versioning, Object Lock GOVERNANCE >= the 90-day recovery window, delete markers
# replicated, a replication role only S3 can assume, and nobody may bypass the lock.

# The module runs with the backup-region provider (ap-south-2).
mock_provider "aws" {
  mock_data "aws_iam_policy_document" {
    defaults = { json = "{\"Version\":\"2012-10-17\",\"Statement\":[]}" }
  }
  mock_data "aws_caller_identity" {
    defaults = { account_id = "111122223333" }
  }
  mock_data "aws_partition" {
    defaults = { partition = "aws" }
  }
  mock_data "aws_region" {
    defaults = { region = "ap-south-2" }
  }
  mock_resource "aws_iam_role" {
    defaults = { arn = "arn:aws:iam::111122223333:role/mock-repl" }
  }
  mock_resource "aws_s3_bucket" {
    defaults = { arn = "arn:aws:s3:::sos-test-files-replica-111122223333" }
  }
}

variables {
  name                = "sos-test-files-replica-111122223333"
  source_bucket_id    = "sos-test-files-111122223333"
  source_kms_key_arn  = "arn:aws:kms:ap-south-1:111122223333:key/00000000-0000-0000-0000-000000000001"
  replica_kms_key_arn = "arn:aws:kms:ap-south-2:111122223333:key/00000000-0000-0000-0000-000000000002"
}

run "replica_is_locked_encrypted_and_versioned" {
  command = plan

  assert {
    condition     = output.posture.object_lock_enabled && output.posture.object_lock_mode == "GOVERNANCE" && output.posture.object_lock_days >= 90
    error_message = "Every replica version is locked (GOVERNANCE) for at least the 90-day recovery window."
  }

  assert {
    condition     = output.posture.versioning == "Enabled" && output.posture.sse_algorithm == "aws:kms" && output.posture.kms_key_arn == var.replica_kms_key_arn
    error_message = "The replica is versioned and encrypted with the backup region's CMK."
  }

  assert {
    condition     = alltrue(values(output.posture.public_access_block))
    error_message = "The replica blocks all public access."
  }

  assert {
    condition     = output.posture.replica_region == "ap-south-2"
    error_message = "The copy lives in Hyderabad (NFR-PRV-001: India only, the backup region)."
  }
}

run "every_school_file_is_replicated" {
  command = plan

  assert {
    condition     = output.posture.rule_status == "Enabled" && output.posture.replicated_prefix == "t/" && output.posture.delete_markers == "Enabled"
    error_message = "Every object under t/ is replicated, delete markers too (version deletes never are)."
  }

  assert {
    condition     = output.posture.replica_kms_key_id == var.replica_kms_key_arn
    error_message = "Replicas are re-encrypted with the backup region's CMK."
  }
}

run "only_s3_can_assume_the_replication_role" {
  command = plan

  assert {
    condition = alltrue([
      for s in data.aws_iam_policy_document.assume.statement :
      s.actions == toset(["sts:AssumeRole"]) && alltrue([for p in s.principals : p.type == "Service" && p.identifiers == toset(["s3.amazonaws.com"])])
    ])
    error_message = "The replication role trusts s3.amazonaws.com only (never an app or host role)."
  }

  assert {
    condition = anytrue([
      for s in data.aws_iam_policy_document.assume.statement : anytrue([
        for c in s.condition : c.variable == "aws:SourceArn" && toset(c.values) == toset(["arn:aws:s3:::sos-test-files-111122223333"])
      ])
    ])
    error_message = "...and only for this source bucket (aws:SourceArn)."
  }

  assert {
    condition = alltrue([
      for s in data.aws_iam_policy_document.replication.statement : length(setintersection(toset(s.actions), toset([
        "s3:DeleteObject", "s3:DeleteObjectVersion", "s3:PutObject", "s3:PutLifecycleConfiguration",
        "s3:BypassGovernanceRetention", "s3:PutObjectRetention", "s3:*", "*",
      ]))) == 0
    ])
    error_message = "The replication role can only replicate: no delete, no put, no lock changes."
  }
}

run "nobody_bypasses_the_lock" {
  command = plan

  assert {
    condition = anytrue([
      for s in data.aws_iam_policy_document.replica.statement :
      s.effect == "Deny" && s.actions == toset(["s3:BypassGovernanceRetention"]) && length(s.condition) == 0
    ])
    error_message = "Without named principals, the bucket policy denies every governance bypass."
  }
}

run "retention_covers_the_recovery_window" {
  command = plan

  variables {
    retention_days = 30
  }

  expect_failures = [var.retention_days]
}
