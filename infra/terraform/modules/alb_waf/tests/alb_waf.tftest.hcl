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
    condition     = aws_lb_listener.https.ssl_policy == "ELBSecurityPolicy-TLS13-1-2-Res-2021-06"
    error_message = "TLS 1.2+ with TLS 1.3, restricted to AEAD forward-secret suites (no TLS 1.2 CBC; audit 2026-10-05 hardening)."
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

# Audit 2026-10-05 hardening: the plain TLS13-1-2 policy still offers TLS 1.2 CBC suites.
run "tls_policy_without_cbc_is_required" {
  command = plan

  variables {
    ssl_policy = "ELBSecurityPolicy-TLS13-1-2-2021-06"
  }

  expect_failures = [var.ssl_policy]
}

# Audit 2026-10-05 detection gap: an ALB-attached WAF inspects only the first 8 KB of a body, and
# SizeRestrictions_BODY counts instead of blocking (large JSON bodies are legitimate). No route
# takes anything but JSON (files go to presigned S3 URLs; multipart is refused by the API), so a
# body over 8 KB that is not JSON is blocked; JSON beyond 8 KB is left to the strict app parsers.
run "oversized_non_json_bodies_are_blocked" {
  command = plan

  assert {
    condition = length([
      for r in aws_wafv2_web_acl.this.rule : r
      if r.name == "block-oversized-non-json-body"
      && length(one(r.action).block) == 1
      && one(one(one(one(r.statement).and_statement).statement[0].size_constraint_statement).field_to_match).body[0].oversize_handling == "MATCH"
      && one(one(one(r.statement).and_statement).statement[0].size_constraint_statement).comparison_operator == "GT"
      && one(one(one(r.statement).and_statement).statement[0].size_constraint_statement).size == 8192
      && one(one(one(one(one(r.statement).and_statement).statement[1].not_statement).statement).byte_match_statement).search_string == "json"
    ]) == 1
    error_message = "A rule blocks bodies over 8 KB (oversize counts as a match) unless the Content-Type is JSON."
  }

  assert {
    condition = one([
      for r in aws_wafv2_web_acl.this.rule : r.priority if r.name == "block-oversized-non-json-body"
      ]) < min([
      for r in aws_wafv2_web_acl.this.rule : r.priority if startswith(r.name, "AWSManagedRules")
    ]...)
    error_message = "The oversize rule runs before the managed rule groups."
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
    condition = one([
      for r in aws_wafv2_web_acl.this.rule :
      one(one(one(one(r.statement).rate_based_statement).scope_down_statement).byte_match_statement).search_string
      if r.name == "rate-limit-auth"
    ]) == "/bff/auth/"
    error_message = "The stricter auth rate limit covers the BFF sign-in routes (/bff/auth/*, apps/web)."
  }

  # P2-07: rate rules answer 429 problem+json with Retry-After; the machine paths have their own rule.
  assert {
    condition = alltrue([
      for r in aws_wafv2_web_acl.this.rule :
      one(one(one(r.action).block).custom_response).response_code == 429
      && one(one(one(one(r.action).block).custom_response).response_header).name == "Retry-After"
      if startswith(r.name, "rate-limit-")
    ]) && length([for r in aws_wafv2_web_acl.this.rule : r if startswith(r.name, "rate-limit-")]) == 3
    error_message = "Every WAF rate rule answers 429 with Retry-After (RFC 6585, RFC 9110)."
  }

  assert {
    condition = one([
      for r in aws_wafv2_web_acl.this.rule :
      one(one(r.statement).rate_based_statement).limit
      if r.name == "rate-limit-machine"
    ]) == var.waf_machine_rate_limit_per_5min
    error_message = "The machine paths (fleet heartbeat, Tally edge) have their own per-IP rate rule."
  }

  assert {
    condition     = one(aws_wafv2_web_acl.this.custom_response_body).content_type == "APPLICATION_JSON"
    error_message = "The WAF 429 body is a JSON problem."
  }

  assert {
    condition     = length(aws_wafv2_web_acl_logging_configuration.this.redacted_fields) == 4
    error_message = "WAF logs redact authorization, cookie and service-token headers and the query string (SEC-008)."
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
