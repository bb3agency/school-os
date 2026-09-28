output "issuer" {
  description = "Issuer of support tokens = the operator pool (SOS_SUPPORT_OIDC_ISSUER / SUPPORT_OIDC_ISSUER)."
  value       = "https://cognito-idp.${data.aws_region.current.region}.amazonaws.com/${var.user_pool_id}"
}

output "client_id" {
  description = "Support app client ID (SOS_SUPPORT_OIDC_AUDIENCE on app containers, SUPPORT_OIDC_CLIENT_ID on web)."
  value       = aws_cognito_user_pool_client.this.id
}

output "client_secret_arn" {
  description = "Secrets Manager ARN of the support client secret (SUPPORT_OIDC_CLIENT_SECRET on web)."
  value       = aws_secretsmanager_secret.client.arn
}

output "posture" {
  description = "Security posture summary (asserted by tests)."
  value = {
    confidential        = aws_cognito_user_pool_client.this.generate_secret
    oauth_flows         = aws_cognito_user_pool_client.this.allowed_oauth_flows
    scopes              = aws_cognito_user_pool_client.this.allowed_oauth_scopes
    access_token_min    = aws_cognito_user_pool_client.this.access_token_validity
    id_token_min        = aws_cognito_user_pool_client.this.id_token_validity
    refresh_rotation    = one(aws_cognito_user_pool_client.this.refresh_token_rotation).feature
    token_revocation    = aws_cognito_user_pool_client.this.enable_token_revocation
    callback_urls       = aws_cognito_user_pool_client.this.callback_urls
    logout_urls         = aws_cognito_user_pool_client.this.logout_urls
    no_user_enumeration = aws_cognito_user_pool_client.this.prevent_user_existence_errors == "ENABLED"
  }
}
