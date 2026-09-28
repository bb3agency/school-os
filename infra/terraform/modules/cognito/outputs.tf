output "tenant_issuer" {
  description = "OIDC issuer for school staff (SOS_OIDC_ISSUER / OIDC_ISSUER)."
  value       = "https://cognito-idp.${data.aws_region.current.region}.amazonaws.com/${aws_cognito_user_pool.this["tenant"].id}"
}

output "tenant_client_id" {
  description = "BFF app client ID (OIDC_CLIENT_ID; also SOS_OIDC_AUDIENCE)."
  value       = aws_cognito_user_pool_client.this["tenant"].id
}

output "tenant_client_secret_arn" {
  description = "Secrets Manager ARN of the BFF client secret (OIDC_CLIENT_SECRET)."
  value       = aws_secretsmanager_secret.client["tenant"].arn
}

output "tenant_user_pool_id" {
  description = "School-staff user pool ID."
  value       = aws_cognito_user_pool.this["tenant"].id
}

output "tenant_user_pool_arn" {
  description = "School-staff user pool ARN."
  value       = aws_cognito_user_pool.this["tenant"].arn
}

output "tenant_hosted_domain" {
  description = "Hosted login domain for school staff."
  value       = "${aws_cognito_user_pool_domain.this["tenant"].domain}.auth.${data.aws_region.current.region}.amazoncognito.com"
}

output "platform_issuer" {
  description = "OIDC issuer for platform operators (SOS_PLATFORM_OIDC_ISSUER)."
  value       = var.create_platform_pool ? "https://cognito-idp.${data.aws_region.current.region}.amazonaws.com/${aws_cognito_user_pool.this["platform"].id}" : null
}

output "platform_client_id" {
  description = "Platform admin app client ID (PLATFORM_OIDC_CLIENT_ID; also SOS_PLATFORM_OIDC_AUDIENCE)."
  value       = var.create_platform_pool ? aws_cognito_user_pool_client.this["platform"].id : null
}

output "platform_client_secret_arn" {
  description = "Secrets Manager ARN of the platform admin client secret."
  value       = var.create_platform_pool ? aws_secretsmanager_secret.client["platform"].arn : null
}

output "platform_user_pool_id" {
  description = "Platform-operator user pool ID (dedicated roots put their own support client in it: envs/dedicated-template operator_user_pool_id)."
  value       = var.create_platform_pool ? aws_cognito_user_pool.this["platform"].id : null
}

output "platform_user_pool_arn" {
  description = "Platform-operator user pool ARN."
  value       = var.create_platform_pool ? aws_cognito_user_pool.this["platform"].arn : null
}

output "support_issuer" {
  description = "Issuer of break-glass support tokens = the operator pool (SOS_SUPPORT_OIDC_ISSUER / SUPPORT_OIDC_ISSUER); null when off."
  value       = var.create_support_client ? module.support[0].issuer : null
}

output "support_client_id" {
  description = "Support app client ID (SOS_SUPPORT_OIDC_AUDIENCE, SUPPORT_OIDC_CLIENT_ID); null when off."
  value       = var.create_support_client ? module.support[0].client_id : null
}

output "support_client_secret_arn" {
  description = "Secrets Manager ARN of the support client secret (SUPPORT_OIDC_CLIENT_SECRET); null when off."
  value       = var.create_support_client ? module.support[0].client_secret_arn : null
}

output "support_posture" {
  description = "Support client posture (asserted by tests); null when off."
  value       = var.create_support_client ? module.support[0].posture : null
}

output "posture" {
  description = "Security posture summary (asserted by tests)."
  value = {
    for k, p in aws_cognito_user_pool.this : k => {
      mfa                = p.mfa_configuration
      tier               = p.user_pool_tier
      min_password       = one(p.password_policy).minimum_length
      advanced_security  = one(p.user_pool_add_ons).advanced_security_mode
      admin_create_only  = one(p.admin_create_user_config).allow_admin_create_user_only
      refresh_rotation   = one(aws_cognito_user_pool_client.this[k].refresh_token_rotation).feature
      access_token_min   = aws_cognito_user_pool_client.this[k].access_token_validity
      oauth_flows        = aws_cognito_user_pool_client.this[k].allowed_oauth_flows
      callback_urls      = aws_cognito_user_pool_client.this[k].callback_urls
      logout_urls        = aws_cognito_user_pool_client.this[k].logout_urls
      totp_mfa_available = one(p.software_token_mfa_configuration).enabled
      device_remembering = length(p.device_configuration) > 0
      pre_token_version  = one(one(p.lambda_config).pre_token_generation_config).lambda_version
    }
  }
}
