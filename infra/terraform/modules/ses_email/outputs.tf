output "identity_arn" {
  description = "SES domain identity ARN (resource of ses:SendEmail)."
  value       = aws_sesv2_email_identity.domain.arn
}

output "configuration_set_name" {
  description = "Configuration set name (SOS_EMAIL_SES_CONFIGURATION_SET)."
  value       = aws_sesv2_configuration_set.this.configuration_set_name
}

output "configuration_set_arn" {
  description = "Configuration set ARN (resource of ses:SendEmail)."
  value       = aws_sesv2_configuration_set.this.arn
}

output "dkim_records" {
  description = "Easy DKIM CNAMEs to publish in the domain's DNS (created in Route 53 when route53_zone_id is set)."
  value = [for t in local.dkim_tokens : {
    name  = "${t}._domainkey.${var.domain}"
    type  = "CNAME"
    value = "${t}.dkim.amazonses.com"
  }]
}

output "posture" {
  description = "Security posture summary (asserted by tests)."
  value = {
    dkim_key_length         = nonsensitive(one(aws_sesv2_email_identity.domain.dkim_signing_attributes).next_signing_key_length)
    default_config_set      = aws_sesv2_email_identity.domain.configuration_set_name
    tls_policy              = one(aws_sesv2_configuration_set.this.delivery_options).tls_policy
    reputation_metrics      = one(aws_sesv2_configuration_set.this.reputation_options).reputation_metrics_enabled
    suppressed_reasons      = one(aws_sesv2_configuration_set.this.suppression_options).suppressed_reasons
    dkim_records_in_route53 = length(aws_route53_record.dkim)
    reputation_alarms       = sort(keys(aws_cloudwatch_metric_alarm.reputation))
  }
}
