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
  billing_supplier_address    = "Synthetic staging supplier (not a tax invoice); Vijayawada 520001, Andhra Pradesh"
}

run "every_app_container_gets_the_full_settings" {
  command = plan

  assert {
    condition = alltrue([
      for c in [module.api.container_definition, module.worker.container_definition, module.worker_pdf.container_definition, module.beat.container_definition, module.migrate.container_definition] :
      length(setsubtract(
        ["SOS_ENV", "SOS_DEPLOYMENT_MODE", "SOS_VERSION", "AWS_REGION", "SOS_KEY_WRAPPER", "SOS_KMS_DATA_KEY_ARN", "SOS_AUDIT_SIGNING_KEY_ARN", "SOS_BILLING_SUPPLIER_LEGAL_NAME", "SOS_BILLING_SUPPLIER_GSTIN", "SOS_BILLING_SUPPLIER_STATE_CODE", "SOS_BILLING_SUPPLIER_ADDRESS", "SOS_S3_BUCKET_FILES", "SOS_S3_BUCKET_AUDIT", "SOS_OIDC_ISSUER", "SOS_OIDC_AUDIENCE", "SOS_SERVICE_NAME"],
        [for e in c.environment : e.name],
      )) == 0
    ])
    error_message = "api, worker, worker-pdf, beat and migrate all get the base app settings."
  }

  assert {
    condition = alltrue([
      for c in [module.api.container_definition, module.worker.container_definition, module.worker_pdf.container_definition, module.beat.container_definition, module.migrate.container_definition] :
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
      && length(setintersection(["SOS_ANTHROPIC_API_KEY", "SOS_EMBEDDINGS_API_KEY"], [for s in module.worker_pdf.container_definition.secrets : s.name])) == 0
      && length(setintersection(["SOS_ANTHROPIC_API_KEY", "SOS_EMBEDDINGS_API_KEY"], [for s in module.migrate.container_definition.secrets : s.name])) == 0
    )
    error_message = "Provider API keys go only to api and worker."
  }

  assert {
    condition     = toset(split(",", module.worker.container_definition.command[6])) == toset(["ingest", "embed", "ocr", "dq", "exports", "maintenance"])
    error_message = "The Fargate worker consumes every Celery queue except pdf, including maintenance (beat jobs)."
  }

  assert {
    condition     = join(" ", module.worker_pdf.container_definition.command) == "celery -A sos_worker.celery_app worker --loglevel=INFO --concurrency=2 -Q pdf"
    error_message = "worker-pdf consumes only the pdf queue (ADR-0025)."
  }
}

# ADR-0025 option A (FR-EXP-002, SEC-030): the pdf queue runs on EC2 capacity whose daemon allows the
# Chromium sandbox; the service keeps every container control and is pinned to that capacity.
run "pdf_worker_runs_on_the_sandbox_capacity" {
  command = plan

  assert {
    condition     = contains(module.cluster.ec2_capacity_providers, "sos-staging-pdf") && one(module.cluster.ec2_capacity_providers) == "sos-staging-pdf"
    error_message = "The cluster has exactly one EC2 capacity provider, the pdf capacity."
  }

  assert {
    condition = (
      one(module.worker_pdf.container_definition.dockerSecurityOptions) == "no-new-privileges"
      && module.worker_pdf.container_definition.readonlyRootFilesystem
      && !module.worker_pdf.container_definition.privileged
      && module.worker_pdf.container_definition.user == "10001:10001"
      && module.worker_pdf.container_definition.linuxParameters.capabilities.drop == ["ALL"]
      && module.worker_pdf.container_definition.linuxParameters.tmpfs[0].containerPath == "/tmp"
    )
    error_message = "worker-pdf: read-only root, non-root, all capabilities dropped, no-new-privileges, tmpfs scratch."
  }

  assert {
    condition     = [for e in module.worker_pdf.container_definition.environment : e.value if e.name == "SOS_SERVICE_NAME"] == ["worker-pdf"]
    error_message = "worker-pdf identifies itself in logs and metrics."
  }

  assert {
    condition     = output.pdf_capacity.posture.imdsv2_required && output.pdf_capacity.posture.root_encrypted && output.pdf_capacity.posture.key_name == null
    error_message = "The pdf capacity is hardened (IMDSv2, encrypted root, no key pair)."
  }

  assert {
    condition     = contains(output.ecs_services, "sos-staging-worker-pdf") && length(output.ecs_services) == 5
    error_message = "The deploy pipeline rolls web, api, worker, worker-pdf and beat."
  }
}

run "fargate_worker_must_not_take_pdf" {
  command = plan

  variables {
    worker_queues = "ingest,embed,ocr,dq,exports,pdf,maintenance"
  }

  expect_failures = [var.worker_queues]
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

# FR-EXP-002 (docs/04 §6): worker and beat run the worker image (Chromium for the pdf queue); the api,
# migrate and db-bootstrap tasks keep the smaller api image. SEC-011 / FR-DOC-003: every container that
# writes the files bucket names the data CMK for SSE-KMS.
run "worker_image_and_upload_encryption" {
  command = plan

  override_module {
    target = module.ecr
    outputs = {
      repository_urls = {
        api    = "444455556666.dkr.ecr.ap-south-1.amazonaws.com/schoolos/api"
        worker = "444455556666.dkr.ecr.ap-south-1.amazonaws.com/schoolos/worker"
        web    = "444455556666.dkr.ecr.ap-south-1.amazonaws.com/schoolos/web"
      }
      repository_arns = {
        api    = "arn:aws:ecr:ap-south-1:444455556666:repository/schoolos/api"
        worker = "arn:aws:ecr:ap-south-1:444455556666:repository/schoolos/worker"
        web    = "arn:aws:ecr:ap-south-1:444455556666:repository/schoolos/web"
      }
      posture = {}
    }
  }

  override_module {
    target = module.kms
    outputs = {
      key_arns = {
        data          = "arn:aws:kms:ap-south-1:444455556666:key/00000000-0000-0000-0000-00000000da7a"
        audit         = "arn:aws:kms:ap-south-1:444455556666:key/00000000-0000-0000-0000-0000000a0d17"
        backup        = "arn:aws:kms:ap-south-1:444455556666:key/00000000-0000-0000-0000-00000000bac0"
        logs          = "arn:aws:kms:ap-south-1:444455556666:key/00000000-0000-0000-0000-000000000109"
        audit-signing = "arn:aws:kms:ap-south-1:444455556666:key/00000000-0000-0000-0000-00000000519e"
      }
      key_properties = {
        audit-signing = { key_spec = "ECC_NIST_P256", key_usage = "SIGN_VERIFY", rotation = false }
      }
    }
  }

  assert {
    condition = (
      module.worker.container_definition.image == "444455556666.dkr.ecr.ap-south-1.amazonaws.com/schoolos/worker:2026.10.1"
      && module.beat.container_definition.image == "444455556666.dkr.ecr.ap-south-1.amazonaws.com/schoolos/worker:2026.10.1"
      && module.worker_pdf.container_definition.image == "444455556666.dkr.ecr.ap-south-1.amazonaws.com/schoolos/worker:2026.10.1"
      && module.api.container_definition.image == "444455556666.dkr.ecr.ap-south-1.amazonaws.com/schoolos/api:2026.10.1"
      && module.migrate.container_definition.image == "444455556666.dkr.ecr.ap-south-1.amazonaws.com/schoolos/api:2026.10.1"
    )
    error_message = "worker and beat run the worker image (Chromium); api and migrate run the api image."
  }

  assert {
    condition = alltrue([
      for c in [module.api.container_definition, module.worker.container_definition, module.worker_pdf.container_definition] :
      [for e in c.environment : e.value if e.name == "SOS_S3_KMS_KEY_ID"] == ["arn:aws:kms:ap-south-1:444455556666:key/00000000-0000-0000-0000-00000000da7a"]
    ])
    error_message = "api and worker encrypt uploads with the data CMK, the files bucket's key (SOS_S3_KMS_KEY_ID)."
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

# FR-IAM-001, SEC-004: Cognito redirects exactly where the BFF sends people. apps/web/src/server/config.ts
# builds every redirect and post-logout URI from APP_BASE_URL (https://app_domain), operators included.
run "oidc_redirects_match_the_bff" {
  command = plan

  assert {
    condition = (
      module.cognito.posture["tenant"].callback_urls == toset(["https://app.staging.example.test/bff/auth/callback"])
      && module.cognito.posture["tenant"].logout_urls == toset(["https://app.staging.example.test/signed-out"])
    )
    error_message = "Staff client: <APP_BASE_URL>/bff/auth/callback and /signed-out."
  }

  assert {
    condition = (
      module.cognito.posture["platform"].callback_urls == toset(["https://app.staging.example.test/bff/auth/platform/callback"])
      && module.cognito.posture["platform"].logout_urls == toset(["https://app.staging.example.test/signed-out?kind=operator"])
    )
    error_message = "Operator admin client: <APP_BASE_URL>/bff/auth/platform/callback and /signed-out?kind=operator."
  }
}

# ADR-0023 option C (US-103, FR-OPS-004, SEC-021): the support app client of the operator pool exists per
# environment; the API and workers know its audience, the web BFF its client ID and secret.
run "support_client_is_wired" {
  command = plan

  assert {
    condition = (
      module.cognito.support_posture.callback_urls == toset(["https://app.staging.example.test/bff/auth/support/callback"])
      && module.cognito.support_posture.logout_urls == toset(["https://app.staging.example.test/signed-out?kind=support"])
      && module.cognito.support_posture.access_token_min == 10
      && module.cognito.support_posture.refresh_rotation == "ENABLED"
    )
    error_message = "Support client: BFF support callback and sign-out on app_domain, 10-minute tokens, rotation."
  }

  assert {
    condition = alltrue([
      for c in [module.api.container_definition, module.worker.container_definition, module.worker_pdf.container_definition, module.beat.container_definition, module.migrate.container_definition] :
      contains([for e in c.environment : e.name], "SOS_SUPPORT_OIDC_AUDIENCE")
      && !contains([for e in c.environment : e.name], "SOS_SUPPORT_OIDC_ISSUER")
    ])
    error_message = "Every app container gets the support audience; the issuer defaults to the operator pool's (SOS_PLATFORM_OIDC_ISSUER)."
  }

  assert {
    condition = (
      contains([for e in module.web.container_definition.environment : e.name], "SUPPORT_OIDC_CLIENT_ID")
      && contains([for s in module.web.container_definition.secrets : s.name], "SUPPORT_OIDC_CLIENT_SECRET")
      && !contains([for e in module.web.container_definition.environment : e.name], "SUPPORT_OIDC_CLIENT_SECRET")
    )
    error_message = "The web BFF gets the support client ID, and its secret from Secrets Manager only."
  }

  assert {
    condition = alltrue([
      for c in [module.api.container_definition, module.worker.container_definition, module.beat.container_definition, module.migrate.container_definition] :
      !contains([for s in c.secrets : s.name], "SUPPORT_OIDC_CLIENT_SECRET")
    ])
    error_message = "Only the BFF holds the support client secret."
  }
}

# FR-PLT-016/017 (invoice PDFs, docs/16 §5.8): the supplier address reaches every app container
# (worker-pdf renders); a separate invoice bucket is optional and, when set, named in the env and
# the task role.
run "invoice_pdf_settings" {
  command = plan

  assert {
    condition = alltrue([
      for c in [module.api.container_definition, module.worker.container_definition, module.worker_pdf.container_definition] :
      [for e in c.environment : e.value if e.name == "SOS_BILLING_SUPPLIER_ADDRESS"] == ["Synthetic staging supplier (not a tax invoice); Vijayawada 520001, Andhra Pradesh"]
      && !contains([for e in c.environment : e.name], "SOS_PLATFORM_INVOICE_BUCKET")
    ])
    error_message = "api, worker and worker-pdf get the supplier address; no invoice bucket unless configured (files bucket)."
  }
}

run "invoice_pdf_separate_bucket" {
  command = plan

  variables {
    platform_invoice_bucket = "sos-staging-invoices-444455556666"
  }

  assert {
    condition = alltrue([
      for c in [module.api.container_definition, module.worker.container_definition, module.worker_pdf.container_definition] :
      [for e in c.environment : e.value if e.name == "SOS_PLATFORM_INVOICE_BUCKET"] == ["sos-staging-invoices-444455556666"]
    ])
    error_message = "The configured invoice bucket reaches the containers that render and sign."
  }
}

run "invoice_address_dev_placeholder_refused" {
  command = plan

  variables {
    billing_supplier_address = "Synthetic supplier address (dev); Vijayawada 520001, Andhra Pradesh"
  }

  expect_failures = [var.billing_supplier_address]
}
