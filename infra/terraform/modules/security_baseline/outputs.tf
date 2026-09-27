output "trail_arn" {
  description = "CloudTrail trail ARN."
  value       = aws_cloudtrail.this.arn
}

output "log_buckets" {
  description = "CloudTrail (Object Lock) and security-evidence bucket names."
  value = {
    cloudtrail = module.trail_bucket.id
    evidence   = module.evidence_bucket.id
  }
}

output "kms_key_arn" {
  description = "CMK for CloudTrail logs, security evidence and the alert topic (grant kms:Decrypt to log readers via IAM)."
  value       = aws_kms_key.security.arn
}

output "alert_topic_arn" {
  description = "SNS topic for security alerts (subscribe further on-call endpoints here)."
  value       = aws_sns_topic.alerts.arn
}

# Posture (known at plan time; asserted by terraform test here and in envs/staging, envs/prod).
output "posture" {
  description = "SEC-023 posture of this account."
  value = {
    trail = {
      name                  = aws_cloudtrail.this.name
      multi_region          = aws_cloudtrail.this.is_multi_region_trail
      global_service_events = aws_cloudtrail.this.include_global_service_events
      log_file_validation   = aws_cloudtrail.this.enable_log_file_validation
      logging               = aws_cloudtrail.this.enable_logging
      kms_key_arn           = aws_cloudtrail.this.kms_key_id
      bucket                = aws_cloudtrail.this.s3_bucket_name
      management_events     = anytrue([for s in aws_cloudtrail.this.advanced_event_selector : anytrue([for f in s.field_selector : f.field == "eventCategory" && contains(f.equals, "Management")])])
      management_read_only_filter = anytrue([
        for s in aws_cloudtrail.this.advanced_event_selector : anytrue([for f in s.field_selector : f.field == "readOnly"])
      ])
      s3_data_event_arn_prefixes = sort(flatten([
        for s in aws_cloudtrail.this.advanced_event_selector : [
          for f in s.field_selector : f.starts_with if f.field == "resources.ARN"
        ]
      ]))
    }
    trail_bucket = {
      object_lock_enabled = module.trail_bucket.object_lock_enabled
      object_lock_mode    = module.trail_bucket.object_lock_mode
      object_lock_days    = module.trail_bucket.object_lock_days
      sse_algorithm       = module.trail_bucket.sse_algorithm
      kms_key_arn         = module.trail_bucket.kms_key_arn
      versioning          = module.trail_bucket.versioning_status
      public_access_block = module.trail_bucket.public_access_block
      access_log_bucket   = var.access_log_bucket
    }
    evidence_bucket = {
      sse_algorithm       = module.evidence_bucket.sse_algorithm
      kms_key_arn         = module.evidence_bucket.kms_key_arn
      versioning          = module.evidence_bucket.versioning_status
      public_access_block = module.evidence_bucket.public_access_block
    }
    log_retention_days         = var.log_retention_days
    log_bucket_deny_actions    = local.log_bucket_deny_actions
    delete_exempt_principals   = var.delete_exempt_principal_arns
    kms_rotation               = aws_kms_key.security.enable_key_rotation
    detection_primary          = module.detection_primary.posture
    detection_dr               = module.detection_dr.posture
    securityhub_linked_regions = aws_securityhub_finding_aggregator.this.specified_regions
    account_public_access_block = length(aws_s3_account_public_access_block.this) == 1 && alltrue([
      for b in aws_s3_account_public_access_block.this : b.block_public_acls && b.block_public_policy && b.ignore_public_acls && b.restrict_public_buckets
    ])
    ebs_encryption_by_default = length(aws_ebs_encryption_by_default.primary) == 1 && length(aws_ebs_encryption_by_default.dr) == 1
    alert_topic_kms_key       = aws_sns_topic.alerts.kms_master_key_id
    alert_subscriptions       = sort([for s in aws_sns_topic_subscription.alerts_email : s.endpoint])
    alert_rules               = { for k, r in aws_cloudwatch_event_rule.alert : k => jsondecode(r.event_pattern) }
    alert_targets             = { for k, t in aws_cloudwatch_event_target.alert : k => t.arn }
    tamper_event_names        = local.tamper_event_names
  }
}
