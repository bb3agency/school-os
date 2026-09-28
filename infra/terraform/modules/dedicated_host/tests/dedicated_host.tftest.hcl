# Dedicated host hardening (07 §13): IMDSv2, no SSH/key pair, 80/443 only, encrypted volumes, no secrets in user data.

mock_provider "aws" {
  mock_data "aws_iam_policy_document" {
    defaults = { json = "{\"Version\":\"2012-10-17\",\"Statement\":[]}" }
  }
  mock_data "aws_caller_identity" {
    defaults = { account_id = "111122223333" }
  }
  mock_data "aws_partition" {
    defaults = { partition = "aws" }
  }
  mock_data "aws_region" {
    defaults = { region = "ap-south-1" }
  }
  mock_data "aws_subnet" {
    defaults = { availability_zone = "ap-south-1a" }
  }
  mock_data "aws_ssm_parameter" {
    defaults = { value = "ami-0123456789abcdef0" }
  }
  mock_resource "aws_iam_role" {
    defaults = { arn = "arn:aws:iam::111122223333:role/mock" }
  }
  mock_resource "aws_s3_bucket" {
    defaults = { arn = "arn:aws:s3:::mock-bucket" }
  }
  mock_resource "aws_secretsmanager_secret" {
    defaults = { arn = "arn:aws:secretsmanager:ap-south-1:111122223333:secret:mock-AbCdEf" }
  }
  mock_resource "aws_ebs_volume" {
    defaults = { id = "vol-0123456789abcdef0" }
  }
}

variables {
  school_code            = "demo-school"
  deployment_id          = "01923f4e-5b6c-7d8e-9f00-112233445566"
  tenant_id              = "01923f4e-5b6c-7d8e-9f00-aabbccddeeff"
  vpc_id                 = "vpc-0123456789abcdef0"
  subnet_id              = "subnet-00000000000000001"
  kms_key_arn            = "arn:aws:kms:ap-south-1:111122223333:key/00000000-0000-0000-0000-000000000001"
  public_host            = "demo-school.example.test"
  acme_email             = "ops@example.test"
  release_version        = "2026.10.1"
  ecr_registry           = "111122223333.dkr.ecr.ap-south-1.amazonaws.com"
  ecr_repository_arns    = ["arn:aws:ecr:ap-south-1:111122223333:repository/schoolos/api"]
  bundle_s3_prefix       = "s3://sos-prod-artifacts-111122223333/dedicated"
  artifacts_bucket_arn   = "arn:aws:s3:::sos-prod-artifacts-111122223333"
  artifacts_kms_key_arn  = "arn:aws:kms:ap-south-1:111122223333:key/00000000-0000-0000-0000-000000000002"
  backup_bucket_name     = "sos-ded-demo-school-backup-111122223333"
  backup_bucket_arn      = "arn:aws:s3:::sos-ded-demo-school-backup-111122223333"
  backup_kms_key_arn     = "arn:aws:kms:ap-south-2:111122223333:key/00000000-0000-0000-0000-000000000003"
  oidc_issuer            = "https://cognito-idp.ap-south-1.amazonaws.com/ap-south-1_example"
  oidc_client_id         = "exampleclientid"
  oidc_client_secret_arn = "arn:aws:secretsmanager:ap-south-1:111122223333:secret:sos/dedicated/demo-school/oidc-AbCdEf"
  control_plane_url      = "https://app.example.test"
}

run "imdsv2_required" {
  command = plan

  assert {
    condition     = one(aws_instance.host.metadata_options).http_tokens == "required" && one(aws_instance.host.metadata_options).http_endpoint == "enabled"
    error_message = "IMDSv2 must be required."
  }

  assert {
    condition     = aws_instance.host.instance_type == "t4g.medium" && strcontains(data.aws_ssm_parameter.ubuntu.name, "/arm64/")
    error_message = "Graviton default with the arm64 Ubuntu image."
  }
}

run "fleet_tags_and_logs" {
  command = plan

  assert {
    condition     = aws_instance.host.tags["schoolos:tier"] == "dedicated" && aws_instance.host.tags["schoolos:deployment-id"] == var.deployment_id
    error_message = "Hosts carry schoolos:tier=dedicated and schoolos:deployment-id for SSM fleet targeting."
  }

  assert {
    condition     = aws_cloudwatch_log_group.host.name == "/schoolos/dedicated/demo-school" && aws_cloudwatch_log_group.host.retention_in_days == 400 && aws_cloudwatch_log_group.host.kms_key_id == var.kms_key_arn
    error_message = "Host logs go to a KMS-encrypted CloudWatch log group kept 400 days."
  }
}

run "audit_archive_compliance" {
  command = plan

  assert {
    condition     = module.audit_archive.object_lock_mode == "COMPLIANCE" && module.audit_archive.object_lock_years == 3 && module.audit_archive.object_lock_enabled
    error_message = "Per-school audit archive uses Object Lock COMPLIANCE for 3 years."
  }
}

run "only_80_443_inbound" {
  command = plan

  assert {
    condition = (toset([aws_vpc_security_group_ingress_rule.https.from_port, aws_vpc_security_group_ingress_rule.http3.from_port, aws_vpc_security_group_ingress_rule.http.from_port]) == toset([443, 80])
    && alltrue([for r in [aws_vpc_security_group_ingress_rule.https, aws_vpc_security_group_ingress_rule.http3, aws_vpc_security_group_ingress_rule.http] : r.from_port == r.to_port]))
    error_message = "Only 80 and 443 (TCP, plus UDP 443 for HTTP/3) are open inbound."
  }
}

run "volumes_encrypted_with_school_key" {
  command = plan

  assert {
    condition     = one(aws_instance.host.root_block_device).encrypted && one(aws_instance.host.root_block_device).kms_key_id == var.kms_key_arn
    error_message = "Root volume encrypted with the school CMK."
  }

  assert {
    condition     = aws_ebs_volume.data.encrypted && aws_ebs_volume.data.kms_key_id == var.kms_key_arn && aws_ebs_volume.data.type == "gp3"
    error_message = "Separate encrypted gp3 data volume for PostgreSQL."
  }
}

run "user_data_disables_ssh_and_has_no_secrets" {
  command = apply

  assert {
    condition     = strcontains(aws_instance.host.user_data, "[systemctl, mask, ssh.service]")
    error_message = "cloud-init disables and masks sshd (no SSH; SSM only)."
  }

  assert {
    condition     = strcontains(aws_instance.host.user_data, "SOS_DATA_VOLUME_ID=vol-0123456789abcdef0") && strcontains(aws_instance.host.user_data, "SOS_DEPLOYMENT_MODE=dedicated")
    error_message = "Host config carries the data volume and dedicated mode."
  }

  assert {
    condition     = !can(regex("(?i)(password|secret_key|api_key)\\s*=", aws_instance.host.user_data))
    error_message = "User data must not contain credentials (they come from Secrets Manager at runtime)."
  }

  assert {
    condition     = aws_secretsmanager_secret_version.generated.secret_string == null
    error_message = "Generated credentials are written write-only."
  }
}

# The host env uses exactly the names apps/api/app/core/config.py reads (SEC-009); the compose side
# is checked by apps/api/tests/deploy/test_env_contract.py.
run "host_env_uses_settings_names" {
  command = apply

  assert {
    condition = alltrue([
      for line in [
        "SOS_KMS_DATA_KEY_ARN=${var.kms_key_arn}",
        "SOS_AUDIT_SIGNING_KEY_ARN=${output.audit_signing_key_arn}",
        "SOS_CONTROL_PLANE_URL=https://app.example.test",
        "SOS_DEPLOYMENT_ID=${var.deployment_id}",
        "SOS_DEDICATED_TENANT_ID=${var.tenant_id}",
        "SOS_KEY_WRAPPER=kms",
      ] : strcontains(aws_instance.host.user_data, line)
    ])
    error_message = "host.env carries the KMS keys, control-plane URL and heartbeat identity under their Settings names."
  }

  assert {
    condition     = !can(regex("SOS_(KMS_KEY_ARN|FLEET_URL|FLEET_HMAC_KEY)=", aws_instance.host.user_data))
    error_message = "Old names that config.py never read are gone."
  }
}

run "audit_signing_key_for_the_host" {
  command = plan

  assert {
    condition     = output.posture.audit_signing_key.key_spec == "ECC_NIST_P256" && output.posture.audit_signing_key.key_usage == "SIGN_VERIFY" && !output.posture.audit_signing_key.rotation
    error_message = "Each host signs its audit archives with its own ECC_NIST_P256 SIGN_VERIFY key (FR-AUD-004)."
  }

  assert {
    condition = anytrue([
      for s in data.aws_iam_policy_document.host.statement :
      s.sid == "AuditSigning" && toset(s.actions) == toset(["kms:Sign", "kms:GetPublicKey"]) && length(s.resources) == 1
    ])
    error_message = "The instance role may kms:Sign and kms:GetPublicKey with the signing key only."
  }
}

# SEC-016, SEC-010 (docs/07 §10, §11): the school's browsers POST uploads straight to the files bucket.
run "files_bucket_cors_allows_the_public_host" {
  command = plan

  assert {
    condition = (
      length(output.files_cors_rules) == 1
      && output.files_cors_rules[0].allowed_origins == toset(["https://demo-school.example.test"])
      && output.files_cors_rules[0].allowed_methods == toset(["POST"])
      && output.files_cors_rules[0].allowed_headers == toset(["content-type"])
      && length(output.files_cors_rules[0].expose_headers) == 0
    )
    error_message = "Files bucket CORS: POST from https://<public_host> only."
  }

  assert {
    condition     = length(module.audit_archive.cors_rules) == 0
    error_message = "The audit archive has no CORS configuration."
  }

  assert {
    condition     = output.files_browser_origin == "https://sos-ded-demo-school-files-111122223333.s3.ap-south-1.amazonaws.com"
    error_message = "The files origin is the regional virtual-hosted bucket origin (compose derives FILES_ORIGIN the same way)."
  }
}

run "files_bucket_cors_adds_the_custom_domain" {
  command = plan

  variables {
    custom_domain = "office.demo-school.example.test"
  }

  assert {
    condition     = output.files_cors_rules[0].allowed_origins == toset(["https://demo-school.example.test", "https://office.demo-school.example.test"])
    error_message = "With a custom domain, both app origins may upload."
  }
}

run "heartbeat_key_is_operator_supplied" {
  command = plan

  variables {
    operator_secret_keys = ["SOS_ANTHROPIC_API_KEY"]
  }

  expect_failures = [var.operator_secret_keys]
}

run "tenant_id_must_be_a_uuid" {
  command = plan

  variables {
    tenant_id = "demo-school"
  }

  expect_failures = [var.tenant_id]
}

run "x86_instances_use_amd64_image" {
  command = plan

  variables {
    instance_type = "t3.medium"
  }

  assert {
    condition     = strcontains(data.aws_ssm_parameter.ubuntu.name, "/amd64/")
    error_message = "Non-Graviton types use the amd64 image."
  }
}

run "backups_only_in_hyderabad" {
  command = plan

  variables {
    backup_region = "ap-southeast-1"
  }

  expect_failures = [var.backup_region]
}

# ADR-0023 option C (US-103, SEC-021): support sign-in is off unless the host gets its own support
# client; then host.env carries it under the Settings/BFF names and the secret stays in Secrets Manager.
run "support_sign_in_off_by_default" {
  command = apply

  assert {
    condition = (
      strcontains(aws_instance.host.user_data, "SOS_SUPPORT_OIDC_AUDIENCE=\n")
      && strcontains(aws_instance.host.user_data, "SUPPORT_OIDC_CLIENT_ID=\n")
      && !strcontains(aws_instance.host.user_data, "SUPPORT_OIDC_CLIENT_SECRET=")
    )
    error_message = "Without a support client, host.env leaves it empty (fail closed) and fetches no support secret."
  }
}

run "support_sign_in_configured" {
  command = apply
  # Own state: the host ignores user_data changes after creation (lifecycle), so it must be a new host.
  state_key = "support"

  variables {
    support_oidc_issuer            = "https://cognito-idp.ap-south-1.amazonaws.com/ap-south-1_OperatorPool"
    support_oidc_client_id         = "supportclientid"
    support_oidc_client_secret_arn = "arn:aws:secretsmanager:ap-south-1:111122223333:secret:sos/dedicated/demo-school/oidc/support-client-secret-AbCdEf"
  }

  assert {
    condition = alltrue([
      for line in [
        "SOS_SUPPORT_OIDC_ISSUER=https://cognito-idp.ap-south-1.amazonaws.com/ap-south-1_OperatorPool",
        "SOS_SUPPORT_OIDC_AUDIENCE=supportclientid",
        "SUPPORT_OIDC_CLIENT_ID=supportclientid",
        " SUPPORT_OIDC_CLIENT_SECRET=arn:aws:secretsmanager:ap-south-1:111122223333:secret:sos/dedicated/demo-school/oidc/support-client-secret-AbCdEf\"",
      ] : strcontains(aws_instance.host.user_data, line)
    ])
    error_message = "host.env carries the support issuer and client ID; the secret is fetched from Secrets Manager by ARN."
  }
}

run "support_settings_all_or_none" {
  command = plan

  variables {
    support_oidc_client_id = "supportclientid"
  }

  expect_failures = [var.support_oidc_client_id]
}

run "support_issuer_is_never_the_staff_pool" {
  command = plan

  variables {
    support_oidc_issuer            = "https://cognito-idp.ap-south-1.amazonaws.com/ap-south-1_example"
    support_oidc_client_id         = "supportclientid"
    support_oidc_client_secret_arn = "arn:aws:secretsmanager:ap-south-1:111122223333:secret:x-AbCdEf"
  }

  expect_failures = [var.support_oidc_issuer]
}
