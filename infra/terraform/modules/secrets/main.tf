# Secrets Manager secrets for the shared tier (SEC-009).
# Every generated value comes from an ephemeral random_password and is written through write-only
# attributes (secret_string_wo), so no secret value is ever stored in Terraform plan or state, and
# nothing here is exported as an output except ARNs.

ephemeral "random_password" "db" {
  for_each = var.db_roles

  length  = 48
  special = false # URL-safe: embedded in connection URLs and passed as psql -v variables.
}

ephemeral "random_password" "generic" {
  for_each = var.random_secrets

  length  = each.value.length
  special = false
}

resource "aws_secretsmanager_secret" "db" {
  for_each = var.db_roles

  name                    = "${var.name_prefix}/db/${each.key}"
  description             = "PostgreSQL login for ${each.value.username} (created by infra/db/bootstrap.sql)"
  kms_key_id              = var.kms_key_arn
  recovery_window_in_days = var.recovery_window_in_days
  tags                    = merge(var.tags, { db_role = each.value.username })
}

resource "aws_secretsmanager_secret_version" "db" {
  for_each = var.db_roles

  secret_id = aws_secretsmanager_secret.db[each.key].id
  secret_string_wo = jsonencode({
    username = each.value.username
    password = ephemeral.random_password.db[each.key].result
    host     = var.db.host
    port     = var.db.port
    dbname   = var.db.name
    url = format(
      "%s://%s:%s@%s:%d/%s?sslmode=verify-full&sslrootcert=%s",
      var.db.driver_scheme,
      each.value.username,
      ephemeral.random_password.db[each.key].result,
      var.db.host,
      var.db.port,
      var.db.name,
      var.db.sslrootcert,
    )
  })
  secret_string_wo_version = each.value.version
}

resource "aws_secretsmanager_secret" "generic" {
  for_each = var.random_secrets

  name                    = "${var.name_prefix}/${each.key}"
  description             = each.value.description
  kms_key_id              = var.kms_key_arn
  recovery_window_in_days = var.recovery_window_in_days
  tags                    = var.tags
}

resource "aws_secretsmanager_secret_version" "generic" {
  for_each = var.random_secrets

  secret_id                = aws_secretsmanager_secret.generic[each.key].id
  secret_string_wo         = ephemeral.random_password.generic[each.key].result
  secret_string_wo_version = each.value.version
}

resource "aws_secretsmanager_secret" "operator" {
  for_each = var.operator_secrets

  name                    = "${var.name_prefix}/${each.key}"
  description             = each.value
  kms_key_id              = var.kms_key_arn
  recovery_window_in_days = var.recovery_window_in_days
  tags                    = merge(var.tags, { value_source = "operator" })
}

resource "aws_secretsmanager_secret_version" "operator" {
  for_each = var.operator_secrets

  secret_id                = aws_secretsmanager_secret.operator[each.key].id
  secret_string_wo         = "__SET_ME__"
  secret_string_wo_version = 1
}
