# Alarms, notification topic, platform log groups and budget (docs/11 §3-5, docs/10 §12).
#
# Custom metrics the application must publish (CloudWatch EMF, namespace var.custom_metric_namespace,
# dimension Environment=<env>), consumed here:
#   CeleryQueueOldestTaskAgeSeconds gauge, max over queues (placeholder until the worker exports it)
#
# Security metrics are NOT published by the app: CloudWatch Logs metric filters derive them from the
# services' structured JSON log lines (app/core/logging.py, apps/web/src/server/log.ts), in
# "<security_metric_namespace>/<env>", a namespace no task role may write (docs/07 §15, SEC-007).

locals {
  dims_env    = { Environment = var.environment }
  security_ns = "${var.security_metric_namespace}/${var.environment}"

  # key => log group, filter pattern, metric name. Event names are pinned by
  # apps/api/tests/deploy/test_security_log_events.py, so renaming an event breaks a test, not an alarm.
  security_filters = {
    # One line per tenant chain whose hash chain fails (also after a verify error), P1.
    audit_chain_broken = {
      log_group = var.worker_log_group_name
      pattern   = "{ $.event = \"audit.chain.broken\" }"
      metric    = "AuditChainVerificationFailures"
    }
    # One line per chain checked (verified or broken): zero in a day means the job did not run.
    audit_chain_checked = {
      log_group = var.worker_log_group_name
      pattern   = "{ ($.event = \"audit.chain.verified\") || ($.event = \"audit.chain.broken\") }"
      metric    = "AuditChainsChecked"
    }
    # The API refused a token or service token (expired tokens are refreshed by the BFF first).
    api_auth_failures = {
      log_group = var.api_log_group_name
      pattern   = "{ ($.event = \"http.request\") && ($.status = 401) }"
      metric    = "ApiAuthFailures"
    }
    # SchoolOS support started a break-glass session in a school (ADR-0023).
    breakglass_session = {
      log_group = var.api_log_group_name
      pattern   = "{ ($.event = \"http.request\") && ($.route = \"POST /api/v1/breakglass/support-session\") && ($.status = 200) }"
      metric    = "BreakGlassSessionsStarted"
    }
    # A spent refresh token came back: a stolen or replayed session (FR-IAM-004).
    refresh_token_reuse = {
      log_group = var.web_log_group_name
      pattern   = "{ $.event = \"refresh_token_reuse_detected\" }"
      metric    = "RefreshTokenReuse"
    }
    # Fleet heartbeat with a bad signature, a replayed nonce or a bad body (SEC-028).
    heartbeat_rejected = {
      log_group = var.api_log_group_name
      pattern   = "{ $.event = \"fleet.heartbeat.rejected\" }"
      metric    = "HeartbeatRejected"
    }
    # A valid operator-pool token whose subject is not an active operator (FR-PLT-028).
    operator_denied = {
      log_group = var.api_log_group_name
      pattern   = "{ $.event = \"platform.operator.denied\" }"
      metric    = "OperatorDenied"
    }
    # Rate limits (audit 2026-10-05 P2-07, app/core/ratelimit.py): a 429 from any API layer.
    api_rate_limited = {
      log_group = var.api_log_group_name
      pattern   = "{ $.event = \"security.rate_limited\" }"
      metric    = "ApiRateLimited"
    }
    # Rejected tokens and refused sign-ins counted by the API backoff (ASVS 2.2.1).
    api_auth_failed = {
      log_group = var.api_log_group_name
      pattern   = "{ $.event = \"security.auth.failed\" }"
      metric    = "ApiAuthFailed"
    }
    # The limiter could not reach Valkey: open policies are not enforced (logged at most every 10 s).
    rate_limiter_unavailable = {
      log_group = var.api_log_group_name
      pattern   = "{ $.event = \"security.rate_limit.unavailable\" }"
      metric    = "RateLimiterUnavailable"
    }
    # Refused sign-in and step-up callbacks in the BFF (apps/web/src/server/auth/handlers.ts).
    sign_in_failed = {
      log_group = var.web_log_group_name
      pattern   = "{ ($.event = \"signin_failed\") || ($.event = \"step_up_failed\") }"
      metric    = "SignInFailures"
    }
    # The BFF refused a sign-in start or callback for its per-IP limit or backoff.
    bff_auth_rate_limited = {
      log_group = var.web_log_group_name
      pattern   = "{ $.event = \"auth_rate_limited\" }"
      metric    = "BffAuthRateLimited"
    }
  }

  # key => alarm name suffix, description, period, threshold.
  security_alarms = {
    api_auth_failures = {
      name        = "api-auth-failures"
      description = "SECURITY: many 401 answers from the API (token replay or a stolen service token). docs/07 §15."
      period      = 300
      threshold   = var.api_auth_failures_per_5min
    }
    breakglass_session = {
      name        = "breakglass-session-started"
      description = "SECURITY (notice): a SchoolOS support break-glass session started in a school (ADR-0023). Check it matches an approved request."
      period      = 300
      threshold   = 1
    }
    refresh_token_reuse = {
      name        = "refresh-token-reuse"
      description = "SECURITY: a spent refresh token was presented again; the session family was revoked (FR-IAM-004). Possible session theft."
      period      = 300
      threshold   = 1
    }
    heartbeat_rejected = {
      name        = "heartbeat-rejected"
      description = "SECURITY: fleet heartbeats rejected (signature, replay or schema; SEC-028)."
      period      = 900
      threshold   = var.heartbeat_rejections_per_15min
    }
    api_rate_limited = {
      name        = "api-rate-limited"
      description = "SECURITY: many API requests refused by rate limits (flood, scraping or a runaway client; P2-07)."
      period      = 300
      threshold   = var.rate_limited_per_5min
    }
    api_auth_failed = {
      name        = "api-auth-failed"
      description = "SECURITY: many rejected tokens or refused sign-ins at the API (credential stuffing or token guessing; ASVS 2.2.1)."
      period      = 300
      threshold   = var.auth_failures_per_5min
    }
    rate_limiter_unavailable = {
      name        = "rate-limiter-unavailable"
      description = "SECURITY: the API rate limiter cannot reach Valkey; normal traffic is not limited (sign-in paths fall back to per-task limits)."
      period      = 300
      threshold   = 1
    }
    sign_in_failed = {
      name        = "sign-in-failures"
      description = "SECURITY: a spike of refused sign-in or step-up callbacks in the BFF (P2-07)."
      period      = 300
      threshold   = var.sign_in_failures_per_5min
    }
    bff_auth_rate_limited = {
      name        = "bff-auth-rate-limited"
      description = "SECURITY: the BFF is refusing sign-ins for its per-IP limit or backoff (P2-07)."
      period      = 300
      threshold   = var.rate_limited_per_5min
    }
    operator_denied = {
      name        = "operator-denied"
      description = "SECURITY: an operator-pool sign-in that is not an active operator reached the control plane (FR-PLT-028)."
      period      = 300
      threshold   = 1
    }
  }
}

resource "aws_cloudwatch_log_metric_filter" "security" {
  for_each = local.security_filters

  name           = "${var.name_prefix}-${replace(each.key, "_", "-")}"
  log_group_name = each.value.log_group
  pattern        = each.value.pattern

  metric_transformation {
    name          = each.value.metric
    namespace     = local.security_ns
    value         = "1"
    default_value = "0"
    unit          = "Count"
  }
}

resource "aws_cloudwatch_metric_alarm" "security" {
  for_each = local.security_alarms

  alarm_name          = "${var.name_prefix}-${each.value.name}"
  alarm_description   = each.value.description
  namespace           = local.security_ns
  metric_name         = local.security_filters[each.key].metric
  statistic           = "Sum"
  period              = each.value.period
  evaluation_periods  = 1
  threshold           = each.value.threshold
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alarms.arn]
  tags                = var.tags

  depends_on = [aws_cloudwatch_log_metric_filter.security]
}

resource "aws_sns_topic" "alarms" {
  name              = "${var.name_prefix}-alarms"
  kms_master_key_id = var.kms_key_arn
  tags              = var.tags
}

data "aws_iam_policy_document" "alarms_topic" {
  statement {
    sid       = "AllowCloudWatchAlarms"
    actions   = ["sns:Publish"]
    resources = [aws_sns_topic.alarms.arn]
    principals {
      type        = "Service"
      identifiers = ["cloudwatch.amazonaws.com"]
    }
  }

  statement {
    sid       = "AllowBudgets"
    actions   = ["sns:Publish"]
    resources = [aws_sns_topic.alarms.arn]
    principals {
      type        = "Service"
      identifiers = ["budgets.amazonaws.com"]
    }
  }
}

resource "aws_sns_topic_policy" "alarms" {
  arn    = aws_sns_topic.alarms.arn
  policy = data.aws_iam_policy_document.alarms_topic.json
}

resource "aws_sns_topic_subscription" "email" {
  for_each = toset(var.alarm_emails)

  topic_arn = aws_sns_topic.alarms.arn
  protocol  = "email"
  endpoint  = each.key
}

# Security/application event stream (OTel collector exports here; 400-day retention, CERT-In/DPDP).
resource "aws_cloudwatch_log_group" "security" {
  name              = "/schoolos/${var.name_prefix}/security-events"
  retention_in_days = var.log_retention_days
  kms_key_id        = var.kms_key_arn
  tags              = var.tags
}

resource "aws_cloudwatch_log_group" "otel" {
  name              = "/schoolos/${var.name_prefix}/otel"
  retention_in_days = var.log_retention_days
  kms_key_id        = var.kms_key_arn
  tags              = var.tags
}

# --- Edge ---------------------------------------------------------------------------

resource "aws_cloudwatch_metric_alarm" "http_5xx" {
  count = var.enable_alb_alarms ? 1 : 0

  alarm_name          = "${var.name_prefix}-http-5xx"
  alarm_description   = "5xx responses (ALB + targets) above threshold. Runbook: docs/11 R1."
  comparison_operator = "GreaterThanOrEqualToThreshold"
  threshold           = var.http_5xx_threshold
  evaluation_periods  = 1
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alarms.arn]
  ok_actions          = [aws_sns_topic.alarms.arn]

  metric_query {
    id          = "total"
    expression  = "elb + target"
    label       = "5xx total"
    return_data = true
  }

  metric_query {
    id = "elb"
    metric {
      namespace   = "AWS/ApplicationELB"
      metric_name = "HTTPCode_ELB_5XX_Count"
      dimensions  = { LoadBalancer = var.alb_arn_suffix }
      period      = 300
      stat        = "Sum"
    }
  }

  metric_query {
    id = "target"
    metric {
      namespace   = "AWS/ApplicationELB"
      metric_name = "HTTPCode_Target_5XX_Count"
      dimensions  = { LoadBalancer = var.alb_arn_suffix }
      period      = 300
      stat        = "Sum"
    }
  }

  tags = var.tags
}

resource "aws_cloudwatch_metric_alarm" "latency_p95" {
  count = var.enable_alb_alarms ? 1 : 0

  alarm_name          = "${var.name_prefix}-latency-p95"
  alarm_description   = "Web target p95 response time above ${var.latency_p95_seconds}s for 15 minutes."
  namespace           = "AWS/ApplicationELB"
  metric_name         = "TargetResponseTime"
  dimensions          = { LoadBalancer = var.alb_arn_suffix, TargetGroup = var.target_group_arn_suffix }
  extended_statistic  = "p95"
  period              = 300
  evaluation_periods  = 3
  threshold           = var.latency_p95_seconds
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alarms.arn]
  ok_actions          = [aws_sns_topic.alarms.arn]
  tags                = var.tags
}

# --- Database -------------------------------------------------------------------------

resource "aws_cloudwatch_metric_alarm" "rds_cpu" {
  count = var.enable_rds_alarms ? 1 : 0

  alarm_name          = "${var.name_prefix}-rds-cpu"
  alarm_description   = "RDS CPU above ${var.rds_cpu_percent}% for 15 minutes."
  namespace           = "AWS/RDS"
  metric_name         = "CPUUtilization"
  dimensions          = { DBInstanceIdentifier = var.rds_instance_id }
  statistic           = "Average"
  period              = 300
  evaluation_periods  = 3
  threshold           = var.rds_cpu_percent
  comparison_operator = "GreaterThanThreshold"
  alarm_actions       = [aws_sns_topic.alarms.arn]
  ok_actions          = [aws_sns_topic.alarms.arn]
  tags                = var.tags
}

resource "aws_cloudwatch_metric_alarm" "rds_storage" {
  count = var.enable_rds_alarms ? 1 : 0

  alarm_name          = "${var.name_prefix}-rds-free-storage"
  alarm_description   = "RDS free storage below 20% of allocated."
  namespace           = "AWS/RDS"
  metric_name         = "FreeStorageSpace"
  dimensions          = { DBInstanceIdentifier = var.rds_instance_id }
  statistic           = "Minimum"
  period              = 300
  evaluation_periods  = 1
  threshold           = var.rds_allocated_storage_gb * 1073741824 * 0.2
  comparison_operator = "LessThanThreshold"
  alarm_actions       = [aws_sns_topic.alarms.arn]
  ok_actions          = [aws_sns_topic.alarms.arn]
  tags                = var.tags
}

resource "aws_cloudwatch_metric_alarm" "rds_memory" {
  count = var.enable_rds_alarms ? 1 : 0

  alarm_name          = "${var.name_prefix}-rds-freeable-memory"
  alarm_description   = "RDS freeable memory below 256 MiB."
  namespace           = "AWS/RDS"
  metric_name         = "FreeableMemory"
  dimensions          = { DBInstanceIdentifier = var.rds_instance_id }
  statistic           = "Minimum"
  period              = 300
  evaluation_periods  = 2
  threshold           = 268435456
  comparison_operator = "LessThanThreshold"
  alarm_actions       = [aws_sns_topic.alarms.arn]
  tags                = var.tags
}

# --- Cache ------------------------------------------------------------------------------

resource "aws_cloudwatch_metric_alarm" "redis_memory" {
  count = var.enable_redis_alarms ? 1 : 0

  alarm_name          = "${var.name_prefix}-valkey-memory"
  alarm_description   = "Valkey memory above 80% (broker uses noeviction; full memory stops task enqueueing)."
  namespace           = "AWS/ElastiCache"
  metric_name         = "DatabaseMemoryUsagePercentage"
  dimensions          = { ReplicationGroupId = var.redis_replication_group_id }
  statistic           = "Maximum"
  period              = 300
  evaluation_periods  = 2
  threshold           = 80
  comparison_operator = "GreaterThanThreshold"
  alarm_actions       = [aws_sns_topic.alarms.arn]
  tags                = var.tags
}

# --- Application ---------------------------------------------------------------------------

# Placeholder until the worker exports queue age; missing data does not alarm.
resource "aws_cloudwatch_metric_alarm" "queue_age" {
  alarm_name          = "${var.name_prefix}-queue-oldest-task-age"
  alarm_description   = "Oldest queued Celery task older than ${var.queue_age_seconds}s (placeholder metric)."
  namespace           = var.custom_metric_namespace
  metric_name         = "CeleryQueueOldestTaskAgeSeconds"
  dimensions          = local.dims_env
  statistic           = "Maximum"
  period              = 300
  evaluation_periods  = 2
  threshold           = var.queue_age_seconds
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alarms.arn]
  tags                = var.tags
}

# P1 security: any tenant chain failing verification (docs/11 §4 "Audit chain broken").
resource "aws_cloudwatch_metric_alarm" "audit_chain_failure" {
  alarm_name          = "${var.name_prefix}-audit-chain-verification-failed"
  alarm_description   = "P1 SECURITY: audit hash-chain verification failed for at least one tenant (SEC-007). Runbook R5."
  namespace           = local.security_ns
  metric_name         = local.security_filters.audit_chain_broken.metric
  statistic           = "Sum"
  period              = 3600
  evaluation_periods  = 1
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alarms.arn]
  ok_actions          = [aws_sns_topic.alarms.arn]
  tags                = var.tags
}

# The daily verification must actually run: no run recorded in 26 hours alarms (missing = breaching).
resource "aws_cloudwatch_metric_alarm" "audit_chain_not_run" {
  alarm_name          = "${var.name_prefix}-audit-chain-verification-missing"
  alarm_description   = "Daily audit chain verification has not checked any chain in the last day (SLO: 100% daily)."
  namespace           = local.security_ns
  metric_name         = local.security_filters.audit_chain_checked.metric
  statistic           = "Sum"
  period              = 86400
  evaluation_periods  = 1
  threshold           = 1
  comparison_operator = "LessThanThreshold"
  treat_missing_data  = "breaching"
  alarm_actions       = [aws_sns_topic.alarms.arn]
  ok_actions          = [aws_sns_topic.alarms.arn]
  tags                = var.tags
}

# --- Cost ------------------------------------------------------------------------------------

resource "aws_budgets_budget" "monthly" {
  count = var.monthly_budget_usd == null ? 0 : 1

  name         = "${var.name_prefix}-monthly"
  budget_type  = "COST"
  limit_amount = tostring(var.monthly_budget_usd)
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  dynamic "notification" {
    for_each = [50, 80, 100]
    content {
      comparison_operator       = "GREATER_THAN"
      threshold                 = notification.value
      threshold_type            = "PERCENTAGE"
      notification_type         = "ACTUAL"
      subscriber_sns_topic_arns = [aws_sns_topic.alarms.arn]
    }
  }

  tags = var.tags
}
