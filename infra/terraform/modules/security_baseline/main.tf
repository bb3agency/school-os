# Account security baseline for one SchoolOS AWS account (SEC-023, pilot-ready gate; docs/07 §13,
# docs/10 §5.1, docs/11 §6-7). Used by envs/staging and envs/prod only, never local/ci.
#
# Stage 0 account model (docs/10 §2): there is no log-archive or security account yet, so each
# workload account (staging, prod) runs its own multi-region account trail into a dedicated,
# Object Lock log bucket in ap-south-1. At Stage 1 an organization trail in the management account
# delivers to the log-archive account and GuardDuty/Security Hub get a delegated administrator in
# the security account; this module's per-account trail can then be retired (docs/10 §5.1).
#
#   CloudTrail   multi-region trail: management events (read + write) and S3 object-level events for
#                the buckets holding school data; log file validation; SSE-KMS (own CMK)
#   Log archive  <prefix>-cloudtrail-<account>: Object Lock (COMPLIANCE in prod), deny-delete policy,
#                versioned, TLS-only, Block Public Access, server access logs to the logs bucket
#   Evidence     <prefix>-security-evidence-<account>: AWS Config snapshots/history and exported
#                GuardDuty findings; SSE-KMS, deny-delete policy, same retention
#   Detection    GuardDuty, Config, Security Hub (FSBP + CIS) in ap-south-1 and ap-south-2
#                (modules/security_detection); Security Hub aggregates ap-south-2 into ap-south-1
#   Alerting     EventBridge rules in ap-south-1 -> encrypted SNS topic -> on-call (P1 security, R5)

data "aws_caller_identity" "current" {}
data "aws_partition" "current" {}
data "aws_region" "current" {}

locals {
  account_id = data.aws_caller_identity.current.account_id
  partition  = data.aws_partition.current.partition
  region     = data.aws_region.current.region
  dr_region  = var.dr_region

  trail_name      = "${var.name_prefix}-trail"
  trail_arn       = "arn:${local.partition}:cloudtrail:${local.region}:${local.account_id}:trail/${local.trail_name}"
  trail_bucket    = "${var.name_prefix}-cloudtrail-${local.account_id}"
  evidence_bucket = "${var.name_prefix}-security-evidence-${local.account_id}"
  trail_prefix    = "cloudtrail"
  config_prefix   = "config"
  findings_prefix = "guardduty"

  # Object-level (data) events: every object in the listed buckets, plus every bucket whose name starts
  # with a listed prefix. The log buckets themselves are never included (they would log their own writes).
  data_event_arn_prefixes = concat(
    [for a in var.data_event_bucket_arns : "${a}/"],
    [for p in var.data_event_bucket_name_prefixes : "arn:${local.partition}:s3:::${p}"],
  )

  # Nobody (root included, unless exempted) may delete log objects or bypass governance retention.
  # S3 Lifecycle expiry is not subject to bucket policies, so retention still ends on schedule.
  log_bucket_deny_actions = [
    "s3:DeleteObject",
    "s3:DeleteObjectVersion",
    "s3:BypassGovernanceRetention",
    "s3:DeleteBucket",
  ]

  # API calls that switch off or blind logging and detection (alerted in every region).
  tamper_event_sources = [
    "cloudtrail.amazonaws.com",
    "guardduty.amazonaws.com",
    "config.amazonaws.com",
    "securityhub.amazonaws.com",
    "kms.amazonaws.com",
    "s3.amazonaws.com",
    "ec2.amazonaws.com",
  ]
  tamper_event_names = [
    # CloudTrail
    "StopLogging", "DeleteTrail", "UpdateTrail", "PutEventSelectors",
    # GuardDuty (incl. suppression filters and trusted IP lists that hide findings)
    "DeleteDetector", "UpdateDetector", "UpdateDetectorFeatureConfiguration", "DeletePublishingDestination", "UpdatePublishingDestination",
    "DisassociateFromMasterAccount", "DisassociateFromAdministratorAccount", "CreateFilter", "UpdateFilter", "CreateIPSet", "UpdateIPSet",
    # AWS Config
    "StopConfigurationRecorder", "DeleteConfigurationRecorder", "DeleteDeliveryChannel", "PutDeliveryChannel", "DeleteConfigRule",
    # Security Hub
    "DisableSecurityHub", "BatchDisableStandards", "UpdateStandardsControl", "BatchUpdateStandardsControlAssociations",
    "DeleteFindingAggregator", "UpdateFindingAggregator", "DisableImportFindingsForProduct",
    # KMS, S3 account guardrails, EBS default encryption
    "DisableKey", "ScheduleKeyDeletion", "DeleteAccountPublicAccessBlock", "PutAccountPublicAccessBlock", "DisableEbsEncryptionByDefault",
  ]

  log_bucket_tamper_event_names = [
    "PutBucketPolicy", "DeleteBucketPolicy", "PutBucketLifecycle", "DeleteBucketLifecycle", "PutBucketObjectLockConfiguration",
    "PutBucketVersioning", "PutBucketPublicAccessBlock", "DeleteBucketPublicAccessBlock", "PutBucketLogging", "PutBucketEncryption",
    "DeleteBucketEncryption", "PutObjectRetention", "PutObjectLegalHold",
  ]

  primary_bus_arn = "arn:${local.partition}:events:${local.region}:${local.account_id}:event-bus/default"
  tags            = merge(var.tags, { control = "SEC-023" })
}

# --- CMK for CloudTrail, evidence, alert topic --------------------------------------------------

data "aws_iam_policy_document" "key" {
  statement {
    sid       = "EnableIAMPolicies"
    actions   = ["kms:*"]
    resources = ["*"]
    principals {
      type        = "AWS"
      identifiers = ["arn:${local.partition}:iam::${local.account_id}:root"]
    }
  }

  statement {
    sid       = "CloudTrailEncryptLogs"
    actions   = ["kms:GenerateDataKey*"]
    resources = ["*"]
    principals {
      type        = "Service"
      identifiers = ["cloudtrail.amazonaws.com"]
    }
    condition {
      test     = "StringLike"
      variable = "kms:EncryptionContext:aws:cloudtrail:arn"
      values   = ["arn:${local.partition}:cloudtrail:*:${local.account_id}:trail/*"]
    }
    condition {
      test     = "StringEquals"
      variable = "aws:SourceArn"
      values   = [local.trail_arn]
    }
  }

  statement {
    sid       = "CloudTrailDescribeKey"
    actions   = ["kms:DescribeKey"]
    resources = ["*"]
    principals {
      type        = "Service"
      identifiers = ["cloudtrail.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "aws:SourceArn"
      values   = [local.trail_arn]
    }
  }

  statement {
    sid       = "GuardDutyExportFindings"
    actions   = ["kms:GenerateDataKey"]
    resources = ["*"]
    principals {
      type        = "Service"
      identifiers = ["guardduty.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [local.account_id]
    }
    condition {
      test     = "StringLike"
      variable = "aws:SourceArn"
      values   = ["arn:${local.partition}:guardduty:*:${local.account_id}:detector/*"]
    }
  }

  statement {
    sid       = "EventBridgePublishToEncryptedTopic"
    actions   = ["kms:GenerateDataKey*", "kms:Decrypt"]
    resources = ["*"]
    principals {
      type        = "Service"
      identifiers = ["events.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [local.account_id]
    }
  }
}

resource "aws_kms_key" "security" {
  description             = "SchoolOS ${var.env}: CloudTrail logs, security evidence, security alert topic (SEC-023)"
  enable_key_rotation     = true
  rotation_period_in_days = 365
  deletion_window_in_days = var.kms_deletion_window_in_days
  multi_region            = false
  policy                  = data.aws_iam_policy_document.key.json
  tags                    = merge(local.tags, { key_purpose = "security-logs" })
}

resource "aws_kms_alias" "security" {
  name          = "alias/${var.name_prefix}-security-logs"
  target_key_id = aws_kms_key.security.key_id
}

# --- CloudTrail log archive bucket (Object Lock) -------------------------------------------------

data "aws_iam_policy_document" "trail_bucket" {
  statement {
    sid       = "CloudTrailAclCheck"
    actions   = ["s3:GetBucketAcl"]
    resources = ["arn:${local.partition}:s3:::${local.trail_bucket}"]
    principals {
      type        = "Service"
      identifiers = ["cloudtrail.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "aws:SourceArn"
      values   = [local.trail_arn]
    }
  }

  statement {
    sid       = "CloudTrailWrite"
    actions   = ["s3:PutObject"]
    resources = ["arn:${local.partition}:s3:::${local.trail_bucket}/${local.trail_prefix}/AWSLogs/${local.account_id}/*"]
    principals {
      type        = "Service"
      identifiers = ["cloudtrail.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "s3:x-amz-acl"
      values   = ["bucket-owner-full-control"]
    }
    condition {
      test     = "StringEquals"
      variable = "aws:SourceArn"
      values   = [local.trail_arn]
    }
  }

  statement {
    sid     = "DenyLogDeletion"
    effect  = "Deny"
    actions = local.log_bucket_deny_actions
    resources = [
      "arn:${local.partition}:s3:::${local.trail_bucket}",
      "arn:${local.partition}:s3:::${local.trail_bucket}/*",
    ]
    principals {
      type        = "*"
      identifiers = ["*"]
    }
    dynamic "condition" {
      for_each = length(var.delete_exempt_principal_arns) > 0 ? [1] : []
      content {
        test     = "ArnNotLike"
        variable = "aws:PrincipalArn"
        values   = var.delete_exempt_principal_arns
      }
    }
  }
}

module "trail_bucket" {
  source = "../s3_bucket"

  name                   = local.trail_bucket
  kms_key_arn            = aws_kms_key.security.arn
  access_logging_enabled = true
  access_log_bucket      = var.access_log_bucket
  force_destroy          = false
  object_lock = {
    mode = var.object_lock_mode
    days = var.log_retention_days
  }
  additional_policy_json = data.aws_iam_policy_document.trail_bucket.json
  lifecycle_rules = [
    {
      # Locked versions cannot be removed before their retain-until date; after it, lifecycle deletes them.
      id                                 = "expire-after-retention"
      expiration_days                    = var.log_retention_days
      noncurrent_version_expiration_days = 1
      abort_incomplete_multipart_days    = 7
    },
  ]
  tags = merge(local.tags, { data_class = "C1-security-logs" })
}

# --- Security evidence bucket (Config, GuardDuty findings) ---------------------------------------

data "aws_iam_policy_document" "evidence_bucket" {
  statement {
    sid       = "GuardDutyBucketLocation"
    actions   = ["s3:GetBucketLocation"]
    resources = ["arn:${local.partition}:s3:::${local.evidence_bucket}"]
    principals {
      type        = "Service"
      identifiers = ["guardduty.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [local.account_id]
    }
    condition {
      test     = "StringLike"
      variable = "aws:SourceArn"
      values   = ["arn:${local.partition}:guardduty:*:${local.account_id}:detector/*"]
    }
  }

  statement {
    sid       = "GuardDutyExportFindings"
    actions   = ["s3:PutObject"]
    resources = ["arn:${local.partition}:s3:::${local.evidence_bucket}/${local.findings_prefix}/*"]
    principals {
      type        = "Service"
      identifiers = ["guardduty.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [local.account_id]
    }
    condition {
      test     = "StringLike"
      variable = "aws:SourceArn"
      values   = ["arn:${local.partition}:guardduty:*:${local.account_id}:detector/*"]
    }
  }

  statement {
    sid     = "DenyLogDeletion"
    effect  = "Deny"
    actions = local.log_bucket_deny_actions
    resources = [
      "arn:${local.partition}:s3:::${local.evidence_bucket}",
      "arn:${local.partition}:s3:::${local.evidence_bucket}/*",
    ]
    principals {
      type        = "*"
      identifiers = ["*"]
    }
    dynamic "condition" {
      for_each = length(var.delete_exempt_principal_arns) > 0 ? [1] : []
      content {
        test     = "ArnNotLike"
        variable = "aws:PrincipalArn"
        values   = var.delete_exempt_principal_arns
      }
    }
  }
}

# No Object Lock here: AWS Config and GuardDuty exports are evidence, not the CERT-In log of record,
# and not every AWS delivery service writes the checksums Object Lock requires. Deletes are denied.
module "evidence_bucket" {
  source = "../s3_bucket"

  name                   = local.evidence_bucket
  kms_key_arn            = aws_kms_key.security.arn
  access_logging_enabled = true
  access_log_bucket      = var.access_log_bucket
  force_destroy          = false
  additional_policy_json = data.aws_iam_policy_document.evidence_bucket.json
  lifecycle_rules = [
    {
      id                                 = "expire-after-retention"
      expiration_days                    = var.log_retention_days
      noncurrent_version_expiration_days = 1
      abort_incomplete_multipart_days    = 7
    },
  ]
  tags = merge(local.tags, { data_class = "C1-security-logs" })
}

# --- CloudTrail ----------------------------------------------------------------------------------

resource "aws_cloudtrail" "this" {
  name                          = local.trail_name
  s3_bucket_name                = module.trail_bucket.id
  s3_key_prefix                 = local.trail_prefix
  kms_key_id                    = aws_kms_key.security.arn
  is_multi_region_trail         = true
  include_global_service_events = true
  enable_log_file_validation    = true
  enable_logging                = true

  advanced_event_selector {
    name = "Management events, read and write"
    field_selector {
      field  = "eventCategory"
      equals = ["Management"]
    }
  }

  advanced_event_selector {
    name = "S3 object events on buckets holding school data"
    field_selector {
      field  = "eventCategory"
      equals = ["Data"]
    }
    field_selector {
      field  = "resources.type"
      equals = ["AWS::S3::Object"]
    }
    field_selector {
      field       = "resources.ARN"
      starts_with = local.data_event_arn_prefixes
    }
  }

  tags = local.tags

  lifecycle {
    precondition {
      condition = !anytrue([
        for p in local.data_event_arn_prefixes :
        startswith("arn:${local.partition}:s3:::${local.trail_bucket}/", p) || startswith("arn:${local.partition}:s3:::${local.evidence_bucket}/", p)
      ])
      error_message = "Data events must not include the log buckets themselves (the trail would log its own deliveries)."
    }
  }

  # The bucket policy must allow CloudTrail before the trail is created.
  depends_on = [module.trail_bucket]
}

# --- AWS Config role (IAM is global: one role for both regions) -----------------------------------

data "aws_iam_policy_document" "config_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["config.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [local.account_id]
    }
  }
}

data "aws_iam_policy_document" "config_delivery" {
  statement {
    sid       = "BucketAcl"
    actions   = ["s3:GetBucketAcl"]
    resources = [module.evidence_bucket.arn]
  }

  statement {
    sid       = "Deliver"
    actions   = ["s3:PutObject"]
    resources = ["${module.evidence_bucket.arn}/${local.config_prefix}/AWSLogs/${local.account_id}/*"]
  }

  statement {
    sid       = "EncryptDelivery"
    actions   = ["kms:GenerateDataKey", "kms:Decrypt"]
    resources = [aws_kms_key.security.arn]
  }
}

resource "aws_iam_role" "config" {
  name               = "${var.name_prefix}-config-recorder"
  assume_role_policy = data.aws_iam_policy_document.config_assume.json
  tags               = local.tags
}

resource "aws_iam_role_policy_attachment" "config" {
  role       = aws_iam_role.config.name
  policy_arn = "arn:${local.partition}:iam::aws:policy/service-role/AWS_ConfigRole"
}

resource "aws_iam_role_policy" "config_delivery" {
  name   = "deliver-to-evidence-bucket"
  role   = aws_iam_role.config.id
  policy = data.aws_iam_policy_document.config_delivery.json
}

# --- Detection per region -----------------------------------------------------------------------

locals {
  # The role ARN, referenced together with its policies so the recorder and delivery channel are only
  # created once the role may write to the evidence bucket (a module-level depends_on would defer the
  # modules' data sources to apply time).
  config_role_arn = element([
    aws_iam_role.config.arn,
    aws_iam_role_policy.config_delivery.id,
    aws_iam_role_policy_attachment.config.id,
  ], 0)
}

module "detection_primary" {
  source = "../security_detection"

  name_prefix = var.name_prefix
  is_primary  = true
  guardduty_features = merge(
    var.guardduty_features_primary,
    length(var.guardduty_runtime_agent_management) > 0 ? { RUNTIME_MONITORING = true } : {},
  )
  guardduty_runtime_agent_management = var.guardduty_runtime_agent_management
  guardduty_export = {
    bucket_arn  = module.evidence_bucket.arn
    prefix      = local.findings_prefix
    kms_key_arn = aws_kms_key.security.arn
  }
  config_role_arn            = local.config_role_arn
  config_bucket_name         = module.evidence_bucket.id
  config_s3_key_prefix       = local.config_prefix
  config_kms_key_arn         = aws_kms_key.security.arn
  config_recording_frequency = var.config_recording_frequency
  config_rules               = var.config_rules
  securityhub_standards      = var.securityhub_standards
  tags                       = local.tags
}

module "detection_dr" {
  source = "../security_detection"

  region             = local.dr_region
  name_prefix        = var.name_prefix
  is_primary         = false
  guardduty_features = var.guardduty_features_dr
  guardduty_export = {
    bucket_arn  = module.evidence_bucket.arn
    prefix      = local.findings_prefix
    kms_key_arn = aws_kms_key.security.arn
  }
  config_role_arn            = local.config_role_arn
  config_bucket_name         = module.evidence_bucket.id
  config_s3_key_prefix       = local.config_prefix
  config_kms_key_arn         = aws_kms_key.security.arn
  config_recording_frequency = var.config_recording_frequency
  securityhub_standards      = var.securityhub_standards
  forward_to_event_bus_arn   = local.primary_bus_arn
  tamper_event_sources       = local.tamper_event_sources
  tamper_event_names         = local.tamper_event_names
  tags                       = local.tags
}

# Security Hub in ap-south-1 shows ap-south-2 findings too (one console, one alert rule).
resource "aws_securityhub_finding_aggregator" "this" {
  linking_mode      = "SPECIFIED_REGIONS"
  specified_regions = [local.dr_region]

  depends_on = [module.detection_primary, module.detection_dr]
}

# --- Account guardrails -------------------------------------------------------------------------

resource "aws_s3_account_public_access_block" "this" {
  count = var.account_public_access_block ? 1 : 0

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_ebs_encryption_by_default" "primary" {
  count   = var.ebs_encryption_by_default ? 1 : 0
  enabled = true
}

resource "aws_ebs_encryption_by_default" "dr" {
  count   = var.ebs_encryption_by_default ? 1 : 0
  region  = local.dr_region
  enabled = true
}

# --- Alerting: EventBridge (ap-south-1) -> SNS -> on-call ------------------------------------------

resource "aws_sns_topic" "alerts" {
  name              = "${var.name_prefix}-security-alerts"
  kms_master_key_id = aws_kms_key.security.arn
  tags              = local.tags
}

data "aws_iam_policy_document" "alerts_topic" {
  statement {
    sid       = "AllowSecurityEventRules"
    actions   = ["sns:Publish"]
    resources = [aws_sns_topic.alerts.arn]
    principals {
      type        = "Service"
      identifiers = ["events.amazonaws.com"]
    }
    condition {
      test     = "ArnLike"
      variable = "aws:SourceArn"
      values   = ["arn:${local.partition}:events:${local.region}:${local.account_id}:rule/${var.name_prefix}-*"]
    }
  }
}

resource "aws_sns_topic_policy" "alerts" {
  arn    = aws_sns_topic.alerts.arn
  policy = data.aws_iam_policy_document.alerts_topic.json
}

resource "aws_sns_topic_subscription" "alerts_email" {
  for_each = toset(var.alert_emails)

  topic_arn = aws_sns_topic.alerts.arn
  protocol  = "email"
  endpoint  = each.key
}

locals {
  alert_rules = {
    # docs/11 §6: GuardDuty high finding = P1 security (R5). Findings from ap-south-2 are forwarded here.
    guardduty-findings = {
      description = "GuardDuty finding at or above severity ${var.guardduty_alert_min_severity} in any region. P1 security: docs/11 R5 (CERT-In 6-hour clock)."
      pattern = jsonencode({
        source        = ["aws.guardduty"]
        "detail-type" = ["GuardDuty Finding"]
        detail        = { severity = [{ numeric = [">=", var.guardduty_alert_min_severity] }] }
      })
    }
    # Failed controls and Config findings (both regions via the aggregator); GuardDuty is excluded
    # because the rule above already pages for it.
    securityhub-findings = {
      description = "New Security Hub finding labelled ${join("/", var.securityhub_alert_labels)} (controls, Config). Triage per docs/11 R5."
      pattern = jsonencode({
        source        = ["aws.securityhub"]
        "detail-type" = ["Security Hub Findings - Imported"]
        detail = {
          findings = {
            ProductName = [{ "anything-but" = ["GuardDuty"] }]
            Severity    = { Label = var.securityhub_alert_labels }
            Workflow    = { Status = ["NEW"] }
            RecordState = ["ACTIVE"]
          }
        }
      })
    }
    detection-tampering = {
      description = "Logging or detection switched off or blinded (CloudTrail, GuardDuty, Config, Security Hub, KMS, account guardrails). P1 security until explained."
      pattern = jsonencode({
        "detail-type" = ["AWS API Call via CloudTrail"]
        detail = {
          eventSource = local.tamper_event_sources
          eventName   = local.tamper_event_names
        }
      })
    }
    log-bucket-tampering = {
      description = "Policy, lifecycle, lock, logging or encryption change on the CloudTrail or evidence bucket."
      pattern = jsonencode({
        "detail-type" = ["AWS API Call via CloudTrail"]
        detail = {
          eventSource       = ["s3.amazonaws.com"]
          eventName         = local.log_bucket_tamper_event_names
          requestParameters = { bucketName = [local.trail_bucket, local.evidence_bucket] }
        }
      })
    }
  }
}

resource "aws_cloudwatch_event_rule" "alert" {
  for_each = local.alert_rules

  name          = "${var.name_prefix}-${each.key}"
  description   = each.value.description
  event_pattern = each.value.pattern
  tags          = local.tags
}

resource "aws_cloudwatch_event_target" "alert" {
  for_each = local.alert_rules

  rule = aws_cloudwatch_event_rule.alert[each.key].name
  arn  = aws_sns_topic.alerts.arn
}
