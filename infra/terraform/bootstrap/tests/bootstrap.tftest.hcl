# State bucket: versioned, SSE-KMS with a dedicated CMK, all public access blocked (SEC-011).

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
  mock_resource "aws_kms_key" {
    defaults = { arn = "arn:aws:kms:ap-south-1:111122223333:key/00000000-0000-0000-0000-00000000000a" }
  }
  mock_resource "aws_s3_bucket" {
    defaults = { arn = "arn:aws:s3:::sos-tfstate-111122223333" }
  }
}

variables {
  aws_account_id = "111122223333"
  account_label  = "prod"
  owner          = "platform@example.test"
  cost_center    = "schoolos"
}

run "state_bucket_is_locked_down" {
  command = apply

  assert {
    condition     = output.posture.sse == "aws:kms"
    error_message = "State must be encrypted with the state CMK."
  }

  assert {
    condition     = output.posture.versioning == "Enabled"
    error_message = "State bucket must be versioned."
  }

  assert {
    condition     = alltrue(values(output.posture.public_access_block))
    error_message = "State bucket must block all public access."
  }

  assert {
    condition     = output.state_bucket == "sos-tfstate-111122223333"
    error_message = "State bucket name is sos-tfstate-<account>."
  }
}
