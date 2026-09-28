# Regional threat detection and posture for one AWS region of one account (SEC-023, docs/07 §13):
#   GuardDuty detector + explicit protection plans (+ optional findings export to S3)
#   AWS Config recorder, delivery channel and (primary region only) managed rules
#   Security Hub with AWS Foundational Security Best Practices and CIS standards
#   (non-primary regions) forwarding of findings and security-service API calls to the primary bus
# Instantiated once per region by modules/security_baseline; never used in local/ci.

data "aws_caller_identity" "current" {}
data "aws_partition" "current" {}
data "aws_region" "current" {}

locals {
  account_id = data.aws_caller_identity.current.account_id
  partition  = data.aws_partition.current.partition
  region     = coalesce(var.region, data.aws_region.current.region)
  forwarding = var.forward_to_event_bus_arn != null
}

# --- GuardDuty ----------------------------------------------------------------------------

resource "aws_guardduty_detector" "this" {
  region = var.region

  enable                       = true
  finding_publishing_frequency = "FIFTEEN_MINUTES"
  tags                         = var.tags
}

resource "aws_guardduty_detector_feature" "this" {
  for_each = var.guardduty_features
  region   = var.region

  detector_id = aws_guardduty_detector.this.id
  name        = each.key
  status      = each.value ? "ENABLED" : "DISABLED"

  dynamic "additional_configuration" {
    for_each = each.key == "RUNTIME_MONITORING" && each.value ? toset(var.guardduty_runtime_agent_management) : toset([])
    content {
      name   = additional_configuration.value
      status = "ENABLED"
    }
  }
}

resource "aws_guardduty_publishing_destination" "s3" {
  count  = var.guardduty_export == null ? 0 : 1
  region = var.region

  detector_id      = aws_guardduty_detector.this.id
  destination_type = "S3"
  destination_arn  = "${var.guardduty_export.bucket_arn}/${var.guardduty_export.prefix}"
  kms_key_arn      = var.guardduty_export.kms_key_arn
}

# --- AWS Config ---------------------------------------------------------------------------

resource "aws_config_configuration_recorder" "this" {
  region = var.region

  name     = "${var.name_prefix}-recorder"
  role_arn = var.config_role_arn

  recording_group {
    all_supported = true
    # IAM and other global resource types are recorded once per account, in the primary region.
    include_global_resource_types = var.is_primary
  }

  recording_mode {
    recording_frequency = var.config_recording_frequency
  }
}

resource "aws_config_delivery_channel" "this" {
  region = var.region

  name           = "${var.name_prefix}-delivery"
  s3_bucket_name = var.config_bucket_name
  s3_key_prefix  = var.config_s3_key_prefix
  s3_kms_key_arn = var.config_kms_key_arn

  snapshot_delivery_properties {
    delivery_frequency = "TwentyFour_Hours"
  }

  depends_on = [aws_config_configuration_recorder.this]
}

resource "aws_config_configuration_recorder_status" "this" {
  region = var.region

  name       = aws_config_configuration_recorder.this.name
  is_enabled = true

  depends_on = [aws_config_delivery_channel.this]
}

resource "aws_config_config_rule" "managed" {
  for_each = var.is_primary ? var.config_rules : {}
  region   = var.region

  name = "${var.name_prefix}-${each.key}"

  source {
    owner             = "AWS"
    source_identifier = each.value
  }

  tags       = var.tags
  depends_on = [aws_config_configuration_recorder_status.this]
}

# --- Security Hub -------------------------------------------------------------------------

resource "aws_securityhub_account" "this" {
  region = var.region

  # Standards are subscribed explicitly below; consolidated control findings (one finding per control).
  enable_default_standards  = false
  control_finding_generator = "SECURITY_CONTROL"
  auto_enable_controls      = true

  depends_on = [aws_config_configuration_recorder_status.this]
}

# Disabled controls with a recorded reason (owner decision 2026-09-27, SEC-023). The CIS metric-filter
# controls CloudWatch.1-14 need the trail in CloudWatch Logs, which Stage 0 does not do (billed per GB);
# the same events are covered by the EventBridge alerts of modules/security_baseline. Only CIS v1.2.0
# and v1.4.0 contain them; v3.0.0 (the default) does not, so nothing is disabled there.
locals {
  cis_cloudwatch_controls = {
    "cis-aws-foundations-benchmark/v/1.4.0" = ["CloudWatch.1", "CloudWatch.4", "CloudWatch.5", "CloudWatch.6", "CloudWatch.7", "CloudWatch.8", "CloudWatch.9", "CloudWatch.10", "CloudWatch.11", "CloudWatch.12", "CloudWatch.13", "CloudWatch.14"]
  }
  cloudwatch_reason = "SchoolOS Stage 0: CloudTrail is not delivered to CloudWatch Logs (cost); tampering, root and security-service changes alert through EventBridge rules instead (docs/10 §5.1, owner decision 2026-09-27)."

  control_exceptions = merge(
    {
      for pair in flatten([
        for std, ids in local.cis_cloudwatch_controls : [for id in ids : { standard = std, control_id = id }]
        if contains(var.securityhub_standards, std)
      ]) : "${pair.standard}|${pair.control_id}" => merge(pair, { reason = local.cloudwatch_reason })
    },
    { for e in var.securityhub_control_exceptions : "${e.standard}|${e.control_id}" => e },
  )
}

resource "aws_securityhub_standards_control_association" "disabled" {
  for_each = local.control_exceptions
  region   = var.region

  standards_arn       = "arn:${local.partition}:securityhub:${local.region}::standards/${each.value.standard}"
  security_control_id = each.value.control_id
  association_status  = "DISABLED"
  updated_reason      = each.value.reason

  depends_on = [aws_securityhub_standards_subscription.this]
}

resource "aws_securityhub_standards_subscription" "this" {
  for_each = toset(var.securityhub_standards)
  region   = var.region

  standards_arn = "arn:${local.partition}:securityhub:${local.region}::standards/${each.value}"

  depends_on = [aws_securityhub_account.this]
}

# --- Forwarding to the primary region (non-primary regions only) ----------------------------

data "aws_iam_policy_document" "forward_assume" {
  count = local.forwarding ? 1 : 0

  statement {
    actions = ["sts:AssumeRole"]
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

data "aws_iam_policy_document" "forward" {
  count = local.forwarding ? 1 : 0

  statement {
    actions   = ["events:PutEvents"]
    resources = [var.forward_to_event_bus_arn]
  }
}

resource "aws_iam_role" "forward" {
  count = local.forwarding ? 1 : 0

  # IAM is global: the region keeps the name unique per regional instance.
  name               = "${var.name_prefix}-${local.region}-security-forward"
  assume_role_policy = data.aws_iam_policy_document.forward_assume[0].json
  tags               = var.tags
}

resource "aws_iam_role_policy" "forward" {
  count = local.forwarding ? 1 : 0

  name   = "put-events-primary-bus"
  role   = aws_iam_role.forward[0].id
  policy = data.aws_iam_policy_document.forward[0].json
}

locals {
  forward_pattern = {
    "$or" = [
      { source = ["aws.guardduty"], "detail-type" = ["GuardDuty Finding"] },
      {
        "detail-type" = ["AWS API Call via CloudTrail"]
        detail = {
          eventSource = var.tamper_event_sources
          eventName   = var.tamper_event_names
        }
      },
    ]
  }
}

resource "aws_cloudwatch_event_rule" "forward" {
  count  = local.forwarding ? 1 : 0
  region = var.region

  name          = "${var.name_prefix}-security-forward"
  description   = "SEC-023: forward GuardDuty findings and security-service tampering in ${local.region} to the primary region."
  event_pattern = jsonencode(local.forward_pattern)
  tags          = var.tags

  lifecycle {
    precondition {
      condition     = length(var.tamper_event_sources) > 0 && length(var.tamper_event_names) > 0
      error_message = "Forwarding needs tamper_event_sources and tamper_event_names."
    }
  }
}

resource "aws_cloudwatch_event_target" "forward" {
  count  = local.forwarding ? 1 : 0
  region = var.region

  rule     = aws_cloudwatch_event_rule.forward[0].name
  arn      = var.forward_to_event_bus_arn
  role_arn = aws_iam_role.forward[0].arn
}
