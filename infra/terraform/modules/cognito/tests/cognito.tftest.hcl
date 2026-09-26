# Identity (07 §5, SEC-004/005): staff pool MFA optional (app enforces for privileged roles),
# operators MFA ON, min 12 password, admin-created users, code flow, rotation, 10-minute tokens.

mock_provider "aws" {
  mock_data "aws_region" {
    defaults = { region = "ap-south-1" }
  }
}

variables {
  name_prefix            = "sos-test"
  tenant_domain_prefix   = "sos-test-schools"
  tenant_callback_urls   = ["https://app.example.test/api/auth/callback"]
  tenant_logout_urls     = ["https://app.example.test/"]
  platform_domain_prefix = "sos-test-ops"
  platform_callback_urls = ["https://admin.example.test/api/auth/platform/callback"]
  platform_logout_urls   = ["https://admin.example.test/"]
  secrets_kms_key_arn    = "arn:aws:kms:ap-south-1:111122223333:key/00000000-0000-0000-0000-000000000001"
  secret_name_prefix     = "sos/test"
}

run "pools" {
  command = plan

  assert {
    condition     = output.posture["tenant"].mfa == "OPTIONAL" && output.posture["tenant"].totp_mfa_available
    error_message = "Staff pool: TOTP available, MFA optional at the pool (enforced by the app for privileged roles)."
  }

  assert {
    condition     = output.posture["platform"].mfa == "ON"
    error_message = "Platform operators: MFA always on."
  }

  assert {
    condition     = alltrue([for p in values(output.posture) : p.min_password >= 12 && p.admin_create_only && p.advanced_security == "ENFORCED"])
    error_message = "Min 12 chars, no self sign-up, threat protection enforced."
  }
}

run "bff_client" {
  command = plan

  assert {
    condition     = alltrue([for p in values(output.posture) : p.oauth_flows == toset(["code"]) && p.refresh_rotation == "ENABLED" && p.access_token_min == 10])
    error_message = "Authorization code flow only, refresh rotation, 10-minute access tokens."
  }

  assert {
    condition     = alltrue([for c in aws_cognito_user_pool_client.this : c.generate_secret && c.prevent_user_existence_errors == "ENABLED"])
    error_message = "Confidential BFF client; no user enumeration."
  }

  assert {
    condition     = alltrue([for v in aws_secretsmanager_secret_version.client : v.secret_string == null])
    error_message = "Client secrets written to Secrets Manager write-only."
  }
}

run "dedicated_has_no_platform_pool" {
  command = plan

  variables {
    create_platform_pool = false
  }

  assert {
    condition     = length(aws_cognito_user_pool.this) == 1 && output.platform_issuer == null
    error_message = "Dedicated hosts have no operator pool."
  }
}
