# Alarms required by docs/11: 5xx, latency, RDS CPU/storage, queue age, audit chain verification.

mock_provider "aws" {
  mock_data "aws_iam_policy_document" {
    defaults = { json = "{\"Version\":\"2012-10-17\",\"Statement\":[]}" }
  }
}

variables {
  name_prefix                = "sos-test"
  environment                = "prod"
  kms_key_arn                = "arn:aws:kms:ap-south-1:111122223333:key/00000000-0000-0000-0000-000000000001"
  alarm_emails               = ["oncall@example.test"]
  alb_arn_suffix             = "app/sos-test-alb/0123456789abcdef"
  target_group_arn_suffix    = "targetgroup/sos-test-web/0123456789abcdef"
  rds_instance_id            = "sos-test-pg"
  redis_replication_group_id = "sos-test-valkey"
  monthly_budget_usd         = 100
}

run "required_alarms_exist" {
  command = plan

  assert {
    condition = length(setsubtract(toset([
      "sos-test-http-5xx", "sos-test-latency-p95", "sos-test-rds-cpu", "sos-test-rds-free-storage",
      "sos-test-queue-oldest-task-age", "sos-test-audit-chain-verification-failed", "sos-test-audit-chain-verification-missing",
    ]), toset(output.alarm_names))) == 0
    error_message = "Missing a required alarm."
  }

  assert {
    condition     = aws_cloudwatch_metric_alarm.audit_chain_not_run.treat_missing_data == "breaching"
    error_message = "A missing daily audit verification run must alarm."
  }

  assert {
    condition     = aws_sns_topic.alarms.kms_master_key_id == var.kms_key_arn
    error_message = "Alarm topic encrypted."
  }
}

run "log_groups_retained_400_days" {
  command = plan

  assert {
    condition     = aws_cloudwatch_log_group.security.retention_in_days == 400 && aws_cloudwatch_log_group.otel.retention_in_days == 400
    error_message = "Log retention 400 days (CERT-In 180 d, DPDP 1 y)."
  }

  assert {
    condition     = length(aws_budgets_budget.monthly) == 1
    error_message = "Budget alerts configured when a limit is given."
  }
}
