# Prod invariants (SEC-009, SEC-011, SEC-022, SEC-030, NFR-AVL-002, NFR-PRV-001), planned against mocks.

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
}

mock_provider "aws" {
  alias = "dr"
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
    defaults = { region = "ap-south-2" }
  }
}

variables {
  aws_account_id  = "111122223333"
  owner           = "platform@example.test"
  cost_center     = "schoolos-prod"
  release_version = "2026.10.1"
  # Synthetic supplier (test only).
  billing_supplier_legal_name = "Synthetic Test Supplier Private Limited"
  billing_supplier_address    = "Synthetic Test Supplier; Vijayawada 520001, Andhra Pradesh"
  billing_supplier_gstin      = "37ABCDE1234F1Z5"
  app_domain                  = "app.example.test"
  admin_domain                = "admin.example.test"
  cognito_domain_prefix       = "sos-test-prod"
  alarm_emails                = ["oncall@example.test"]
  state_bucket_arn            = "arn:aws:s3:::sos-tfstate-111122223333"
  state_kms_key_arn           = "arn:aws:kms:ap-south-1:111122223333:key/00000000-0000-0000-0000-000000000009"
}

run "rds_encrypted_and_deletion_protected" {
  command = plan

  assert {
    condition     = module.platform.security_posture.rds.storage_encrypted
    error_message = "RDS storage must be encrypted (SEC-011)."
  }

  assert {
    condition     = module.platform.security_posture.rds.deletion_protection
    error_message = "Prod RDS must have deletion protection."
  }

  assert {
    condition     = !module.platform.security_posture.rds.publicly_accessible
    error_message = "RDS must never be publicly accessible."
  }

  assert {
    condition     = module.platform.security_posture.rds.force_ssl == "1"
    error_message = "RDS must require TLS (rds.force_ssl=1)."
  }

  assert {
    condition     = module.platform.security_posture.rds.backup_retention_days >= 14
    error_message = "PITR must cover at least 14 days (NFR-AVL-002)."
  }

  assert {
    condition     = module.platform.security_posture.rds.managed_master_secret
    error_message = "RDS master password must be managed by Secrets Manager (SEC-009)."
  }
}

run "dr_backups_replicated_to_hyderabad" {
  command = plan

  assert {
    condition     = module.rds_dr.retention_days >= 30
    error_message = "Replicated backups in ap-south-2 must be kept >= 30 days (docs/10 §9)."
  }
}

run "cache_encrypted" {
  command = plan

  assert {
    condition = (module.platform.security_posture.redis.at_rest_encryption
      && module.platform.security_posture.redis.transit_encryption
    && module.platform.security_posture.redis.transit_mode == "required")
    error_message = "Valkey must encrypt at rest and require TLS in transit (SEC-011)."
  }

  assert {
    condition     = module.platform.security_posture.redis.engine == "valkey"
    error_message = "Cache engine must be Valkey."
  }
}

run "audit_archive_compliance_lock" {
  command = plan

  assert {
    condition     = module.platform.security_posture.audit_object_lock == "COMPLIANCE" && module.platform.security_posture.audit_object_lock_yrs == 3
    error_message = "Prod audit archive must be Object Lock COMPLIANCE for 3 years."
  }

  assert {
    condition     = module.platform.security_posture.audit_signing_key.key_spec == "ECC_NIST_P256" && module.platform.security_posture.audit_signing_key.key_usage == "SIGN_VERIFY"
    error_message = "Daily audit archives are signed with an asymmetric KMS key (FR-AUD-004)."
  }
}

run "containers_hardened" {
  command = plan

  assert {
    condition = alltrue([
      for c in [module.platform.security_posture.web_container, module.platform.security_posture.api_container, module.platform.security_posture.worker_container, module.platform.security_posture.beat_container, module.platform.security_posture.migrate_container] :
      c.readonlyRootFilesystem && !c.privileged && c.user != "root" && c.user != "0" && contains(c.linuxParameters.capabilities.drop, "ALL")
    ])
    error_message = "Every container runs non-root with a read-only root FS and all capabilities dropped (SEC-030)."
  }

  assert {
    condition     = alltrue([for c in [module.platform.security_posture.api_container, module.platform.security_posture.worker_container, module.platform.security_posture.beat_container, module.platform.security_posture.migrate_container] : length([for e in c.environment : e if can(regex("(?i)(password|secret|api_key|token)", e.name))]) == 0])
    error_message = "No secret may be passed as a plain environment variable; use Secrets Manager injection (SEC-009)."
  }
}

run "identity_controls" {
  command = plan

  assert {
    condition     = module.platform.security_posture.cognito["platform"].mfa == "ON"
    error_message = "Platform operators must always use MFA."
  }

  assert {
    condition     = module.platform.security_posture.cognito["tenant"].min_password >= 12
    error_message = "Password minimum length is 12."
  }

  assert {
    condition     = module.platform.security_posture.cognito["tenant"].refresh_rotation == "ENABLED" && module.platform.security_posture.cognito["tenant"].access_token_min <= 10
    error_message = "Refresh token rotation on; access tokens <= 10 minutes."
  }
}

run "ecr_immutable_and_scanned" {
  command = plan

  assert {
    condition     = alltrue([for r in values(module.platform.security_posture.ecr) : r.immutable && r.scan_on_push && r.encryption == "KMS"])
    error_message = "ECR repositories must be immutable, scanned on push and KMS-encrypted."
  }
}

run "region_guard_rejects_other_regions" {
  command = plan

  variables {
    aws_region = "us-east-1"
  }

  expect_failures = [var.aws_region]
}

run "region_guard_rejects_other_dr_region" {
  command = plan

  variables {
    dr_region = "ap-southeast-1"
  }

  expect_failures = [var.dr_region]
}

run "security_baseline_sec_023" {
  command = plan

  assert {
    condition = (module.security.posture.trail.multi_region && module.security.posture.trail.log_file_validation
    && module.security.posture.trail.management_events)
    error_message = "Prod has a multi-region, validated CloudTrail with management events (SEC-023)."
  }

  assert {
    condition     = module.security.posture.trail_bucket.object_lock_mode == "COMPLIANCE" && module.security.posture.trail_bucket.object_lock_days >= 400
    error_message = "Prod CloudTrail logs are Object Lock COMPLIANCE for >= 400 days (CERT-In 180 d in India, DPDP 1 y)."
  }

  assert {
    condition     = length(module.security.posture.delete_exempt_principals) == 0
    error_message = "Nobody is exempt from the prod log buckets' deny-delete policy."
  }

  assert {
    condition = length(setsubtract([
      "arn:aws:s3:::${module.platform.buckets.files}/",
      "arn:aws:s3:::${module.platform.buckets.audit}/",
      "arn:aws:s3:::sos-ded-",
    ], module.security.posture.trail.s3_data_event_arn_prefixes)) == 0
    error_message = "S3 data events cover the files and audit buckets and every dedicated host's buckets."
  }

  assert {
    condition = alltrue([
      for p in [module.security.posture.detection_primary, module.security.posture.detection_dr] :
      p.guardduty_enabled && p.guardduty_features["S3_DATA_EVENTS"] == "ENABLED" && p.config_recording_enabled && length(p.securityhub_standards) >= 2
    ])
    error_message = "GuardDuty (S3 Protection), Config and Security Hub (FSBP + CIS) run in ap-south-1 and ap-south-2."
  }

  assert {
    condition     = module.security.posture.detection_primary.guardduty_features["EBS_MALWARE_PROTECTION"] == "ENABLED"
    error_message = "Malware Protection covers dedicated-host EBS volumes."
  }

  assert {
    condition     = toset(module.security.posture.alert_subscriptions) == toset(var.alarm_emails)
    error_message = "Security alerts reach the on-call recipients (alarm_emails unless security_alert_emails is set)."
  }

  assert {
    condition     = module.security.posture.account_public_access_block
    error_message = "Account-level S3 Block Public Access is on in prod."
  }
}

# Audit W3-06 (b): every school file has an independent, locked copy in Hyderabad.
run "files_locked_copy_in_hyderabad" {
  command = plan

  assert {
    condition = (
      module.files_replica.posture.object_lock_mode == "GOVERNANCE"
      && module.files_replica.posture.object_lock_days >= 90
      && module.files_replica.posture.replica_region == "ap-south-2"
      && module.files_replica.posture.versioning == "Enabled"
      && module.files_replica.posture.replicated_prefix == "t/"
      && module.files_replica.posture.delete_markers == "Enabled"
    )
    error_message = "The files bucket is replicated to a locked (GOVERNANCE >= 90 days), versioned bucket in ap-south-2 (SSE-KMS: modules/s3_replica tests)."
  }
}
