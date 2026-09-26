# SEC-009: every secret is KMS-encrypted and written write-only (values never in state).

mock_provider "aws" {}

variables {
  name_prefix = "sos/test"
  kms_key_arn = "arn:aws:kms:ap-south-1:111122223333:key/00000000-0000-0000-0000-000000000001"
  db = {
    host = "db.example.internal"
    port = 5432
    name = "schoolos"
  }
  random_secrets = {
    service_token_key = { description = "service token key" }
  }
  operator_secrets = {
    anthropic_api_key = "Anthropic API key"
  }
}

run "db_roles_match_bootstrap_sql" {
  command = plan

  assert {
    condition     = toset([for s in aws_secretsmanager_secret.db : s.tags["db_role"]]) == toset(["sos_app", "sos_migrator", "sos_platform", "sos_readonly"])
    error_message = "Secrets exist for every login role in infra/db/bootstrap.sql."
  }

  assert {
    condition     = aws_secretsmanager_secret.db["app"].name == "sos/test/db/app"
    error_message = "Secret naming: <prefix>/db/<role>."
  }
}

run "values_never_in_state" {
  command = plan

  assert {
    condition = alltrue(concat(
      [for v in aws_secretsmanager_secret_version.db : v.secret_string == null],
      [for v in aws_secretsmanager_secret_version.generic : v.secret_string == null],
      [for v in aws_secretsmanager_secret_version.operator : v.secret_string == null],
    ))
    error_message = "Secret values must only be written via secret_string_wo."
  }

  assert {
    condition = alltrue(concat(
      [for s in aws_secretsmanager_secret.db : s.kms_key_id == var.kms_key_arn],
      [for s in aws_secretsmanager_secret.generic : s.kms_key_id == var.kms_key_arn],
      [for s in aws_secretsmanager_secret.operator : s.kms_key_id == var.kms_key_arn],
    ))
    error_message = "All secrets encrypted with the CMK."
  }
}
