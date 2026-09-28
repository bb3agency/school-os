output "region" {
  description = "Region this instance runs in."
  value       = local.region
}

output "detector_id" {
  description = "GuardDuty detector ID."
  value       = aws_guardduty_detector.this.id
}

output "securityhub_enabled" {
  description = "Security Hub account resource ID (depend on it before linking regions)."
  value       = aws_securityhub_account.this.id
}

# Posture (known at plan time; asserted by terraform test in this module, security_baseline and the envs).
output "posture" {
  description = "Detection posture of this region."
  value = {
    guardduty_enabled         = aws_guardduty_detector.this.enable
    guardduty_frequency       = aws_guardduty_detector.this.finding_publishing_frequency
    guardduty_features        = { for k, f in aws_guardduty_detector_feature.this : k => f.status }
    guardduty_export          = length(aws_guardduty_publishing_destination.s3) == 1
    config_recording_all      = one(aws_config_configuration_recorder.this.recording_group).all_supported
    config_global_types       = one(aws_config_configuration_recorder.this.recording_group).include_global_resource_types
    config_recording_enabled  = aws_config_configuration_recorder_status.this.is_enabled
    config_delivery_kms       = aws_config_delivery_channel.this.s3_kms_key_arn
    config_rules              = { for k, r in aws_config_config_rule.managed : k => one(r.source).source_identifier }
    securityhub_standards     = sort([for s in aws_securityhub_standards_subscription.this : s.standards_arn])
    securityhub_default_stds  = aws_securityhub_account.this.enable_default_standards
    forwards_to_primary       = length(aws_cloudwatch_event_target.forward) == 1
    forward_target_event_bus  = try(aws_cloudwatch_event_target.forward[0].arn, null)
    forward_event_pattern_obj = try(jsondecode(aws_cloudwatch_event_rule.forward[0].event_pattern), null)
  }
}

output "securityhub_disabled_controls" {
  description = "Controls disabled per standard with their recorded reason (asserted by tests)."
  value       = { for k, a in aws_securityhub_standards_control_association.disabled : k => { status = a.association_status, reason = a.updated_reason } }
}
