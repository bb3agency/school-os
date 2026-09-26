# SEC-011: separate CMKs with automatic annual rotation.

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
    defaults = { region = "ap-south-1" }
  }
}

variables {
  name_prefix = "sos-test"
  keys = {
    data   = { description = "data" }
    audit  = { description = "audit" }
    backup = { description = "backup" }
    logs   = { description = "logs", allow_cloudwatch_logs = true }
  }
}

run "separate_rotating_keys" {
  command = plan

  assert {
    condition     = length(aws_kms_key.this) == 4
    error_message = "One CMK per purpose."
  }

  assert {
    condition     = alltrue([for k in aws_kms_key.this : k.enable_key_rotation && k.rotation_period_in_days == 365])
    error_message = "Every CMK rotates annually."
  }

  assert {
    condition     = alltrue([for k in aws_kms_key.this : k.deletion_window_in_days == 30 && !k.multi_region])
    error_message = "30-day deletion window (crypto-shred grace) and single-region keys."
  }

  assert {
    condition     = aws_kms_alias.this["audit"].name == "alias/sos-test-audit"
    error_message = "Aliases follow alias/<prefix>-<purpose>."
  }
}
