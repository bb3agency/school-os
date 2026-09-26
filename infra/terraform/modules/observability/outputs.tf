output "alarm_topic_arn" {
  description = "SNS topic receiving alarms."
  value       = aws_sns_topic.alarms.arn
}

output "security_log_group_name" {
  description = "Security events log group."
  value       = aws_cloudwatch_log_group.security.name
}

output "otel_log_group_name" {
  description = "OTel collector log group."
  value       = aws_cloudwatch_log_group.otel.name
}

output "alarm_names" {
  description = "All alarm names."
  value = concat(
    aws_cloudwatch_metric_alarm.http_5xx[*].alarm_name,
    aws_cloudwatch_metric_alarm.latency_p95[*].alarm_name,
    aws_cloudwatch_metric_alarm.rds_cpu[*].alarm_name,
    aws_cloudwatch_metric_alarm.rds_storage[*].alarm_name,
    aws_cloudwatch_metric_alarm.rds_memory[*].alarm_name,
    aws_cloudwatch_metric_alarm.redis_memory[*].alarm_name,
    [
      aws_cloudwatch_metric_alarm.queue_age.alarm_name,
      aws_cloudwatch_metric_alarm.audit_chain_failure.alarm_name,
      aws_cloudwatch_metric_alarm.audit_chain_not_run.alarm_name,
    ],
  )
}
