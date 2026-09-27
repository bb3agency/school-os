# SEC-011: separate CMKs with automatic annual rotation; FR-AUD-004: asymmetric audit-signing keys.

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

run "asymmetric_signing_key" {
  command = plan

  variables {
    keys = {
      data          = { description = "data" }
      audit-signing = { description = "audit signing", key_spec = "ECC_NIST_P256", key_usage = "SIGN_VERIFY" }
    }
  }

  assert {
    condition     = aws_kms_key.this["audit-signing"].customer_master_key_spec == "ECC_NIST_P256" && aws_kms_key.this["audit-signing"].key_usage == "SIGN_VERIFY"
    error_message = "Audit archives are signed with an ECC_NIST_P256 SIGN_VERIFY key (ECDSA_SHA_256)."
  }

  assert {
    condition     = !aws_kms_key.this["audit-signing"].enable_key_rotation
    error_message = "AWS KMS cannot rotate asymmetric keys; rotation stays off for them."
  }

  assert {
    condition     = aws_kms_key.this["data"].enable_key_rotation && aws_kms_key.this["data"].key_usage == "ENCRYPT_DECRYPT" && aws_kms_key.this["data"].customer_master_key_spec == "SYMMETRIC_DEFAULT"
    error_message = "Symmetric keys keep annual rotation."
  }

  assert {
    condition     = aws_kms_alias.this["audit-signing"].name == "alias/sos-test-audit-signing"
    error_message = "Signing keys get an alias like every other CMK."
  }
}

run "signing_keys_are_not_shared_with_services" {
  command = plan

  variables {
    keys = {
      bad = { description = "bad", key_spec = "ECC_NIST_P256", key_usage = "SIGN_VERIFY", allow_cloudwatch_logs = true }
    }
  }

  expect_failures = [var.keys]
}

run "symmetric_spec_cannot_sign" {
  command = plan

  variables {
    keys = {
      bad = { description = "bad", key_usage = "SIGN_VERIFY" }
    }
  }

  expect_failures = [var.keys]
}
