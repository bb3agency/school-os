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
    condition     = contains(output.ecs_services, "sos-staging-worker-pdf") && length(output.ecs_services) == 5 && output.ecs_services[0] == "sos-staging-api"
    error_message = "The deploy pipeline rolls api (first: the migrate task reuses its network configuration), web, worker, worker-pdf and beat."
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
        artifacts     = "arn:aws:kms:ap-south-1:444455556666:key/00000000-0000-0000-0000-0000000a7f00"
        logs          = "arn:aws:kms:ap-south-1:444455556666:key/00000000-0000-0000-0000-000000000109"
        audit-signing = "arn:aws:kms:ap-south-1:444455556666:key/00000000-0000-0000-0000-00000000519e"
      }
      key_properties = {
        audit-signing = { key_spec = "ECC_NIST_P256", key_usage = "SIGN_VERIFY", rotation = false }
      }
    }
  }

  # Audit 2026-10-05 key separation: every dedicated host may decrypt release bundles, so the
  # artifacts bucket has its own key, never the data key.
  assert {
    condition     = module.s3.artifacts_kms_key_arn == "arn:aws:kms:ap-south-1:444455556666:key/00000000-0000-0000-0000-0000000a7f00"
    error_message = "The artifacts bucket uses its own CMK (kms_key_arns.artifacts), not the data key."
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

  assert {
    condition     = module.redis.slow_log_kms_key_arn == "arn:aws:kms:ap-south-1:444455556666:key/00000000-0000-0000-0000-000000000109"
    error_message = "The Valkey slow-log group uses the logs CMK: the data key does not grant CloudWatch Logs, so CreateLogGroup would fail."
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

# US-102, FR-IAM-013 (staff invitation email through SES): off by default; when on, api and worker get
# the provider, sender, https app URL and configuration set, and only the worker may send.
run "email_off_by_default" {
  command = plan

  assert {
    condition = alltrue([
      for c in [module.api.container_definition, module.worker.container_definition] :
      [for e in c.environment : e.value if e.name == "SOS_EMAIL_PROVIDER"] == ["off"]
      && !contains([for e in c.environment : e.name], "SOS_EMAIL_FROM")
    ])
    error_message = "Email is off unless configured."
  }

  assert {
    condition     = length(module.ses) == 0 && output.ses == null
    error_message = "No SES identity without email_domain."
  }
}

run "email_through_ses" {
  command = plan

  variables {
    email_provider = "ses"
    email_domain   = "mail.staging.example.test"
    email_from     = "SchoolOS <no-reply@mail.staging.example.test>"
  }

  assert {
    condition = alltrue([
      for c in [module.api.container_definition, module.worker.container_definition] :
      [for e in c.environment : e.value if e.name == "SOS_EMAIL_PROVIDER"] == ["ses"]
      && [for e in c.environment : e.value if e.name == "SOS_EMAIL_FROM"] == ["SchoolOS <no-reply@mail.staging.example.test>"]
      && [for e in c.environment : e.value if e.name == "SOS_EMAIL_APP_URL"] == ["https://app.staging.example.test"]
      && [for e in c.environment : e.value if e.name == "SOS_EMAIL_SES_CONFIGURATION_SET"] == ["sos-staging-email"]
    ])
    error_message = "api and worker get the SES provider, sender, https app URL and configuration set."
  }

  assert {
    condition = alltrue([
      for c in [module.beat.container_definition, module.worker_pdf.container_definition, module.migrate.container_definition] :
      !contains([for e in c.environment : e.name], "SOS_EMAIL_PROVIDER")
    ])
    error_message = "Only api (queues) and worker (sends) know about email."
  }

  assert {
    condition     = module.ses[0].posture.tls_policy == "REQUIRE" && module.ses[0].posture.default_config_set == "sos-staging-email" && toset(module.ses[0].posture.reputation_alarms) == toset(["bounce", "complaint"])
    error_message = "The SES identity uses the configuration set with TLS required."
  }
}

run "email_ses_needs_a_sender" {
  command = plan

  variables {
    email_provider = "ses"
    email_domain   = "mail.staging.example.test"
  }

  expect_failures = [var.email_from]
}

run "email_sender_in_the_domain" {
  command = plan

  variables {
    email_domain = "mail.staging.example.test"
    email_from   = "no-reply@elsewhere.example.test"
  }

  expect_failures = [var.email_from]
}

run "email_fake_refused" {
  command = plan

  variables {
    email_provider = "fake"
  }

  expect_failures = [var.email_provider]
}

# ADR-0025 follow-up: CI publishes linux/amd64 images only, so every task and the pdf capacity run
# x86_64 by default; a Graviton pdf capacity is refused unless the whole platform is ARM64.
run "one_architecture_for_every_task" {
  command = plan

  assert {
    condition = alltrue([
      for m in [module.web, module.api, module.worker, module.worker_pdf, module.beat, module.migrate, module.db_bootstrap] :
      m.cpu_architecture == "X86_64"
    ])
    error_message = "Every task definition is X86_64, matching the amd64 images CI builds."
  }

  assert {
    condition     = output.pdf_capacity.posture.ami_parameter == "/aws/service/ecs/optimized-ami/amazon-linux-2023/recommended/image_id"
    error_message = "The pdf capacity uses the x86_64 ECS-optimized AMI (t3.medium)."
  }
}

run "graviton_pdf_capacity_needs_arm64_everywhere" {
  command = plan

  variables {
    pdf_worker = { instance_type = "t4g.medium" }
  }

  expect_failures = [var.pdf_worker]
}

# Claude safety lock (docs/10 §11): every app container gets SOS_ANTHROPIC_ZDR_CONFIRMED, false
# unless the operator confirms the Anthropic ZDR agreement and DPA.
run "anthropic_zdr_confirmation_defaults_to_false" {
  command = plan

  assert {
    condition = alltrue([
      for c in [module.api.container_definition, module.worker.container_definition, module.worker_pdf.container_definition, module.beat.container_definition, module.migrate.container_definition] :
      [for e in c.environment : e.value if e.name == "SOS_ANTHROPIC_ZDR_CONFIRMED"] == ["false"]
    ])
    error_message = "Every app container gets SOS_ANTHROPIC_ZDR_CONFIRMED=false by default."
  }
}

run "anthropic_zdr_confirmation_when_set" {
  command = plan

  variables {
    anthropic_zdr_confirmed = true
  }

  assert {
    condition     = [for e in module.api.container_definition.environment : e.value if e.name == "SOS_ANTHROPIC_ZDR_CONFIRMED"] == ["true"]
    error_message = "anthropic_zdr_confirmed = true reaches the api."
  }
}

# Public marketing site (docs/17 §5.6): web task only, empty (hidden) by default, not secrets.
run "public_site_settings_reach_only_the_web_task" {
  command = plan

  variables {
    public_contact_email   = "hello@example.test"
    public_company_name    = "Synthetic Company Private Limited"
    public_company_address = "1 Synthetic Road|Vijayawada 520001"
    public_whatsapp_number = "+919000000000"
  }

  assert {
    condition = (
      [for e in module.web.container_definition.environment : e.value if e.name == "SOS_PUBLIC_CONTACT_EMAIL"] == ["hello@example.test"]
      && [for e in module.web.container_definition.environment : e.value if e.name == "SOS_PUBLIC_WHATSAPP_NUMBER"] == ["+919000000000"]
      && [for e in module.web.container_definition.environment : e.value if e.name == "SOS_PUBLIC_COMPANY_ADDRESS"] == ["1 Synthetic Road|Vijayawada 520001"]
    )
    error_message = "The web task gets the public site settings."
  }

  assert {
    condition = alltrue([
      for c in [module.api.container_definition, module.worker.container_definition, module.beat.container_definition, module.migrate.container_definition] :
      length([for e in c.environment : e.name if startswith(e.name, "SOS_PUBLIC_")]) == 0
    ])
    error_message = "Only the web task gets SOS_PUBLIC_* settings."
  }
}

run "public_site_settings_are_empty_by_default" {
  command = plan

  assert {
    condition     = [for e in module.web.container_definition.environment : e.value if e.name == "SOS_PUBLIC_WHATSAPP_NUMBER"] == [""]
    error_message = "Unset public site settings are empty (hidden)."
  }
}

run "whatsapp_number_with_spaces_refused" {
  command = plan

  variables {
    public_whatsapp_number = "+91 90000 00000"
  }

  expect_failures = [var.public_whatsapp_number]
}

run "whatsapp_number_with_leading_zero_refused" {
  command = plan

  variables {
    public_whatsapp_number = "09000000000"
  }

  expect_failures = [var.public_whatsapp_number]
}

run "contact_email_with_query_refused" {
  command = plan

  variables {
    public_contact_email = "hello@example.test?subject=hi"
  }

  expect_failures = [var.public_contact_email]
}

# Audit W3-06: the internet-facing api cannot tag (sos-lifecycle=discarded / export-7d) or delete any
# school file; only the worker discards. worker-pdf may only tag its own exports export-7d.
run "api_role_cannot_tag_or_delete_school_files" {
  command = plan

  assert {
    condition = alltrue([
      for s in data.aws_iam_policy_document.api.statement : length(setintersection(toset(s.actions), toset([
        "s3:DeleteObject", "s3:DeleteObjectVersion", "s3:PutObjectTagging", "s3:PutObjectVersionTagging",
        "s3:DeleteObjectTagging", "s3:*", "s3:Delete*", "s3:Put*", "*",
      ]))) == 0 || alltrue([for r in s.resources : !strcontains(r, "/t/")])
    ])
    error_message = "The api task role must not tag or delete under files/t/* (W3-06)."
  }

  assert {
    condition = anytrue([
      for s in data.aws_iam_policy_document.worker.statement :
      s.sid == "FilesDiscard" && toset(s.actions) == toset(["s3:DeleteObject", "s3:PutObjectTagging", "s3:PutObjectVersionTagging"]) && toset(s.resources) == toset(["arn:aws:s3:::sos-staging-files-444455556666/t/*"])
    ])
    error_message = "The worker task role discards (tag + delete) under files/t/*."
  }

  assert {
    condition = alltrue([
      for s in data.aws_iam_policy_document.worker_pdf.statement :
      !contains(s.actions, "s3:DeleteObject") && !contains(s.actions, "s3:PutObjectVersionTagging")
    ])
    error_message = "worker-pdf never deletes school files nor tags older versions."
  }

  assert {
    condition = anytrue([
      for s in data.aws_iam_policy_document.worker_pdf.statement :
      s.sid == "ExportLifecycleTag" && toset(s.actions) == toset(["s3:PutObjectTagging"])
      && toset(s.resources) == toset(["arn:aws:s3:::sos-staging-files-444455556666/t/*/exports/*"])
      && anytrue([for c in s.condition : c.test == "StringEquals" && c.variable == "s3:RequestObjectTag/sos-lifecycle" && toset(c.values) == toset(["export-7d"])])
    ])
    error_message = "worker-pdf may set only sos-lifecycle=export-7d, only on t/*/exports/*."
  }
}
