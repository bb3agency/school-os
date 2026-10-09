# Network isolation (07 §13): data tier has no internet route; default SG denies all; flow logs on.

mock_provider "aws" {
  mock_data "aws_iam_policy_document" {
    defaults = { json = "{\"Version\":\"2012-10-17\",\"Statement\":[]}" }
  }
  mock_data "aws_region" {
    defaults = { region = "ap-south-1" }
  }
  mock_resource "aws_iam_role" {
    defaults = { arn = "arn:aws:iam::111122223333:role/mock" }
  }
  mock_resource "aws_cloudwatch_log_group" {
    defaults = { arn = "arn:aws:logs:ap-south-1:111122223333:log-group:mock" }
  }
}

variables {
  name            = "sos-test"
  log_kms_key_arn = "arn:aws:kms:ap-south-1:111122223333:key/00000000-0000-0000-0000-000000000001"
}

run "layout_matches_docs" {
  command = plan

  assert {
    condition     = aws_vpc.this.cidr_block == "10.20.0.0/16"
    error_message = "VPC CIDR is 10.20.0.0/16 (docs/10 §3)."
  }

  assert {
    condition     = length(aws_subnet.public) == 2 && length(aws_subnet.app) == 2 && length(aws_subnet.data) == 2
    error_message = "Three tiers across two AZs."
  }

  assert {
    condition     = alltrue([for s in aws_subnet.public : !s.map_public_ip_on_launch])
    error_message = "Public subnets must not auto-assign public IPs."
  }
}

run "data_tier_has_no_internet_route" {
  command = apply

  assert {
    condition     = !contains(concat([aws_route.public_internet.route_table_id], aws_route.app_nat[*].route_table_id), aws_route_table.data.id)
    error_message = "No IGW/NAT route may target the data route table."
  }

  assert {
    condition     = alltrue([for a in aws_route_table_association.data : a.route_table_id == aws_route_table.data.id])
    error_message = "Data subnets use the isolated data route table."
  }

  assert {
    condition     = length(aws_route.app_nat) == 2 && length(aws_nat_gateway.this) == 1
    error_message = "single NAT mode: one NAT gateway shared by both app route tables."
  }
}

run "default_security_group_is_empty" {
  command = plan

  assert {
    condition     = length(aws_default_security_group.this.ingress) == 0 && length(aws_default_security_group.this.egress) == 0
    error_message = "Default security group must have no rules."
  }
}

run "flow_logs_enabled" {
  command = plan

  assert {
    condition     = length(aws_flow_log.this) == 1 && aws_flow_log.this[0].traffic_type == "ALL" && aws_flow_log.this[0].max_aggregation_interval == 600
    error_message = "Flow logs record accepted and rejected traffic (ALL, 10-minute aggregation; audit 2026-10-05 detection gap)."
  }

  assert {
    condition     = aws_cloudwatch_log_group.flow[0].retention_in_days == 400
    error_message = "Flow logs retained 400 days."
  }
}

# Audit 2026-10-05 detection gap: the S3 gateway endpoint had no policy, so a compromised task could
# copy data to any bucket in the world over it. It now reaches this account's buckets and only the
# AWS-owned buckets that ECR, dnf and the agents read from.
run "s3_endpoint_reaches_only_this_accounts_buckets" {
  command = plan

  assert {
    condition = anytrue([
      for s in jsondecode(aws_vpc_endpoint.s3.policy).Statement :
      s.Effect == "Allow" && s.Action == "s3:*" && s.Resource == "*" && try(s.Condition.StringEquals["aws:ResourceAccount"], "") == data.aws_caller_identity.current.account_id
    ])
    error_message = "The S3 endpoint allows any S3 action only on buckets of this account."
  }

  assert {
    condition = alltrue([
      for s in jsondecode(aws_vpc_endpoint.s3.policy).Statement :
      s.Effect == "Allow" && (try(s.Condition.StringEquals["aws:ResourceAccount"], "") == data.aws_caller_identity.current.account_id || (s.Action == ["s3:GetObject"] && try(alltrue([for r in s.Resource : endswith(r, "/*")]), false)))
    ])
    error_message = "Every other endpoint statement is read-only (s3:GetObject) on listed AWS-owned buckets."
  }

  assert {
    condition = anytrue([
      for s in jsondecode(aws_vpc_endpoint.s3.policy).Statement :
      try(contains(s.Resource, "arn:aws:s3:::prod-ap-south-1-starport-layer-bucket/*"), false)
    ])
    error_message = "ECR image layers (the starport layer bucket) stay reachable."
  }
}

run "no_nat_option" {
  command = plan

  variables {
    nat_mode = "none"
  }

  assert {
    condition     = length(aws_nat_gateway.this) == 0 && length(aws_route.app_nat) == 0
    error_message = "nat_mode=none creates no NAT gateway."
  }
}

# Audit 2026-10-05 hardening (confused deputy): the flow-log role is assumable only for this
# account's flow logs.
run "flow_log_role_trusts_only_this_account" {
  command = plan

  assert {
    condition = alltrue([
      for s in data.aws_iam_policy_document.flow_assume.statement :
      anytrue([for c in s.condition : c.test == "StringEquals" && c.variable == "aws:SourceAccount" && toset(c.values) == toset([data.aws_caller_identity.current.account_id])])
      && anytrue([for c in s.condition : c.test == "ArnLike" && c.variable == "aws:SourceArn" && toset(c.values) == toset(["arn:aws:ec2:ap-south-1:${data.aws_caller_identity.current.account_id}:vpc-flow-log/*"])])
    ])
    error_message = "The flow-log trust policy pins aws:SourceAccount and aws:SourceArn (confused deputy)."
  }
}
