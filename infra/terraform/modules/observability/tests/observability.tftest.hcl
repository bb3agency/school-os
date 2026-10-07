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
  api_log_group_name         = "/schoolos/ecs/sos-test-api"
  worker_log_group_name      = "/schoolos/ecs/sos-test-worker"
  web_log_group_name         = "/schoolos/ecs/sos-test-web"
}

# SEC-007, docs/07 §15: nothing in the application publishes AuditChainVerification* by EMF, so the
# P1 alarm could never fire and the "missing run" alarm fired every day. The metrics now come from
# metric filters on the worker's structured log lines (audit.chain.broken / audit.chain.verified),
# in a namespace no task role may write (the api/worker may PutMetricData only in "SchoolOS").
run "audit_chain_alarms_are_fed_by_the_worker_logs" {
  command = plan

  assert {
    condition = (
      aws_cloudwatch_log_metric_filter.security["audit_chain_broken"].log_group_name == var.worker_log_group_name
      && aws_cloudwatch_log_metric_filter.security["audit_chain_broken"].pattern == "{ $.event = \"audit.chain.broken\" }"
      && aws_cloudwatch_log_metric_filter.security["audit_chain_broken"].metric_transformation[0].name == aws_cloudwatch_metric_alarm.audit_chain_failure.metric_name
      && aws_cloudwatch_log_metric_filter.security["audit_chain_broken"].metric_transformation[0].namespace == aws_cloudwatch_metric_alarm.audit_chain_failure.namespace
    )
    error_message = "The audit-chain P1 alarm must read a metric that the worker's audit.chain.broken log line produces."
  }

  assert {
    condition = (
      aws_cloudwatch_log_metric_filter.security["audit_chain_checked"].log_group_name == var.worker_log_group_name
      && aws_cloudwatch_log_metric_filter.security["audit_chain_checked"].metric_transformation[0].name == aws_cloudwatch_metric_alarm.audit_chain_not_run.metric_name
      && aws_cloudwatch_log_metric_filter.security["audit_chain_checked"].metric_transformation[0].namespace == aws_cloudwatch_metric_alarm.audit_chain_not_run.namespace
    )
    error_message = "The missing-run alarm must read a metric that every verified or broken chain produces."
  }

  assert {
    condition     = aws_cloudwatch_metric_alarm.audit_chain_failure.namespace != var.custom_metric_namespace
    error_message = "Security metrics must live outside the namespace the app task roles may write (no spoofing)."
  }
}

run "security_events_alarm" {
  command = plan

  assert {
    condition = length(setsubtract(toset([
      "sos-test-api-auth-failures", "sos-test-breakglass-session-started", "sos-test-refresh-token-reuse",
      "sos-test-heartbeat-rejected", "sos-test-operator-denied",
    ]), toset(output.alarm_names))) == 0
    error_message = "Missing a security-event alarm (docs/07 §15)."
  }

  # P2-07: rate-limit trips, failed sign-ins and a limiter outage alarm.
  assert {
    condition = length(setsubtract(toset([
      "sos-test-api-rate-limited", "sos-test-api-auth-failed", "sos-test-rate-limiter-unavailable",
      "sos-test-sign-in-failures", "sos-test-bff-auth-rate-limited",
    ]), toset(output.alarm_names))) == 0
    error_message = "Missing a rate-limit or failed sign-in alarm (P2-07)."
  }

  assert {
    condition = (
      aws_cloudwatch_log_metric_filter.security["sign_in_failed"].log_group_name == var.web_log_group_name
      && aws_cloudwatch_log_metric_filter.security["bff_auth_rate_limited"].log_group_name == var.web_log_group_name
      && aws_cloudwatch_log_metric_filter.security["api_rate_limited"].log_group_name == var.api_log_group_name
      && aws_cloudwatch_log_metric_filter.security["api_auth_failed"].pattern == "{ $.event = \"security.auth.failed\" }"
      && aws_cloudwatch_metric_alarm.security["rate_limiter_unavailable"].threshold == 1
    )
    error_message = "Rate-limit and sign-in filters read the service that writes each event (P2-07)."
  }

  assert {
    condition = (
      aws_cloudwatch_log_metric_filter.security["refresh_token_reuse"].log_group_name == var.web_log_group_name
      && aws_cloudwatch_log_metric_filter.security["breakglass_session"].log_group_name == var.api_log_group_name
      && aws_cloudwatch_log_metric_filter.security["api_auth_failures"].pattern == "{ ($.event = \"http.request\") && ($.status = 401) }"
    )
    error_message = "Each security filter reads the log group of the service that writes the event."
  }
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

# Audit 2026-10-05 hardening (confused deputy): CloudWatch and Budgets may publish to the alarm topic
# only for this account's alarms and budgets.
run "alarm_topic_accepts_only_this_account" {
  command = plan

  assert {
    condition = length(data.aws_iam_policy_document.alarms_topic.statement) == 2 && alltrue([
      for s in data.aws_iam_policy_document.alarms_topic.statement :
      anytrue([for c in s.condition : c.test == "StringEquals" && c.variable == "aws:SourceAccount" && toset(c.values) == toset([data.aws_caller_identity.current.account_id])])
    ])
    error_message = "Every service statement on the alarm topic pins aws:SourceAccount (confused deputy)."
  }
}
