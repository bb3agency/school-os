variable "name_prefix" {
  description = "Secret name prefix, e.g. sos/prod. Secrets are named <prefix>/<name>."
  type        = string
}

variable "kms_key_arn" {
  description = "CMK encrypting all secrets."
  type        = string
}

variable "db" {
  description = "Connection facts used to build role connection URLs."
  type = object({
    host          = string
    port          = number
    name          = string
    sslrootcert   = optional(string, "/etc/ssl/certs/rds-global-bundle.pem")
    driver_scheme = optional(string, "postgresql+psycopg")
  })
}

variable "db_roles" {
  description = <<-EOT
    Login roles created by infra/db/bootstrap.sql. For each entry a password is generated ephemerally and
    written (write-only) to secret <prefix>/db/<key> as JSON {username, password, host, port, dbname, url}.
    Bump version to rotate; then re-run the db-bootstrap task so the role picks up the new password.
  EOT
  type = map(object({
    username = string
    version  = optional(number, 1)
  }))
  default = {
    app      = { username = "sos_app" }
    migrator = { username = "sos_migrator" }
    platform = { username = "sos_platform" }
    readonly = { username = "sos_readonly" }
  }
}

variable "random_secrets" {
  description = "Plain random secrets (e.g. service token key, BFF session secret). Value = random string; bump version to rotate."
  type = map(object({
    description = string
    length      = optional(number, 64)
    version     = optional(number, 1)
  }))
  default = {}
}

variable "operator_secrets" {
  description = <<-EOT
    Secrets whose values an operator supplies (third-party API keys). Created with the placeholder value
    "__SET_ME__" once; set the real value with `aws secretsmanager put-secret-value`. Terraform never
    overwrites it afterwards (write-only, version pinned). The app must refuse to start on the placeholder.
  EOT
  type        = map(string)
  default     = {}
}

variable "recovery_window_in_days" {
  description = "Recovery window on deletion."
  type        = number
  default     = 30
}

variable "tags" {
  description = "Extra tags."
  type        = map(string)
  default     = {}
}
