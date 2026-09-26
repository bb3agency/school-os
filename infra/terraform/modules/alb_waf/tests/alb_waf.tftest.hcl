# Edge (TB1, SEC-022): only 80/443 are open to the internet; HTTP redirects; TLS 1.2+; WAF managed + rate rules.

mock_provider "aws" {}

variables {
  name               = "sos-test"
  vpc_id             = "vpc-0123456789abcdef0"
  vpc_cidr           = "10.20.0.0/16"
  public_subnet_ids  = ["subnet-00000000000000001", "subnet-00000000000000002"]
  domain_names       = ["app.example.test", "admin.example.test"]
  access_logs_bucket = "sos-test-logs"
  log_kms_key_arn    = "arn:aws:kms:ap-south-1:111122223333:key/00000000-0000-0000-0000-000000000001"
}

run "only_80_and_443_public" {
  command = plan

  assert {
    condition = (aws_vpc_security_group_ingress_rule.https_v4.from_port == 443 && aws_vpc_security_group_ingress_rule.https_v4.to_port == 443
    && aws_vpc_security_group_ingress_rule.http_v4.from_port == 80 && aws_vpc_security_group_ingress_rule.http_v4.to_port == 80)
    error_message = "The ALB admits only 80 and 443 from the internet."
  }

  assert {
    condition     = aws_vpc_security_group_egress_rule.web.cidr_ipv4 == var.vpc_cidr && aws_vpc_security_group_egress_rule.web.from_port == 3000
    error_message = "ALB egress is limited to the web port inside the VPC."
  }
}

run "tls_and_redirect" {
  command = plan

  assert {
    condition     = aws_lb_listener.https.ssl_policy == "ELBSecurityPolicy-TLS13-1-2-2021-06"
    error_message = "TLS 1.2+ with TLS 1.3 policy."
  }

  assert {
    condition     = one(aws_lb_listener.http.default_action).type == "redirect" && one(one(aws_lb_listener.http.default_action).redirect).protocol == "HTTPS"
    error_message = "HTTP must redirect to HTTPS."
  }

  assert {
    condition     = aws_lb.this.drop_invalid_header_fields && one(aws_lb.this.access_logs).enabled
    error_message = "Drop invalid headers and keep access logs."
  }
}

run "waf_rules" {
  command = plan

  assert {
    condition = length(setintersection(
      toset([for r in aws_wafv2_web_acl.this.rule : r.name]),
      toset(["AWSManagedRulesCommonRuleSet", "AWSManagedRulesKnownBadInputsRuleSet", "AWSManagedRulesAmazonIpReputationList", "AWSManagedRulesSQLiRuleSet", "rate-limit-ip", "rate-limit-auth"]),
    )) == 6
    error_message = "WAF must include AWS managed rule groups and per-IP rate rules (SEC-022)."
  }

  assert {
    condition     = length(aws_wafv2_web_acl_logging_configuration.this.redacted_fields) == 3
    error_message = "WAF logs redact authorization, cookie and service-token headers."
  }
}

run "api_not_routed_by_default" {
  command = plan

  assert {
    condition     = length(aws_lb_target_group.api) == 0 && length(aws_lb_listener_rule.fleet_heartbeat) == 0
    error_message = "The API is internal unless the fleet heartbeat is explicitly exposed."
  }
}

run "heartbeat_exposes_only_one_path" {
  command = plan

  variables {
    expose_fleet_heartbeat = true
  }

  assert {
    condition     = flatten([for c in aws_lb_listener_rule.fleet_heartbeat[0].condition : [for p in c.path_pattern : tolist(p.values)]]) == ["/api/v1/fleet/heartbeat"]
    error_message = "Only POST /api/v1/fleet/heartbeat reaches the API."
  }
}
