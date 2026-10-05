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

run "auth_token_never_in_state" {
  command = plan

  assert {
    condition     = aws_elasticache_replication_group.this.auth_token == null && aws_elasticache_replication_group.this.auth_token_wo_version == 1
    error_message = "AUTH token must be set only via auth_token_wo (never stored in state)."
  }

  assert {
    condition     = aws_secretsmanager_secret_version.this.secret_string == null && aws_secretsmanager_secret.this.kms_key_id == var.kms_key_arn
    error_message = "Connection secret written write-only and encrypted with the CMK."
  }
}

run "only_security_group_ingress" {
  command = plan

  assert {
    condition     = alltrue([for r in aws_vpc_security_group_ingress_rule.valkey : r.cidr_ipv4 == null && r.from_port == 6379])
    error_message = "6379 only from referenced security groups."
  }
}
