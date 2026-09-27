# Shared-tier app containers get the settings apps/api/app/core/config.py reads, all of them, so each
# passes the staging/prod start-up guards (SEC-009, NFR-AVL-002); audit archives are signed with an
# asymmetric KMS key the worker may use (FR-AUD-004, SEC-011); the invoice supplier is validated
# (FR-PLT-016). apps/api/tests/deploy/test_env_contract.py checks the same maps against Settings.

mock_provider "aws" {
  mock_data "aws_iam_policy_document" {
    defaults = { json = "{\"Version\":\"2012-10-17\",\"Statement\":[]}" }
  }
  mock_data "aws_caller_identity" {
    defaults = { account_id = "444455556666" }
  }
  mock_data "aws_partition" {
    defaults = { partition = "aws" }
  }
  mock_data "aws_region" {
    defaults = { region = "ap-south-1" }
  }
}

variables {
  env                         = "staging"
  release_version             = "2026.10.1"
  app_domain                  = "app.staging.example.test"
  admin_domain                = "admin.staging.example.test"
  cognito_domain_prefix       = "sos-test-shared"
  github_deploy_environment   = "staging"
  billing_supplier_legal_name = "SchoolOS Staging Synthetic Supplier (not a tax invoice)"
  billing_supplier_gstin      = "37STAGE0000S1Z5"
}

run "every_app_container_gets_the_full_settings" {
  command = plan

  assert {
    condition = alltrue([
      for c in [module.api.container_definition, module.worker.container_definition, module.beat.container_definition, module.migrate.container_definition] :
      length(setsubtract(
        ["SOS_ENV", "SOS_DEPLOYMENT_MODE", "SOS_VERSION", "AWS_REGION", "SOS_KEY_WRAPPER", "SOS_KMS_DATA_KEY_ARN", "SOS_AUDIT_SIGNING_KEY_ARN", "SOS_BILLING_SUPPLIER_LEGAL_NAME", "SOS_BILLING_SUPPLIER_GSTIN", "SOS_BILLING_SUPPLIER_STATE_CODE", "SOS_S3_BUCKET_FILES", "SOS_S3_BUCKET_AUDIT", "SOS_OIDC_ISSUER", "SOS_OIDC_AUDIENCE", "SOS_SERVICE_NAME"],
        [for e in c.environment : e.name],
      )) == 0
    ])
    error_message = "api, worker, beat and migrate all get the base app settings."
  }

  assert {
    condition = alltrue([
      for c in [module.api.container_definition, module.worker.container_definition, module.beat.container_definition, module.migrate.container_definition] :
      length(setsubtract(["SOS_DATABASE_URL", "SOS_PLATFORM_DATABASE_URL", "SOS_REDIS_URL", "SOS_SERVICE_TOKEN_KEY"], [for s in c.secrets : s.name])) == 0
    ])
    error_message = "api, worker, beat and migrate all get the guarded secrets (no dev-only defaults)."
  }

  assert {
    condition     = contains([for s in module.migrate.container_definition.secrets : s.name], "SOS_MIGRATOR_DATABASE_URL")
    error_message = "migrate runs Alembic as sos_migrator."
  }

  assert {
    condition = alltrue([
      for c in [module.api.container_definition, module.worker.container_definition, module.beat.container_definition, module.migrate.container_definition] :
      length(setintersection(["SOS_KMS_KEY_ARN", "SOS_FLEET_URL", "SOS_FLEET_HMAC_KEY"], concat([for e in c.environment : e.name], [for s in c.secrets : s.name]))) == 0
    ])
    error_message = "Old names that config.py never read are gone."
  }

  assert {
    condition = alltrue([
      for c in [module.api.container_definition, module.worker.container_definition, module.beat.container_definition, module.migrate.container_definition] :
      [for e in c.environment : e.value if e.name == "SOS_KEY_WRAPPER"] == ["kms"] && [for e in c.environment : e.value if e.name == "SOS_DEPLOYMENT_MODE"] == ["shared"]
    ])
    error_message = "Every container uses the KMS key wrapper in shared mode."
  }

  assert {
    condition = (
      [for e in module.api.container_definition.environment : e.value if e.name == "SOS_BILLING_SUPPLIER_GSTIN"] == ["37STAGE0000S1Z5"]
      && [for e in module.api.container_definition.environment : e.value if e.name == "SOS_BILLING_SUPPLIER_STATE_CODE"] == ["37"]
    )
    error_message = "The invoice supplier reaches the containers."
  }

  assert {
    condition = (
      length(setsubtract(["SOS_ANTHROPIC_API_KEY", "SOS_EMBEDDINGS_API_KEY"], [for s in module.worker.container_definition.secrets : s.name])) == 0
      && length(setintersection(["SOS_ANTHROPIC_API_KEY", "SOS_EMBEDDINGS_API_KEY"], [for s in module.beat.container_definition.secrets : s.name])) == 0
      && length(setintersection(["SOS_ANTHROPIC_API_KEY", "SOS_EMBEDDINGS_API_KEY"], [for s in module.migrate.container_definition.secrets : s.name])) == 0
    )
    error_message = "Provider API keys go only to api and worker."
  }

  assert {
    condition     = toset(split(",", module.worker.container_definition.command[6])) == toset(["ingest", "embed", "ocr", "dq", "exports", "pdf", "maintenance"])
    error_message = "The worker consumes every Celery queue, including maintenance (beat jobs)."
  }
}

run "audit_archives_are_signed_by_the_worker" {
  command = plan

  assert {
    condition     = output.security_posture.audit_signing_key.key_spec == "ECC_NIST_P256" && output.security_posture.audit_signing_key.key_usage == "SIGN_VERIFY" && !output.security_posture.audit_signing_key.rotation
    error_message = "An ECC_NIST_P256 SIGN_VERIFY key signs audit archives (no automatic rotation for asymmetric keys)."
  }

  assert {
    condition = anytrue([
      for s in data.aws_iam_policy_document.worker.statement :
      s.sid == "AuditSigning" && toset(s.actions) == toset(["kms:Sign", "kms:GetPublicKey"]) && length(s.resources) == 1
    ])
    error_message = "The worker task role may kms:Sign and kms:GetPublicKey with the signing key only."
  }

  assert {
    condition     = alltrue([for s in data.aws_iam_policy_document.api.statement : !contains(s.actions, "kms:Sign")])
    error_message = "The API never signs audit archives."
  }
}

# SEC-016, SEC-010 (docs/07 §10, §11): browsers upload with presigned POST straight to the files bucket,
# so the bucket allows exactly the app origin to POST, and the web task's CSP gets the bucket origin.
run "browser_uploads_reach_the_files_bucket" {
  command = plan

  assert {
    condition = (
      length(module.s3.files_cors_rules) == 1
      && module.s3.files_cors_rules[0].allowed_origins == toset(["https://app.staging.example.test"])
      && module.s3.files_cors_rules[0].allowed_methods == toset(["POST"])
    )
    error_message = "The files bucket allows presigned POST from https://<app_domain> only (not the admin domain)."
  }

  assert {
    condition     = [for e in module.web.container_definition.environment : e.value if e.name == "FILES_ORIGIN"] == ["https://sos-staging-files-444455556666.s3.ap-south-1.amazonaws.com"]
    error_message = "The web task gets FILES_ORIGIN = the files bucket's regional virtual-hosted origin."
  }

  assert {
    condition     = output.files_browser_origin == "https://sos-staging-files-444455556666.s3.ap-south-1.amazonaws.com"
    error_message = "The files origin is output for operators."
  }

  assert {
    condition = alltrue([
      for c in [module.api.container_definition, module.worker.container_definition, module.beat.container_definition, module.migrate.container_definition] :
      !contains([for e in c.environment : e.name], "FILES_ORIGIN")
    ])
    error_message = "FILES_ORIGIN is a web (BFF) setting only."
  }
}

run "gstin_must_be_valid" {
  command = plan

  variables {
    billing_supplier_gstin = "37ABCDE1234F1Z"
  }

  expect_failures = [var.billing_supplier_gstin]
}

run "gstin_dev_placeholder_refused" {
  command = plan

  variables {
    billing_supplier_gstin = "37AAAAA0000A1Z5"
  }

  expect_failures = [var.billing_supplier_gstin]
}

run "gstin_matches_state_code" {
  command = plan

  variables {
    billing_supplier_gstin = "36ABCDE1234F1Z5"
  }

  expect_failures = [var.billing_supplier_gstin]
}

run "supplier_name_dev_placeholder_refused" {
  command = plan

  variables {
    billing_supplier_legal_name = "SchoolOS Synthetic Supplier (dev)"
  }

  expect_failures = [var.billing_supplier_legal_name]
}
