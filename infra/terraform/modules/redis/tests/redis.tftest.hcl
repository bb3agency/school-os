# Valkey: TLS required, KMS at rest, auth token only via write-only attributes (SEC-009, SEC-011).

mock_provider "aws" {}

variables {
  name        = "sos-test-valkey"
  vpc_id      = "vpc-0123456789abcdef0"
  subnet_ids  = ["subnet-00000000000000001", "subnet-00000000000000002"]
  kms_key_arn = "arn:aws:kms:ap-south-1:111122223333:key/00000000-0000-0000-0000-000000000001"
  secret_name = "sos/test/valkey"
  allowed_security_groups = {
    api = "sg-00000000000000001"
  }
}

run "encrypted_and_tls_required" {
  command = plan

  assert {
    condition     = aws_elasticache_replication_group.this.at_rest_encryption_enabled && aws_elasticache_replication_group.this.kms_key_id == var.kms_key_arn
    error_message = "At-rest encryption with the data CMK."
  }

  assert {
    condition     = aws_elasticache_replication_group.this.transit_encryption_enabled && aws_elasticache_replication_group.this.transit_encryption_mode == "required"
    error_message = "TLS in transit must be required."
  }

  assert {
    condition     = aws_elasticache_replication_group.this.engine == "valkey" && startswith(aws_elasticache_replication_group.this.engine_version, "8.")
    error_message = "Valkey 8."
  }
}

run "celery_verifies_the_valkey_certificate" {
  command = plan

  assert {
    condition     = startswith(local.url_template, "rediss://") && endswith(local.url_template, "?ssl_cert_reqs=required")
    error_message = "The connection URL must be rediss:// with ssl_cert_reqs=required (kombu defaults to CERT_NONE)."
  }
}

run "passwords_never_in_state" {
  command = plan

  assert {
    condition     = aws_elasticache_replication_group.this.auth_token == null
    error_message = "No shared AUTH token: access is per-user RBAC (P2-06)."
  }

  assert {
    condition = alltrue([for u in aws_elasticache_user.this :
    u.passwords == null && u.passwords_wo_version == 1 && u.engine == "valkey"])
    error_message = "User passwords must be set only via passwords_wo (never stored in state)."
  }

  assert {
    condition = alltrue([for k, v in aws_secretsmanager_secret_version.user : v.secret_string == null]) && alltrue([
    for s in aws_secretsmanager_secret.user : s.kms_key_id == var.kms_key_arn])
    error_message = "Connection secrets written write-only and encrypted with the CMK."
  }
}

# Audit 2026-10-05 P2-06: one user per service, the default user off, web only on its own keys.
run "one_user_per_service_and_web_only_on_its_keys" {
  command = plan

  assert {
    condition     = toset(keys(aws_secretsmanager_secret.user)) == toset(["web", "api", "worker", "beat"])
    error_message = "One connection secret per service user."
  }

  assert {
    condition     = startswith(aws_elasticache_user.this["default"].access_string, "off ") && aws_elasticache_user.this["default"].user_name == "default"
    error_message = "The default user must be off."
  }

  assert {
    condition = (
      strcontains(aws_elasticache_user.this["web"].access_string, " ~sos:web:* ~sos:rl:v1:bff:* -@all ")
      && !strcontains(aws_elasticache_user.this["web"].access_string, "~* ")
      && !strcontains(aws_elasticache_user.this["web"].access_string, "&")
      && !strcontains(aws_elasticache_user.this["web"].access_string, "+@all")
      && endswith(split("-@dangerous", aws_elasticache_user.this["web"].access_string)[0], "+@write ")
    )
    error_message = "web reaches only sos:web:* and sos:rl:v1:bff:*, no channels, dangerous commands removed after +@write."
  }

  assert {
    condition = alltrue([for u in ["api", "worker", "beat"] :
    strcontains(aws_elasticache_user.this[u].access_string, "-@dangerous -@admin")])
    error_message = "api, worker and beat never get dangerous or admin commands."
  }

  assert {
    condition     = length(aws_elasticache_replication_group.this.user_group_ids) == 1
    error_message = "The replication group uses the RBAC user group."
  }
}

run "only_security_group_ingress" {
  command = plan

  assert {
    condition     = alltrue([for r in aws_vpc_security_group_ingress_rule.valkey : r.cidr_ipv4 == null && r.from_port == 6379])
    error_message = "6379 only from referenced security groups."
  }
}
