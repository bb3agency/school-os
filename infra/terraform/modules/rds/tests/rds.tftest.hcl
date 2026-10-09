# RDS PostgreSQL 16 posture (SEC-011, NFR-AVL-002, docs/10 §9).

mock_provider "aws" {
  mock_data "aws_iam_policy_document" {
    defaults = { json = "{\"Version\":\"2012-10-17\",\"Statement\":[]}" }
  }
}

variables {
  identifier  = "sos-test-pg"
  vpc_id      = "vpc-0123456789abcdef0"
  subnet_ids  = ["subnet-00000000000000001", "subnet-00000000000000002"]
  kms_key_arn = "arn:aws:kms:ap-south-1:111122223333:key/00000000-0000-0000-0000-000000000001"
  allowed_security_groups = {
    api = "sg-00000000000000001"
  }
}

run "encrypted_private_protected" {
  command = plan

  assert {
    condition     = aws_db_instance.this.storage_encrypted && aws_db_instance.this.kms_key_id == var.kms_key_arn
    error_message = "Storage encrypted with the data CMK."
  }

  assert {
    condition     = !aws_db_instance.this.publicly_accessible
    error_message = "Never publicly accessible."
  }

  assert {
    condition     = aws_db_instance.this.deletion_protection
    error_message = "Deletion protection defaults to on."
  }

  assert {
    condition     = aws_db_instance.this.backup_retention_period == 14 && aws_db_instance.this.engine == "postgres" && aws_db_instance.this.engine_version == "16"
    error_message = "PostgreSQL 16 with 14-day PITR."
  }

  assert {
    condition     = aws_db_instance.this.manage_master_user_password && aws_db_instance.this.performance_insights_kms_key_id == var.kms_key_arn
    error_message = "Master password managed by Secrets Manager; Performance Insights encrypted."
  }
}

run "tls_and_logging_parameters" {
  command = plan

  assert {
    condition     = one([for p in aws_db_parameter_group.this.parameter : p.value if p.name == "rds.force_ssl"]) == "1"
    error_message = "rds.force_ssl must be 1."
  }

  assert {
    condition     = one([for p in aws_db_parameter_group.this.parameter : p.value if p.name == "log_parameter_max_length"]) == "0"
    error_message = "Bind parameter values must never be logged (PII)."
  }

  assert {
    condition     = one([for p in aws_db_parameter_group.this.parameter : p.value if p.name == "log_statement"]) == "none"
    error_message = "log_statement=none (DDL with passwords must not be logged)."
  }

  assert {
    condition     = strcontains(one([for p in aws_db_parameter_group.this.parameter : p.value if p.name == "shared_preload_libraries"]), "pg_stat_statements")
    error_message = "pg_stat_statements must be preloaded."
  }

  assert {
    condition     = one([for p in aws_db_parameter_group.this.parameter : p.value if p.name == "idle_in_transaction_session_timeout"]) == "60000"
    error_message = "idle_in_transaction_session_timeout set."
  }
}

run "only_security_group_ingress" {
  command = plan

  assert {
    condition     = alltrue([for r in aws_vpc_security_group_ingress_rule.postgres : r.cidr_ipv4 == null && r.from_port == 5432])
    error_message = "5432 only from referenced security groups, never a CIDR."
  }
}

run "rejects_other_major_versions" {
  command = plan

  variables {
    engine_version = "15.7"
  }

  expect_failures = [var.engine_version]
}

# Audit 2026-10-05 hardening (confused deputy): Enhanced Monitoring assumes the role only for this
# account's instances.
run "monitoring_role_trusts_only_this_account" {
  command = plan

  assert {
    condition = alltrue([
      for s in data.aws_iam_policy_document.monitoring_assume.statement :
      anytrue([for c in s.condition : c.test == "StringEquals" && c.variable == "aws:SourceAccount" && toset(c.values) == toset([data.aws_caller_identity.current.account_id])])
    ])
    error_message = "The RDS monitoring trust policy pins aws:SourceAccount (confused deputy)."
  }
}
