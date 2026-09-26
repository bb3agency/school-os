# Public edge (TB1): ALB with TLS 1.2+/1.3 (ACM), HTTP->HTTPS redirect, access logs, and WAFv2 with AWS
# managed rule groups + per-IP rate rules (SEC-022). Only the web/BFF is routed; the API is internal,
# except the HMAC-authenticated fleet heartbeat endpoint when expose_fleet_heartbeat = true.

# --- Certificate ------------------------------------------------------------------

resource "aws_acm_certificate" "this" {
  domain_name               = var.domain_names[0]
  subject_alternative_names = slice(var.domain_names, 1, length(var.domain_names))
  validation_method         = "DNS"
  key_algorithm             = "RSA_2048"
  tags                      = var.tags

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_route53_record" "validation" {
  for_each = var.route53_zone_id == null ? {} : {
    for o in aws_acm_certificate.this.domain_validation_options : o.domain_name => o
  }

  zone_id         = var.route53_zone_id
  name            = each.value.resource_record_name
  type            = each.value.resource_record_type
  records         = [each.value.resource_record_value]
  ttl             = 300
  allow_overwrite = true
}

# Waits until the certificate is issued. Without a Route 53 zone, add the records from the
# acm_validation_records output at your DNS provider while this waits (timeout 2 h).
resource "aws_acm_certificate_validation" "this" {
  certificate_arn         = aws_acm_certificate.this.arn
  validation_record_fqdns = var.route53_zone_id == null ? null : [for r in aws_route53_record.validation : r.fqdn]

  timeouts {
    create = "2h"
  }
}

# --- Security group -----------------------------------------------------------------

resource "aws_security_group" "alb" {
  name        = "${var.name}-alb"
  description = "Public ALB: 80/443 from the internet, egress to app tasks only"
  vpc_id      = var.vpc_id
  tags        = merge(var.tags, { Name = "${var.name}-alb" })
}

# Public web entry point for schools (browsers on office PCs/phones): must accept the internet.
#trivy:ignore:AVD-AWS-0107
resource "aws_vpc_security_group_ingress_rule" "https_v4" {
  security_group_id = aws_security_group.alb.id
  description       = "HTTPS from the internet (schools)"
  ip_protocol       = "tcp"
  from_port         = 443
  to_port           = 443
  cidr_ipv4         = "0.0.0.0/0"
}

# Port 80 only issues a 301 redirect to HTTPS (listener below); no content is served over HTTP.
#trivy:ignore:AVD-AWS-0107
resource "aws_vpc_security_group_ingress_rule" "http_v4" {
  security_group_id = aws_security_group.alb.id
  description       = "HTTP from the internet (redirect to HTTPS only)"
  ip_protocol       = "tcp"
  from_port         = 80
  to_port           = 80
  cidr_ipv4         = "0.0.0.0/0"
}

resource "aws_vpc_security_group_egress_rule" "web" {
  security_group_id = aws_security_group.alb.id
  description       = "To web tasks"
  ip_protocol       = "tcp"
  from_port         = var.web_port
  to_port           = var.web_port
  cidr_ipv4         = var.vpc_cidr
}

resource "aws_vpc_security_group_egress_rule" "api" {
  count = var.expose_fleet_heartbeat ? 1 : 0

  security_group_id = aws_security_group.alb.id
  description       = "To API tasks (fleet heartbeat only)"
  ip_protocol       = "tcp"
  from_port         = var.api_port
  to_port           = var.api_port
  cidr_ipv4         = var.vpc_cidr
}

# --- Load balancer -------------------------------------------------------------------

# Internet-facing by design: this is the product's public entry point, protected by WAF (SEC-022).
#trivy:ignore:AVD-AWS-0053
resource "aws_lb" "this" {
  name                       = "${var.name}-alb"
  load_balancer_type         = "application"
  internal                   = false
  security_groups            = [aws_security_group.alb.id]
  subnets                    = var.public_subnet_ids
  idle_timeout               = var.idle_timeout
  drop_invalid_header_fields = true
  desync_mitigation_mode     = "strictest"
  enable_deletion_protection = var.deletion_protection
  enable_http2               = true

  access_logs {
    bucket  = var.access_logs_bucket
    prefix  = "alb"
    enabled = true
  }

  tags = var.tags
}

resource "aws_lb_target_group" "web" {
  name                 = "${var.name}-web"
  port                 = var.web_port
  protocol             = "HTTP"
  target_type          = "ip"
  vpc_id               = var.vpc_id
  deregistration_delay = 30

  health_check {
    path                = var.web_health_check_path
    matcher             = "200"
    interval            = 15
    healthy_threshold   = 2
    unhealthy_threshold = 3
  }

  tags = var.tags
}

resource "aws_lb_target_group" "api" {
  count = var.expose_fleet_heartbeat ? 1 : 0

  name                 = "${var.name}-api"
  port                 = var.api_port
  protocol             = "HTTP"
  target_type          = "ip"
  vpc_id               = var.vpc_id
  deregistration_delay = 30

  health_check {
    path                = var.api_health_check_path
    matcher             = "200"
    interval            = 15
    healthy_threshold   = 2
    unhealthy_threshold = 3
  }

  tags = var.tags
}

resource "aws_lb_listener" "http" {
  load_balancer_arn = aws_lb.this.arn
  port              = 80
  protocol          = "HTTP"

  default_action {
    type = "redirect"
    redirect {
      port        = "443"
      protocol    = "HTTPS"
      status_code = "HTTP_301"
    }
  }
}

resource "aws_lb_listener" "https" {
  load_balancer_arn = aws_lb.this.arn
  port              = 443
  protocol          = "HTTPS"
  ssl_policy        = var.ssl_policy
  certificate_arn   = aws_acm_certificate_validation.this.certificate_arn

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.web.arn
  }
}

# Only the exact heartbeat path + POST reaches the API; everything else under /api/v1 is internal.
resource "aws_lb_listener_rule" "fleet_heartbeat" {
  count = var.expose_fleet_heartbeat ? 1 : 0

  listener_arn = aws_lb_listener.https.arn
  priority     = 10

  condition {
    path_pattern {
      values = ["/api/v1/fleet/heartbeat"]
    }
  }

  condition {
    http_request_method {
      values = ["POST"]
    }
  }

  action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.api[0].arn
  }
}

# --- Route 53 alias records (optional) -------------------------------------------------

resource "aws_route53_record" "alias" {
  for_each = var.route53_zone_id == null ? toset([]) : toset(var.domain_names)

  zone_id = var.route53_zone_id
  name    = each.key
  type    = "A"

  alias {
    name                   = aws_lb.this.dns_name
    zone_id                = aws_lb.this.zone_id
    evaluate_target_health = true
  }
}

# --- WAF ---------------------------------------------------------------------------------

locals {
  managed_rule_groups = [
    # name, priority, overrides-to-count
    { name = "AWSManagedRulesAmazonIpReputationList", priority = 10, count_rules = [] },
    { name = "AWSManagedRulesKnownBadInputsRuleSet", priority = 20, count_rules = [] },
    # Imports and bulk JSON bodies exceed the 8 KB body inspection limit; size is enforced by the app.
    { name = "AWSManagedRulesCommonRuleSet", priority = 30, count_rules = ["SizeRestrictions_BODY"] },
    { name = "AWSManagedRulesSQLiRuleSet", priority = 40, count_rules = [] },
    { name = "AWSManagedRulesLinuxRuleSet", priority = 50, count_rules = [] },
  ]
}

resource "aws_wafv2_web_acl" "this" {
  name        = "${var.name}-web-acl"
  description = "SchoolOS edge protection - SEC-022"
  scope       = "REGIONAL"

  default_action {
    allow {}
  }

  rule {
    name     = "rate-limit-auth"
    priority = 1

    action {
      block {}
    }

    statement {
      rate_based_statement {
        limit              = var.waf_auth_rate_limit_per_5min
        aggregate_key_type = "IP"

        scope_down_statement {
          byte_match_statement {
            search_string         = var.waf_auth_path_prefix
            positional_constraint = "STARTS_WITH"
            field_to_match {
              uri_path {}
            }
            text_transformation {
              priority = 0
              type     = "LOWERCASE"
            }
          }
        }
      }
    }

    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "${var.name}-rate-limit-auth"
      sampled_requests_enabled   = true
    }
  }

  rule {
    name     = "rate-limit-ip"
    priority = 2

    action {
      block {}
    }

    statement {
      rate_based_statement {
        limit              = var.waf_rate_limit_per_5min
        aggregate_key_type = "IP"
      }
    }

    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "${var.name}-rate-limit-ip"
      sampled_requests_enabled   = true
    }
  }

  dynamic "rule" {
    for_each = local.managed_rule_groups
    content {
      name     = rule.value.name
      priority = rule.value.priority

      override_action {
        none {}
      }

      statement {
        managed_rule_group_statement {
          vendor_name = "AWS"
          name        = rule.value.name

          dynamic "rule_action_override" {
            for_each = rule.value.count_rules
            content {
              name = rule_action_override.value
              action_to_use {
                count {}
              }
            }
          }
        }
      }

      visibility_config {
        cloudwatch_metrics_enabled = true
        metric_name                = "${var.name}-${rule.value.name}"
        sampled_requests_enabled   = true
      }
    }
  }

  visibility_config {
    cloudwatch_metrics_enabled = true
    metric_name                = "${var.name}-web-acl"
    sampled_requests_enabled   = true
  }

  tags = var.tags
}

resource "aws_wafv2_web_acl_association" "alb" {
  resource_arn = aws_lb.this.arn
  web_acl_arn  = aws_wafv2_web_acl.this.arn
}

# WAF log group names must start with aws-waf-logs-.
resource "aws_cloudwatch_log_group" "waf" {
  name              = "aws-waf-logs-${var.name}"
  retention_in_days = var.waf_log_retention_days
  kms_key_id        = var.log_kms_key_arn
  tags              = var.tags
}

resource "aws_wafv2_web_acl_logging_configuration" "this" {
  resource_arn            = aws_wafv2_web_acl.this.arn
  log_destination_configs = [aws_cloudwatch_log_group.waf.arn]

  # Never log credentials or session cookies (invariant 5).
  redacted_fields {
    single_header {
      name = "authorization"
    }
  }
  redacted_fields {
    single_header {
      name = "cookie"
    }
  }
  redacted_fields {
    single_header {
      name = "x-service-token"
    }
  }
}
