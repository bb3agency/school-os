# Amazon SES v2 for staff invitation emails (US-102, FR-IAM-013, FR-NOT-001; docs/03 §5).
#
# - Domain identity in the default region (ap-south-1, NFR-PRV-001) with Easy DKIM (RSA 2048). The
#   three DKIM CNAMEs go into Route 53 when a zone is given, otherwise they are an output to create
#   by hand; the identity stays "pending" until they resolve.
# - Configuration set (the identity's default): TLS required to the receiving server, reputation
#   metrics, account suppression list for bounces and complaints.
# - Bounce/complaint monitoring through the account reputation metrics (AWS/SES
#   Reputation.BounceRate / Reputation.ComplaintRate) alarming to the ops topic. No SES event
#   destination to SNS/email: those notifications carry recipient addresses, which must not reach
#   alert channels (CLAUDE.md §6.5, SEC-008).
# New accounts are in the SES sandbox (verified recipients only, 200 messages/day): request
# production access by hand (docs/10 §5.2).

resource "aws_sesv2_configuration_set" "this" {
  configuration_set_name = "${var.name_prefix}-email"

  delivery_options {
    tls_policy = "REQUIRE"
  }

  reputation_options {
    reputation_metrics_enabled = true
  }

  sending_options {
    sending_enabled = true
  }

  suppression_options {
    suppressed_reasons = ["BOUNCE", "COMPLAINT"]
  }

  tags = var.tags
}

resource "aws_sesv2_email_identity" "domain" {
  email_identity         = var.domain
  configuration_set_name = aws_sesv2_configuration_set.this.configuration_set_name

  dkim_signing_attributes {
    next_signing_key_length = "RSA_2048_BIT"
  }

  tags = var.tags
}

locals {
  # The block also holds the (unused, BYODKIM-only) private key, so it is sensitive as a whole; the
  # tokens are public (they are published in DNS).
  dkim_tokens = nonsensitive(one(aws_sesv2_email_identity.domain.dkim_signing_attributes).tokens)
}

resource "aws_route53_record" "dkim" {
  count = var.route53_zone_id == null ? 0 : 3

  zone_id = var.route53_zone_id
  name    = "${local.dkim_tokens[count.index]}._domainkey.${var.domain}"
  type    = "CNAME"
  ttl     = 1800
  records = ["${local.dkim_tokens[count.index]}.dkim.amazonses.com"]
}

resource "aws_cloudwatch_metric_alarm" "reputation" {
  for_each = var.reputation_alarms ? {
    bounce    = { metric = "Reputation.BounceRate", threshold = var.bounce_rate_alarm }
    complaint = { metric = "Reputation.ComplaintRate", threshold = var.complaint_rate_alarm }
  } : {}

  alarm_name          = "${var.name_prefix}-ses-${each.key}-rate"
  alarm_description   = "SES ${each.key} rate at or above ${each.value.threshold * 100}% (SES may pause sending). Check invitation addresses; docs/11 §6."
  namespace           = "AWS/SES"
  metric_name         = each.value.metric
  statistic           = "Maximum"
  period              = 3600
  evaluation_periods  = 1
  threshold           = each.value.threshold
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [var.alarm_topic_arn]
  ok_actions          = [var.alarm_topic_arn]
  tags                = var.tags
}
