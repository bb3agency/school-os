# ADR-0023 option C (US-103, FR-OPS-004, SEC-005, SEC-021): the break-glass support app client of the
# operator pool is confidential, code flow only, 10-minute tokens with refresh rotation, and redirects
# only to the BFF's support callback and signed-out page on the school app host.

mock_provider "aws" {
  mock_data "aws_region" {
    defaults = { region = "ap-south-1" }
  }
}

variables {
  name                = "sos-test-support"
  user_pool_id        = "ap-south-1_OpsPool"
  callback_urls       = ["https://app.example.test/bff/auth/support/callback"]
  logout_urls         = ["https://app.example.test/signed-out?kind=support"]
  secret_name         = "sos/test/oidc/support-client-secret"
  secrets_kms_key_arn = "arn:aws:kms:ap-south-1:111122223333:key/00000000-0000-0000-0000-000000000001"
}

run "support_client" {
  command = plan

  assert {
    condition     = output.posture.confidential && output.posture.oauth_flows == toset(["code"])
    error_message = "Confidential client (secret held by the BFF), authorization code flow only."
  }

  assert {
    condition     = output.posture.scopes == toset(["openid", "profile", "email"])
    error_message = "Scopes openid profile email (the BFF's OIDC_SCOPE)."
  }

  assert {
    condition     = output.posture.access_token_min == 10 && output.posture.id_token_min == 10 && output.posture.refresh_rotation == "ENABLED" && output.posture.token_revocation
    error_message = "10-minute access/ID tokens, refresh rotation and revocation (ADR-0023)."
  }

  assert {
    condition     = output.posture.callback_urls == toset(["https://app.example.test/bff/auth/support/callback"]) && output.posture.logout_urls == toset(["https://app.example.test/signed-out?kind=support"])
    error_message = "Redirects only to the BFF support callback and the support signed-out page."
  }

  assert {
    condition     = aws_secretsmanager_secret_version.client.secret_string == null && output.posture.no_user_enumeration
    error_message = "Client secret written write-only; no user enumeration."
  }

  assert {
    condition     = output.issuer == "https://cognito-idp.ap-south-1.amazonaws.com/ap-south-1_OpsPool"
    error_message = "The issuer is the operator pool's."
  }
}

run "refuses_a_foreign_callback" {
  command = plan

  variables {
    callback_urls = ["https://app.example.test/bff/auth/callback"]
  }

  expect_failures = [var.callback_urls]
}

run "refuses_plain_http" {
  command = plan

  variables {
    logout_urls = ["http://app.example.test/signed-out?kind=support"]
  }

  expect_failures = [var.logout_urls]
}

run "refuses_long_access_tokens" {
  command = plan

  variables {
    access_token_minutes = 60
  }

  expect_failures = [var.access_token_minutes]
}
