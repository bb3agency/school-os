# Amazon Cognito (ADR-0012) behind app/identity (07 §5).
#
# Tenant (school staff) pool:
#   - MFA "OPTIONAL" with TOTP. Cognito MFA is pool-wide, so the API enforces MFA for owner, principal
#     and office_admin (SEC-005, ADR-0018): without the claim sos:mfa="true" it answers 403 mfa_required.
#   - Password policy: min 12, no composition rules (NIST SP 800-63B), breached-password screening via
#     threat protection (PLUS tier), admin-created users only (no self sign-up).
#   - BFF app client: confidential (secret kept server side), authorization code flow; the BFF always
#     sends PKCE (S256). Access/ID tokens 10 min, refresh 12 h with rotation enabled.
# Platform operator pool: separate pool, MFA "ON" for every operator (contract §5).
# Both pools: device remembering off; pre-token-generation Lambda adds sos:mfa (ADR-0018, lambda.tf).

data "aws_region" "current" {}

locals {
  pools = merge(
    {
      tenant = {
        name          = "${var.name_prefix}-schools"
        mfa           = "OPTIONAL"
        domain_prefix = var.tenant_domain_prefix
        callback_urls = var.tenant_callback_urls
        logout_urls   = var.tenant_logout_urls
        client_name   = "${var.name_prefix}-web-bff"
        secret_name   = "${var.secret_name_prefix}/oidc/tenant-client-secret"
        admin_only    = true
        temp_pwd_days = 3
      }
    },
    var.create_platform_pool ? {
      platform = {
        name          = "${var.name_prefix}-platform-operators"
        mfa           = "ON"
        domain_prefix = var.platform_domain_prefix
        callback_urls = var.platform_callback_urls
        logout_urls   = var.platform_logout_urls
        client_name   = "${var.name_prefix}-platform-admin"
        secret_name   = "${var.secret_name_prefix}/oidc/platform-client-secret"
        admin_only    = true
        temp_pwd_days = 1
      }
    } : {},
  )
}

resource "aws_cognito_user_pool" "this" {
  for_each = local.pools

  name                     = each.value.name
  user_pool_tier           = var.user_pool_tier
  deletion_protection      = var.deletion_protection
  mfa_configuration        = each.value.mfa
  alias_attributes         = ["email", "preferred_username"]
  auto_verified_attributes = ["email"]

  username_configuration {
    case_sensitive = false
  }

  software_token_mfa_configuration {
    enabled = true
  }

  password_policy {
    minimum_length                   = 12
    require_lowercase                = false
    require_uppercase                = false
    require_numbers                  = false
    require_symbols                  = false
    temporary_password_validity_days = each.value.temp_pwd_days
    password_history_size            = var.user_pool_tier == "PLUS" || var.user_pool_tier == "ESSENTIALS" ? 5 : null
  }

  admin_create_user_config {
    allow_admin_create_user_only = each.value.admin_only
  }

  account_recovery_setting {
    recovery_mechanism {
      name     = "verified_email"
      priority = 1
    }
  }

  user_pool_add_ons {
    advanced_security_mode = var.advanced_security_mode
  }

  # Device remembering stays OFF (no device_configuration): a remembered device would skip MFA and
  # make the sos:mfa claim meaningless (ADR-0018).

  lambda_config {
    pre_token_generation_config {
      lambda_arn     = aws_lambda_function.pre_token.arn
      lambda_version = "V2_0"
    }
  }

  dynamic "email_configuration" {
    for_each = var.ses_email_identity_arn == null ? [] : [1]
    content {
      email_sending_account = "DEVELOPER"
      source_arn            = var.ses_email_identity_arn
      from_email_address    = var.from_email_address
    }
  }

  tags = merge(var.tags, { pool = each.key })
}

resource "aws_cognito_user_pool_domain" "this" {
  for_each = local.pools

  domain                = each.value.domain_prefix
  user_pool_id          = aws_cognito_user_pool.this[each.key].id
  managed_login_version = 2
}

resource "aws_cognito_user_pool_client" "this" {
  for_each = local.pools

  name         = each.value.client_name
  user_pool_id = aws_cognito_user_pool.this[each.key].id

  generate_secret                      = true
  allowed_oauth_flows_user_pool_client = true
  allowed_oauth_flows                  = ["code"]
  allowed_oauth_scopes                 = ["openid", "email", "profile"]
  supported_identity_providers         = ["COGNITO"]
  callback_urls                        = each.value.callback_urls
  logout_urls                          = each.value.logout_urls
  explicit_auth_flows                  = ["ALLOW_USER_SRP_AUTH", "ALLOW_REFRESH_TOKEN_AUTH"]

  prevent_user_existence_errors = "ENABLED"
  enable_token_revocation       = true
  auth_session_validity         = 3

  access_token_validity  = var.access_token_minutes
  id_token_validity      = var.access_token_minutes
  refresh_token_validity = var.refresh_token_hours
  token_validity_units {
    access_token  = "minutes"
    id_token      = "minutes"
    refresh_token = "hours"
  }

  # Every refresh returns a new refresh token and invalidates the old one (reuse => session revoked by the BFF).
  refresh_token_rotation {
    feature                    = "ENABLED"
    retry_grace_period_seconds = 0
  }

  read_attributes  = ["email", "email_verified", "preferred_username", "name"]
  write_attributes = ["name"]
}

resource "aws_secretsmanager_secret" "client" {
  for_each = local.pools

  name        = each.value.secret_name
  description = "OIDC client secret for ${each.value.client_name}"
  kms_key_id  = var.secrets_kms_key_arn
  tags        = var.tags
}

# The client secret is generated by Cognito (it is in the app client's state, marked sensitive); it is
# written to Secrets Manager write-only so it is not duplicated in the secret version's state.
resource "aws_secretsmanager_secret_version" "client" {
  for_each = local.pools

  secret_id                = aws_secretsmanager_secret.client[each.key].id
  secret_string_wo         = aws_cognito_user_pool_client.this[each.key].client_secret
  secret_string_wo_version = var.client_secret_version
}
