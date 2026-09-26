# Alarms, notification topic, platform log groups and budget (docs/11 §3-5, docs/10 §12).
#
# Custom metrics the application must publish (CloudWatch EMF, namespace var.custom_metric_namespace,
# dimension Environment=<env>), consumed here:
#   AuditChainVerificationRuns      count, +1 per completed daily verification run (all tenants)
#   AuditChainVerificationFailures  count, +1 per tenant whose chain fails verification (P1 security)
#   CeleryQueueOldestTaskAgeSeconds gauge, max over queues (placeholder until the worker exports it)

locals {
  dims_env = { Environment = var.environment }
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
  namespace           = var.custom_metric_namespace
  metric_name         = "AuditChainVerificationFailures"
  dimensions          = local.dims_env
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
  alarm_description   = "Daily audit chain verification has not reported a run in the last day (SLO: 100% daily)."
  namespace           = var.custom_metric_namespace
  metric_name         = "AuditChainVerificationRuns"
  dimensions          = local.dims_env
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
