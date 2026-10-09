# Staging keeps every encryption/isolation control of prod; only teardown-related protections differ.

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

mock_provider "aws" {
  alias = "dr"
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
    defaults = { region = "ap-south-2" }
  }
}

variables {
  aws_account_id  = "444455556666"
  owner           = "platform@example.test"
  cost_center     = "schoolos-staging"
  release_version = "2026.10.1"
  # Same synthetic values as terraform.tfvars.example (staging only, not valid for tax invoices).
  billing_supplier_legal_name = "SchoolOS Staging Synthetic Supplier (not a tax invoice)"
  billing_supplier_address    = "Synthetic Test Supplier; Vijayawada 520001, Andhra Pradesh"
  billing_supplier_gstin      = "37STAGE0000S1Z5"
  app_domain                  = "app.staging.example.test"
  admin_domain                = "admin.staging.example.test"
  cognito_domain_prefix       = "sos-test-staging"
  alarm_emails                = ["dev@example.test"]
  state_bucket_arn            = "arn:aws:s3:::sos-tfstate-444455556666"
  state_kms_key_arn           = "arn:aws:kms:ap-south-1:444455556666:key/00000000-0000-0000-0000-000000000009"
}

run "staging_keeps_encryption_and_tls" {
  command = plan

  assert {
    condition     = module.platform.security_posture.rds.storage_encrypted && module.platform.security_posture.rds.force_ssl == "1"
    error_message = "Staging RDS must still be encrypted and TLS-only."
  }

  assert {
    condition     = module.platform.security_posture.redis.at_rest_encryption && module.platform.security_posture.redis.transit_encryption
    error_message = "Staging Valkey must still be encrypted."
  }

  assert {
    condition     = module.platform.security_posture.audit_object_lock == "COMPLIANCE"
    error_message = "Staging audit archive still uses COMPLIANCE mode (short retention)."
  }
}

run "staging_is_synthetic_and_disposable" {
  command = plan

  assert {
    condition     = var.data_class == "synthetic"
    error_message = "Staging must be tagged data_class=synthetic (invariant 11)."
  }

  assert {
    condition     = !module.platform.security_posture.rds.deletion_protection
    error_message = "Staging RDS is disposable (no deletion protection)."
  }
}

run "staging_containers_hardened" {
  command = plan

  assert {
    condition = alltrue([
      for c in [module.platform.security_posture.web_container, module.platform.security_posture.api_container, module.platform.security_posture.worker_container] :
      c.readonlyRootFilesystem && !c.privileged && contains(c.linuxParameters.capabilities.drop, "ALL")
    ])
    error_message = "Containers are hardened in every environment (SEC-030)."
  }
}

run "security_baseline_on_in_staging" {
  command = plan

  assert {
    condition     = length(module.security) == 1
    error_message = "The SEC-023 baseline is created in the staging account by default."
  }

  assert {
    condition     = module.security[0].posture.trail_bucket.object_lock_mode == "GOVERNANCE" && module.security[0].posture.trail_bucket.object_lock_days >= 180
    error_message = "Staging CloudTrail logs are GOVERNANCE-locked for at least the CERT-In 180 days."
  }

  assert {
    condition = alltrue([
      for p in [module.security[0].posture.detection_primary, module.security[0].posture.detection_dr] :
      p.guardduty_enabled && p.config_recording_enabled && length(p.securityhub_standards) >= 2
    ])
    error_message = "Staging exercises the same detection as prod."
  }
}

run "security_baseline_can_be_disabled" {
  command = plan

  variables {
    enable_security_baseline = false
  }

  assert {
    condition     = length(module.security) == 0 && output.security == null
    error_message = "enable_security_baseline = false creates nothing."
  }
}

# Audit W3-06 (b): staging exercises the locked files copy with the DR path.
run "files_locked_copy_with_the_dr_path" {
  command = plan

  assert {
    condition     = length(module.files_replica) == 1 && module.files_replica[0].posture.object_lock_mode == "GOVERNANCE" && module.files_replica[0].posture.object_lock_days >= 90
    error_message = "Staging replicates the files bucket to a locked bucket in ap-south-2."
  }
}

# Audit W3-04: staging's pull-request plan role reads no secrets.
run "pr_plan_role_reads_no_secrets" {
  command = plan

  assert {
    condition     = !module.platform.github_actions.plan_reads_secrets && contains(module.platform.github_actions.plan_subjects, "repo:bb3agency/school-os:pull_request")
    error_message = "Staging's plan role trusts every same-repo pull request, so it must not read secrets."
  }
}

# Audit 2026-10-05 P2-08 (b): only deploy-staging.yml on main (which needs green CI) deploys staging.
run "staging_deploy_only_from_the_staging_workflow_on_main" {
  command = plan

  assert {
    condition     = module.platform.github_actions.deploy_workflow_refs == ["bb3agency/school-os/.github/workflows/deploy-staging.yml@refs/heads/main"]
    error_message = "The staging deploy role pins job_workflow_ref to deploy-staging.yml on main."
  }
}
