# docs/10 §6: immutable tags, scan on push, KMS encryption for api/worker/web.

mock_provider "aws" {
  mock_data "aws_iam_policy_document" {
    defaults = { json = "{\"Version\":\"2012-10-17\",\"Statement\":[]}" }
  }
}

variables {
  kms_key_arn = "arn:aws:kms:ap-south-1:111122223333:key/00000000-0000-0000-0000-000000000001"
}

run "repositories" {
  command = plan

  assert {
    condition     = toset(keys(aws_ecr_repository.this)) == toset(["api", "worker", "web"])
    error_message = "Repositories api, worker, web."
  }

  assert {
    condition     = alltrue([for r in values(output.posture) : r.immutable && r.scan_on_push && r.encryption == "KMS"])
    error_message = "Immutable, scanned on push, KMS-encrypted."
  }

  assert {
    condition     = aws_ecr_repository.this["api"].name == "schoolos/api"
    error_message = "Repository names schoolos/<service>."
  }
}
