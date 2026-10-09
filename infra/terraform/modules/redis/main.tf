# ElastiCache Valkey 8 replication group (Celery broker, rate limits, BFF sessions).
# TLS in transit (required), KMS at rest (SEC-011). Access control is RBAC (audit 2026-10-05 P2-06):
# one ElastiCache user per service and the default user off. Passwords are generated ephemerally and
# written only to write-only attributes (ElastiCache + Secrets Manager), so they never appear in plan
# or state (SEC-009).

locals {
  # The Celery broker (kombu) treats a rediss:// URL without ssl_cert_reqs as CERT_NONE: TLS
  # without any certificate check, so anyone on the path could read or inject tasks. The Celery
  # result backend refuses such a URL outright. redis-py (API) and node-redis (web) verify by
  # default and ignore or accept the parameter (SEC-011, ASVS 9.2.1).
  # Arguments: user name, password, primary endpoint.
  url_template = "rediss://%s:%s@%s:6379/0?ssl_cert_reqs=required"

  # P2-06: the same rules as the dedicated hosts' Valkey ACL (deploy/dedicated/compose.yaml; kept
  # equal by apps/api/tests/deploy/test_valkey_acl.py). web (the internet-facing BFF) reaches only
  # its sessions (sos:web:*) and sign-in rate limits (sos:rl:v1:bff:*): read/write commands and its
  # two Lua scripts on those keys; no Celery queue, no API key, no SELECT, no pub/sub. api, worker
  # and beat use the broker and every key, without dangerous or admin commands (FLUSHALL, KEYS,
  # CONFIG, ACL, ...). The default user exists only because a user group needs one.
  access_strings = {
    default = "off -@all"
    web     = "on ~sos:web:* ~sos:rl:v1:bff:* -@all +@read +@write -@dangerous +eval +evalsha +ping +hello +client|setinfo"
    api     = "on ~* &* +@all -@dangerous -@admin"
    worker  = "on ~* &* +@all -@dangerous -@admin"
    beat    = "on ~* &* +@all -@dangerous -@admin"
  }
  service_users = toset(["web", "api", "worker", "beat"])
}

# ElastiCache passwords: 16-128 printable characters, no spaces, quotes or @; 64 alphanumerics
# ~ 380 bits. One per user, the disabled default user included.
ephemeral "random_password" "user" {
  for_each = local.access_strings

  length  = 64
  special = false
}

resource "aws_elasticache_user" "this" {
  for_each = local.access_strings

  user_id              = "${var.name}-${each.key}"
  user_name            = each.key
  engine               = "valkey"
  access_string        = each.value
  passwords_wo         = ephemeral.random_password.user[each.key].result
  passwords_wo_version = var.auth_token_version
  tags                 = var.tags
}

resource "aws_elasticache_user_group" "this" {
  engine        = "valkey"
  user_group_id = var.name
  user_ids      = [for u in aws_elasticache_user.this : u.user_id]
  tags          = var.tags
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
  kms_key_id        = coalesce(var.log_kms_key_arn, var.kms_key_arn)
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
  # RBAC instead of one shared AUTH token (P2-06). A group created with an AUTH token moves once with
  # `aws elasticache modify-replication-group --auth-token-update-strategy DELETE
  # --user-group-ids-to-add <name>` before this apply (docs/10 §5.3).
  user_group_ids = [aws_elasticache_user_group.this.user_group_id]

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

# One connection secret per service user (JSON: host, port, username, password, url). Each ECS task
# gets only its own (modules/shared_platform).
resource "aws_secretsmanager_secret" "user" {
  for_each = local.service_users

  name        = "${var.secret_name}/${each.key}"
  description = "Valkey connection for ${var.name}, user ${each.key} (host, port, username, password, url)"
  kms_key_id  = var.kms_key_arn
  tags        = var.tags
}

resource "aws_secretsmanager_secret_version" "user" {
  for_each = local.service_users

  secret_id = aws_secretsmanager_secret.user[each.key].id
  secret_string_wo = jsonencode({
    host     = aws_elasticache_replication_group.this.primary_endpoint_address
    port     = 6379
    username = each.key
    password = ephemeral.random_password.user[each.key].result
    url = format(local.url_template, each.key, ephemeral.random_password.user[each.key].result,
    aws_elasticache_replication_group.this.primary_endpoint_address)
  })
  secret_string_wo_version = var.auth_token_version
}
