# RDS PostgreSQL 16 (docs/10 §9, SEC-011, NFR-AVL-002, NFR-PRV-001).
# - Storage, snapshots, Performance Insights and the master secret encrypted with the data CMK
# - TLS required (rds.force_ssl), SCRAM passwords, no bind values in logs
# - PITR 14 days; automated backups replicated to ap-south-2 by ../rds_backup_replication (DR provider)
# - Master password generated and stored by RDS in Secrets Manager (never in Terraform state)
# - Application roles (sos_app, sos_migrator, sos_platform, sos_readonly, ...) are created by
#   infra/db/bootstrap.sql through the one-off "db-bootstrap" ECS task, not by Terraform.

resource "aws_db_subnet_group" "this" {
  name       = var.identifier
  subnet_ids = var.subnet_ids
  tags       = var.tags
}

resource "aws_security_group" "this" {
  name        = "${var.identifier}-db"
  description = "PostgreSQL: 5432 from SchoolOS app tasks only"
  vpc_id      = var.vpc_id
  tags        = merge(var.tags, { Name = "${var.identifier}-db" })
}

resource "aws_vpc_security_group_ingress_rule" "postgres" {
  for_each = var.allowed_security_groups

  security_group_id            = aws_security_group.this.id
  description                  = "PostgreSQL from ${each.key}"
  ip_protocol                  = "tcp"
  from_port                    = 5432
  to_port                      = 5432
  referenced_security_group_id = each.value
}

locals {
  base_parameters = {
    # TLS only; modern protocol floor.
    "rds.force_ssl"            = { value = "1", apply_method = "immediate" }
    "ssl_min_protocol_version" = { value = "TLSv1.2", apply_method = "immediate" }
    "password_encryption"      = { value = "scram-sha-256", apply_method = "immediate" }
    # Query statistics (static: needs reboot).
    "shared_preload_libraries"   = { value = "pg_stat_statements", apply_method = "pending-reboot" }
    "pg_stat_statements.track"   = { value = "top", apply_method = "immediate" }
    "pg_stat_statements.max"     = { value = "5000", apply_method = "pending-reboot" }
    "track_io_timing"            = { value = "1", apply_method = "immediate" }
    "track_activity_query_size"  = { value = "4096", apply_method = "pending-reboot" }
    "log_min_duration_statement" = { value = tostring(var.log_min_duration_statement_ms), apply_method = "immediate" }
    # Never log bind parameter values (they can contain personal data, invariant 5).
    "log_parameter_max_length"          = { value = "0", apply_method = "immediate" }
    "log_parameter_max_length_on_error" = { value = "0", apply_method = "immediate" }
    # DDL is not logged: ALTER ROLE ... PASSWORD would leak secrets into logs.
    "log_statement"                       = { value = "none", apply_method = "immediate" }
    "log_connections"                     = { value = "1", apply_method = "immediate" }
    "log_disconnections"                  = { value = "1", apply_method = "immediate" }
    "log_lock_waits"                      = { value = "1", apply_method = "immediate" }
    "log_temp_files"                      = { value = "10240", apply_method = "immediate" }
    "log_autovacuum_min_duration"         = { value = "10000", apply_method = "immediate" }
    "idle_in_transaction_session_timeout" = { value = tostring(var.idle_in_transaction_timeout_ms), apply_method = "immediate" }
    "rds.log_retention_period"            = { value = "4320", apply_method = "immediate" }
  }
  parameters = merge(local.base_parameters, var.extra_parameters)
}

resource "aws_db_parameter_group" "this" {
  name        = "${var.identifier}-pg16"
  family      = "postgres16"
  description = "SchoolOS PostgreSQL 16 parameters"
  tags        = var.tags

  dynamic "parameter" {
    for_each = local.parameters
    content {
      name         = parameter.key
      value        = parameter.value.value
      apply_method = parameter.value.apply_method
    }
  }

  lifecycle {
    create_before_destroy = true
  }
}

data "aws_caller_identity" "current" {}

# Confused deputy (audit 2026-10-05 hardening): only for this account's instances.
data "aws_iam_policy_document" "monitoring_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["monitoring.rds.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [data.aws_caller_identity.current.account_id]
    }
  }
}

resource "aws_iam_role" "monitoring" {
  count = var.monitoring_interval > 0 ? 1 : 0

  name               = "${var.identifier}-rds-monitoring"
  assume_role_policy = data.aws_iam_policy_document.monitoring_assume.json
  tags               = var.tags
}

resource "aws_iam_role_policy_attachment" "monitoring" {
  count = var.monitoring_interval > 0 ? 1 : 0

  role       = aws_iam_role.monitoring[0].name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonRDSEnhancedMonitoringRole"
}

# deletion_protection is variable-driven: envs/prod hard-wires true (asserted by envs/prod/tests), only the
# disposable synthetic-data staging stack turns it off.
#trivy:ignore:AVD-AWS-0177
resource "aws_db_instance" "this" {
  identifier     = var.identifier
  engine         = "postgres"
  engine_version = var.engine_version
  instance_class = var.instance_class
  db_name        = var.db_name
  port           = 5432

  username                      = var.master_username
  manage_master_user_password   = true
  master_user_secret_kms_key_id = var.kms_key_arn

  allocated_storage     = var.allocated_storage_gb
  max_allocated_storage = var.max_allocated_storage_gb
  storage_type          = "gp3"
  storage_encrypted     = true
  kms_key_id            = var.kms_key_arn

  db_subnet_group_name   = aws_db_subnet_group.this.name
  vpc_security_group_ids = [aws_security_group.this.id]
  publicly_accessible    = false
  multi_az               = var.multi_az
  network_type           = "IPV4"
  ca_cert_identifier     = "rds-ca-rsa2048-g1"

  parameter_group_name                = aws_db_parameter_group.this.name
  iam_database_authentication_enabled = true

  backup_retention_period   = var.backup_retention_days
  backup_window             = var.backup_window
  maintenance_window        = var.maintenance_window
  copy_tags_to_snapshot     = true
  delete_automated_backups  = false
  deletion_protection       = var.deletion_protection
  skip_final_snapshot       = var.skip_final_snapshot
  final_snapshot_identifier = var.skip_final_snapshot ? null : "${var.identifier}-final"

  auto_minor_version_upgrade  = true
  allow_major_version_upgrade = false
  apply_immediately           = false

  performance_insights_enabled          = true
  performance_insights_kms_key_id       = var.kms_key_arn
  performance_insights_retention_period = 7
  monitoring_interval                   = var.monitoring_interval
  monitoring_role_arn                   = var.monitoring_interval > 0 ? aws_iam_role.monitoring[0].arn : null
  enabled_cloudwatch_logs_exports       = ["postgresql", "upgrade"]

  tags = var.tags
}
