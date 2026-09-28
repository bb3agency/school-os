# Break-glass support app client (ADR-0023 option C; US-103, FR-OPS-004, SEC-005, SEC-021).
#
# A third app client in the OPERATOR pool, used only by the school app's BFF (/bff/auth/support/*)
# when an operator uses an approved break-glass grant. The operator pool has MFA ON and its
# pre-token-generation Lambda adds sos:mfa to every token the pool issues, whatever the client
# (modules/cognito/lambda.tf), so this client's tokens carry it without a Lambda change. The API
# accepts these tokens on tenant routes only with this client_id, token_use = access and
# sos:mfa = "true" (SOS_SUPPORT_OIDC_AUDIENCE); the operator admin client is never accepted there.
#
# Confidential (the secret stays in the BFF), authorization code flow only. The BFF always sends
# PKCE (S256); Cognito has no per-client "PKCE required" switch, so that is enforced by the BFF
# (apps/web/src/server/auth). Access/ID tokens 10 minutes, refresh rotation on.
# Used by modules/cognito (shared tier, one per environment) and envs/dedicated-template (one per
# dedicated host, in the prod operator pool).

data "aws_region" "current" {}

resource "aws_cognito_user_pool_client" "this" {
  name         = var.name
  user_pool_id = var.user_pool_id

  generate_secret                      = true
  allowed_oauth_flows_user_pool_client = true
  allowed_oauth_flows                  = ["code"]
  allowed_oauth_scopes                 = ["openid", "email", "profile"]
  supported_identity_providers         = ["COGNITO"]
  callback_urls                        = var.callback_urls
  logout_urls                          = var.logout_urls
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

  # Every refresh returns a new refresh token and invalidates the old one (reuse => session revoked).
  refresh_token_rotation {
    feature                    = "ENABLED"
    retry_grace_period_seconds = 0
  }

  # Read-only profile: support sessions never change the operator's attributes.
  read_attributes  = ["email", "email_verified", "preferred_username", "name"]
  write_attributes = []
}

resource "aws_secretsmanager_secret" "client" {
  name        = var.secret_name
  description = "OIDC client secret for ${var.name} (break-glass support sign-in, ADR-0023)"
  kms_key_id  = var.secrets_kms_key_arn
  tags        = var.tags
}

# Written write-only, like the other client secrets (modules/cognito).
resource "aws_secretsmanager_secret_version" "client" {
  secret_id                = aws_secretsmanager_secret.client.id
  secret_string_wo         = aws_cognito_user_pool_client.this.client_secret
  secret_string_wo_version = var.client_secret_version
}
