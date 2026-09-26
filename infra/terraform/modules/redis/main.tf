# ElastiCache Valkey 8 replication group (Celery broker, rate limits, BFF sessions).
# TLS in transit (required), KMS at rest (SEC-011), AUTH token generated ephemerally and written only
# to write-only attributes (ElastiCache + Secrets Manager), so it never appears in plan or state (SEC-009).

ephemeral "random_password" "auth" {
  length  = 64
  special = false # ElastiCache AUTH tokens forbid several symbols; 64 alphanumerics ~ 380 bits.
}

resource "aws_elasticache_subnet_group" "this" {
  name       = var.name
  subnet_ids = var.subnet_ids
  tags       = var.tags
}

resource "aws_security_group" "this" {
  name        = "${var.name}-cache"
  description = "Valkey: 6379 from SchoolOS app tasks only"
  vpc_id      = var.vpc_id
  tags        = merge(var.tags, { Name = "${var.name}-cache" })
}

resource "aws_vpc_security_group_ingress_rule" "valkey" {
  for_each = var.allowed_security_groups

  security_group_id            = aws_security_group.this.id
  description                  = "Valkey from ${each.key}"
  ip_protocol                  = "tcp"
  from_port                    = 6379
  to_port                      = 6379
  referenced_security_group_id = each.value
}

resource "aws_elasticache_parameter_group" "this" {
  name        = "${var.name}-valkey8"
  family      = "valkey8"
  description = "SchoolOS Valkey 8"

  # Celery visibility/eviction: never evict broker keys silently.
  parameter {
    name  = "maxmemory-policy"
    value = "noeviction"
  }

  tags = var.tags
}

resource "aws_cloudwatch_log_group" "slow" {
  name              = "/schoolos/${var.name}/valkey-slow-log"
  retention_in_days = var.log_retention_days
  kms_key_id        = var.kms_key_arn
  tags              = var.tags
}

resource "aws_elasticache_replication_group" "this" {
  replication_group_id = var.name
  description          = "SchoolOS Valkey (${var.name})"
  engine               = "valkey"
  engine_version       = var.engine_version
  node_type            = var.node_type
  port                 = 6379
  parameter_group_name = aws_elasticache_parameter_group.this.name
  subnet_group_name    = aws_elasticache_subnet_group.this.name
  security_group_ids   = [aws_security_group.this.id]

  num_cache_clusters         = var.num_cache_clusters
  automatic_failover_enabled = var.num_cache_clusters > 1
  multi_az_enabled           = var.num_cache_clusters > 1

  at_rest_encryption_enabled = true
  kms_key_id                 = var.kms_key_arn
  transit_encryption_enabled = true
  transit_encryption_mode    = "required"
  auth_token_wo              = ephemeral.random_password.auth.result
  auth_token_wo_version      = var.auth_token_version
  auth_token_update_strategy = "ROTATE"

  snapshot_retention_limit   = var.snapshot_retention_days
  snapshot_window            = "20:00-21:00"
  maintenance_window         = "sat:21:30-sat:22:30"
  auto_minor_version_upgrade = true
  apply_immediately          = false

  log_delivery_configuration {
    destination      = aws_cloudwatch_log_group.slow.name
    destination_type = "cloudwatch-logs"
    log_format       = "json"
    log_type         = "slow-log"
  }

  tags = var.tags
}

resource "aws_secretsmanager_secret" "this" {
  name        = var.secret_name
  description = "Valkey connection for ${var.name} (host, port, auth_token, url)"
  kms_key_id  = var.kms_key_arn
  tags        = var.tags
}

resource "aws_secretsmanager_secret_version" "this" {
  secret_id = aws_secretsmanager_secret.this.id
  secret_string_wo = jsonencode({
    host       = aws_elasticache_replication_group.this.primary_endpoint_address
    port       = 6379
    auth_token = ephemeral.random_password.auth.result
    url        = "rediss://:${ephemeral.random_password.auth.result}@${aws_elasticache_replication_group.this.primary_endpoint_address}:6379/0"
  })
  secret_string_wo_version = var.auth_token_version
}
