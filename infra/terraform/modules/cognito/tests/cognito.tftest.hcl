# Identity (07 §5, SEC-004/005): staff pool MFA optional (app enforces for privileged roles),
# operators MFA ON, min 12 password, admin-created users, code flow, rotation, 10-minute tokens.

mock_provider "aws" {
  mock_data "aws_region" {
    defaults = { region = "ap-south-1" }
  }
  mock_data "aws_iam_policy_document" {
    defaults = { json = "{\"Version\":\"2012-10-17\",\"Statement\":[]}" }
  }
  mock_data "aws_caller_identity" {
    defaults = { account_id = "111122223333" }
  }
  mock_data "aws_partition" {
    defaults = { partition = "aws" }
  }
  mock_resource "aws_iam_role" {
    defaults = { arn = "arn:aws:iam::111122223333:role/mock" }
  }
  mock_resource "aws_cloudwatch_log_group" {
    defaults = { arn = "arn:aws:logs:ap-south-1:111122223333:log-group:mock" }
  }
  mock_resource "aws_cognito_user_pool" {
    defaults = {
      id  = "ap-south-1_MockPool"
      arn = "arn:aws:cognito-idp:ap-south-1:111122223333:userpool/ap-south-1_MockPool"
    }
  }
  mock_resource "aws_lambda_function" {
    defaults = { arn = "arn:aws:lambda:ap-south-1:111122223333:function:mock" }
  }
}

variables {
  name_prefix            = "sos-test"
  tenant_domain_prefix   = "sos-test-schools"
  tenant_callback_urls   = ["https://app.example.test/bff/auth/callback"]
  tenant_logout_urls     = ["https://app.example.test/signed-out"]
  platform_domain_prefix = "sos-test-ops"
  platform_callback_urls = ["https://app.example.test/bff/auth/platform/callback"]
  platform_logout_urls   = ["https://app.example.test/signed-out?kind=operator"]
  secrets_kms_key_arn    = "arn:aws:kms:ap-south-1:111122223333:key/00000000-0000-0000-0000-000000000001"
  secret_name_prefix     = "sos/test"
  logs_kms_key_arn       = "arn:aws:kms:ap-south-1:111122223333:key/00000000-0000-0000-0000-000000000002"
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
    condition     = alltrue([for p in values(output.posture) : p.min_password >= 12 && p.admin_create_only && p.tier == "ESSENTIALS"])
    error_message = "Min 12 chars, no self sign-up, Essentials plan (ADR-0018)."
  }

  assert {
    condition     = alltrue([for p in values(output.posture) : !p.device_remembering && p.pre_token_version == "V2_0"])
    error_message = "Device remembering off and the sos:mfa pre-token Lambda (V2_0) attached to every pool (ADR-0018)."
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

# ADR-0023 option C (US-103, FR-OPS-004, SEC-005, SEC-021): a third app client in the OPERATOR pool for
# break-glass support sign-in to the school app. MFA comes from the pool (ON) and the pool's
# pre-token Lambda (sos:mfa for every client of the pool).
run "support_client_in_the_operator_pool" {
  command = apply

  variables {
    create_support_client = true
    support_callback_urls = ["https://app.example.test/bff/auth/support/callback"]
    support_logout_urls   = ["https://app.example.test/signed-out?kind=support"]
  }

  assert {
    condition     = output.support_issuer == output.platform_issuer
    error_message = "Support tokens come from the operator pool (never the staff pool)."
  }

  assert {
    condition = (
      output.support_posture.confidential
      && output.support_posture.oauth_flows == toset(["code"])
      && output.support_posture.access_token_min == 10
      && output.support_posture.refresh_rotation == "ENABLED"
      && output.support_posture.callback_urls == toset(["https://app.example.test/bff/auth/support/callback"])
      && output.support_posture.logout_urls == toset(["https://app.example.test/signed-out?kind=support"])
    )
    error_message = "Confidential code-flow client, 10-minute tokens, rotation, BFF support redirects only."
  }

  assert {
    condition     = output.posture["platform"].mfa == "ON" && output.posture["platform"].pre_token_version == "V2_0"
    error_message = "The pool that issues support tokens has MFA ON and the sos:mfa pre-token Lambda."
  }
}

run "support_client_off_by_default" {
  command = plan

  assert {
    condition     = output.support_client_id == null && output.support_issuer == null && output.support_posture == null
    error_message = "No support client unless asked for (dedicated roots create their own)."
  }
}

run "support_client_needs_the_operator_pool" {
  command = plan

  variables {
    create_platform_pool  = false
    create_support_client = true
    support_callback_urls = ["https://app.example.test/bff/auth/support/callback"]
    support_logout_urls   = ["https://app.example.test/signed-out?kind=support"]
  }

  expect_failures = [var.create_support_client]
}
