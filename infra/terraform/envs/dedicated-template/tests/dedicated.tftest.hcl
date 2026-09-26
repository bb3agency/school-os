# Dedicated-tier invariants for one school (SEC-009, SEC-011, SEC-030, NFR-AVL-002, NFR-PRV-001).

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
  mock_data "aws_subnet" {
    defaults = { availability_zone = "ap-south-1a" }
  }
  mock_resource "aws_kms_key" {
    defaults = { arn = "arn:aws:kms:ap-south-1:111122223333:key/00000000-0000-0000-0000-00000000000a" }
  }
  mock_resource "aws_lambda_function" {
    defaults = { arn = "arn:aws:lambda:ap-south-1:111122223333:function:mock" }
  }
  mock_resource "aws_cognito_user_pool" {
    defaults = { arn = "arn:aws:cognito-idp:ap-south-1:111122223333:userpool/ap-south-1_mock", id = "ap-south-1_mock" }
  }
  mock_resource "aws_iam_role" {
    defaults = { arn = "arn:aws:iam::111122223333:role/mock" }
  }
  mock_resource "aws_cloudwatch_log_group" {
    defaults = { arn = "arn:aws:logs:ap-south-1:111122223333:log-group:mock" }
  }
  mock_resource "aws_s3_bucket" {
    defaults = { arn = "arn:aws:s3:::mock-bucket" }
  }
  mock_resource "aws_secretsmanager_secret" {
    defaults = { arn = "arn:aws:secretsmanager:ap-south-1:111122223333:secret:mock-AbCdEf" }
  }
}

mock_provider "aws" {
  alias = "backup"
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
  mock_resource "aws_kms_key" {
    defaults = { arn = "arn:aws:kms:ap-south-2:111122223333:key/00000000-0000-0000-0000-00000000000a" }
  }
  mock_resource "aws_iam_role" {
    defaults = { arn = "arn:aws:iam::111122223333:role/mock" }
  }
  mock_resource "aws_cloudwatch_log_group" {
    defaults = { arn = "arn:aws:logs:ap-south-2:111122223333:log-group:mock" }
  }
  mock_resource "aws_s3_bucket" {
    defaults = { arn = "arn:aws:s3:::mock-bucket" }
  }
  mock_resource "aws_secretsmanager_secret" {
    defaults = { arn = "arn:aws:secretsmanager:ap-south-2:111122223333:secret:mock-AbCdEf" }
  }
}

variables {
  aws_account_id        = "111122223333"
  owner                 = "platform@example.test"
  cost_center           = "school-demo"
  school_code           = "demo-school"
  deployment_id         = "01923f4e-5b6c-7d8e-9f00-112233445566"
  tenant_id             = "01923f4e-5b6c-7d8e-9f00-aabbccddeeff"
  domain                = "demo-school.example.test"
  custom_domain         = "office.demo-school.example.test"
  acme_email            = "ops@example.test"
  release_version       = "2026.10.1"
  artifacts_bucket      = "sos-prod-artifacts-111122223333"
  artifacts_kms_key_arn = "arn:aws:kms:ap-south-1:111122223333:key/00000000-0000-0000-0000-000000000001"
  control_plane_url     = "https://app.example.test"
}

run "host_is_hardened" {
  command = plan

  assert {
    condition     = module.host.posture.imdsv2_required
    error_message = "IMDSv2 must be required on the dedicated host."
  }

  assert {
    condition     = module.host.posture.root_encrypted && module.host.posture.data_encrypted
    error_message = "Root and data volumes must be encrypted with the school CMK (SEC-011)."
  }

  assert {
    condition     = !module.host.posture.public_ip_on_launch
    error_message = "Public access only through the Elastic IP (no auto-assigned IPs)."
  }
}

run "school_buckets_private_and_encrypted" {
  command = apply

  assert {
    condition     = module.host.posture.files_bucket_sse == "aws:kms" && alltrue(values(module.host.posture.files_bucket_private))
    error_message = "Per-school files bucket must be private and SSE-KMS."
  }

  assert {
    condition     = module.backup_bucket.sse_algorithm == "aws:kms" && alltrue(values(module.backup_bucket.public_access_block))
    error_message = "Backup bucket must be private and SSE-KMS."
  }

  assert {
    condition     = module.backup_bucket.object_lock_mode == "GOVERNANCE" && module.backup_bucket.object_lock_days == 30
    error_message = "Backups are protected by Object Lock (GOVERNANCE, 30 days) by default."
  }
}

run "school_code_is_validated" {
  command = plan

  variables {
    school_code = "Bad_Code!"
  }

  expect_failures = [var.school_code]
}

run "backup_region_guard" {
  command = plan

  variables {
    backup_region = "us-west-2"
  }

  expect_failures = [var.backup_region]
}
