# SEC-023 (pilot-ready gate): CloudTrail to an Object Lock bucket, GuardDuty, Config and Security Hub
# in ap-south-1 and ap-south-2, alerts to the on-call topic. Planned against mocks (never applied).

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

# The CMK ARN is known at plan in tests so encryption wiring can be asserted.
override_resource {
  target          = aws_kms_key.security
  override_during = plan
  values = {
    arn    = "arn:aws:kms:ap-south-1:111122223333:key/00000000-0000-0000-0000-00000000000a"
    key_id = "00000000-0000-0000-0000-00000000000a"
  }
}

variables {
  env              = "prod"
  name_prefix      = "sos-test"
  object_lock_mode = "COMPLIANCE"
  data_event_bucket_arns = [
    "arn:aws:s3:::sos-test-files-111122223333",
    "arn:aws:s3:::sos-test-audit-archive-111122223333",
  ]
  data_event_bucket_name_prefixes = ["sos-ded-"]
  access_log_bucket               = "sos-test-logs-111122223333"
  alert_emails                    = ["oncall@example.test"]
}

run "cloudtrail_all_regions_validated_encrypted" {
  command = plan

  assert {
    condition     = output.posture.trail.multi_region && output.posture.trail.global_service_events && output.posture.trail.logging
    error_message = "One trail records every region, including global service events."
  }

  assert {
    condition     = output.posture.trail.log_file_validation
    error_message = "Log file validation (digest files) must be on."
  }

  assert {
    condition     = output.posture.trail.bucket == "sos-test-cloudtrail-111122223333"
    error_message = "The trail delivers to the dedicated log bucket."
  }

  assert {
    condition     = output.posture.trail.management_events && !output.posture.trail.management_read_only_filter
    error_message = "Management events are recorded, read and write."
  }

  assert {
    condition = output.posture.trail.s3_data_event_arn_prefixes == sort([
      "arn:aws:s3:::sos-test-files-111122223333/",
      "arn:aws:s3:::sos-test-audit-archive-111122223333/",
      "arn:aws:s3:::sos-ded-",
    ])
    error_message = "S3 data events cover the school-data buckets and the dedicated-host prefix."
  }

  assert {
    condition     = output.posture.kms_rotation
    error_message = "The security-logs CMK rotates annually."
  }
}

run "log_bucket_locked_private_undeletable" {
  command = plan

  assert {
    condition     = output.posture.trail_bucket.object_lock_enabled && output.posture.trail_bucket.object_lock_mode == "COMPLIANCE"
    error_message = "The CloudTrail bucket uses Object Lock (COMPLIANCE here)."
  }

  assert {
    condition     = output.posture.trail_bucket.object_lock_days >= 180 && output.posture.log_retention_days == 400
    error_message = "Retention covers CERT-In (180 days in India) and defaults to 400 days (docs/10 §5)."
  }

  assert {
    condition     = output.posture.trail_bucket.sse_algorithm == "aws:kms" && output.posture.evidence_bucket.sse_algorithm == "aws:kms"
    error_message = "Log buckets are SSE-KMS encrypted."
  }

  assert {
    condition = alltrue(concat(
      values(output.posture.trail_bucket.public_access_block),
      values(output.posture.evidence_bucket.public_access_block),
    ))
    error_message = "Log buckets block all public access."
  }

  assert {
    condition     = output.posture.trail_bucket.versioning == "Enabled" && output.posture.evidence_bucket.versioning == "Enabled"
    error_message = "Log buckets are versioned."
  }

  assert {
    condition     = output.posture.trail_bucket.access_log_bucket == var.access_log_bucket
    error_message = "Server access logging goes to the environment's logs bucket."
  }

  assert {
    condition = length(setsubtract(
      ["s3:DeleteObject", "s3:DeleteObjectVersion", "s3:BypassGovernanceRetention", "s3:DeleteBucket"],
      output.posture.log_bucket_deny_actions,
    )) == 0 && length(output.posture.delete_exempt_principals) == 0
    error_message = "The bucket policy denies deletes and governance bypass to everyone."
  }

  assert {
    condition = (output.posture.trail_bucket.kms_key_arn == "arn:aws:kms:ap-south-1:111122223333:key/00000000-0000-0000-0000-00000000000a"
      && output.posture.evidence_bucket.kms_key_arn == output.posture.trail_bucket.kms_key_arn
      && output.posture.trail.kms_key_arn == output.posture.trail_bucket.kms_key_arn
    && output.posture.alert_topic_kms_key == output.posture.trail_bucket.kms_key_arn)
    error_message = "Trail, log buckets and alert topic use the security-logs CMK."
  }
}

run "detection_in_both_regions" {
  command = plan

  assert {
    condition = alltrue([
      for p in [output.posture.detection_primary, output.posture.detection_dr] :
      p.guardduty_enabled && p.guardduty_features["S3_DATA_EVENTS"] == "ENABLED" && p.config_recording_all && p.config_recording_enabled && p.guardduty_export
    ])
    error_message = "GuardDuty (S3 Protection, findings export) and Config run in ap-south-1 and ap-south-2."
  }

  assert {
    condition     = output.posture.detection_primary.guardduty_features["EBS_MALWARE_PROTECTION"] == "ENABLED"
    error_message = "Malware Protection (EBS) is on in ap-south-1, where dedicated hosts run."
  }

  assert {
    condition     = output.posture.detection_primary.config_global_types && !output.posture.detection_dr.config_global_types
    error_message = "Global resource types are recorded once, in ap-south-1."
  }

  assert {
    condition = alltrue([for id in ["CLOUD_TRAIL_ENABLED", "ROOT_ACCOUNT_MFA_ENABLED", "S3_BUCKET_LEVEL_PUBLIC_ACCESS_PROHIBITED", "S3_BUCKET_SERVER_SIDE_ENCRYPTION_ENABLED", "RDS_STORAGE_ENCRYPTED", "MULTI_REGION_CLOUD_TRAIL_ENABLED"] :
    contains(values(output.posture.detection_primary.config_rules), id)])
    error_message = "Config rules cover CloudTrail, root MFA, public access and encryption."
  }

  assert {
    condition = (output.posture.detection_primary.securityhub_standards == sort([
      "arn:aws:securityhub:ap-south-1::standards/aws-foundational-security-best-practices/v/1.0.0",
      "arn:aws:securityhub:ap-south-1::standards/cis-aws-foundations-benchmark/v/3.0.0",
      ]) && output.posture.detection_dr.securityhub_standards == sort([
      "arn:aws:securityhub:ap-south-2::standards/aws-foundational-security-best-practices/v/1.0.0",
      "arn:aws:securityhub:ap-south-2::standards/cis-aws-foundations-benchmark/v/3.0.0",
    ]))
    error_message = "Security Hub FSBP and CIS in both regions."
  }

  assert {
    condition     = output.posture.securityhub_linked_regions == toset(["ap-south-2"])
    error_message = "Security Hub aggregates ap-south-2 findings into ap-south-1."
  }

  assert {
    condition     = output.posture.detection_dr.forwards_to_primary && output.posture.detection_dr.forward_target_event_bus == "arn:aws:events:ap-south-1:111122223333:event-bus/default"
    error_message = "ap-south-2 forwards findings and tampering events to the ap-south-1 bus."
  }

  assert {
    condition     = !output.posture.detection_primary.forwards_to_primary
    error_message = "ap-south-1 is the alerting region."
  }
}

run "alerts_reach_on_call" {
  command = plan

  assert {
    condition     = output.posture.alert_rules["guardduty-findings"].detail.severity[0].numeric[1] == 7
    error_message = "GuardDuty High (>= 7) findings page (docs/11 §6, P1 security)."
  }

  assert {
    condition     = contains(output.posture.alert_rules["securityhub-findings"].detail.findings.Severity.Label, "CRITICAL")
    error_message = "Critical Security Hub findings alert."
  }

  assert {
    condition = length(setsubtract(
      ["StopLogging", "DeleteTrail", "DeleteDetector", "StopConfigurationRecorder", "DisableSecurityHub", "ScheduleKeyDeletion"],
      output.posture.alert_rules["detection-tampering"].detail.eventName,
    )) == 0
    error_message = "Switching off CloudTrail, GuardDuty, Config or Security Hub alerts."
  }

  assert {
    condition = toset(output.posture.alert_rules["log-bucket-tampering"].detail.requestParameters.bucketName) == toset([
      "sos-test-cloudtrail-111122223333", "sos-test-security-evidence-111122223333",
    ])
    error_message = "Changes to the log buckets alert."
  }

  assert {
    condition     = toset(keys(output.posture.alert_targets)) == toset(keys(output.posture.alert_rules))
    error_message = "Every alert rule targets the security topic."
  }

  assert {
    condition     = toset(output.posture.alert_subscriptions) == toset(["oncall@example.test"])
    error_message = "On-call recipients are subscribed."
  }

  assert {
    condition     = output.posture.account_public_access_block && output.posture.ebs_encryption_by_default
    error_message = "Account-level S3 Block Public Access and EBS default encryption are on."
  }
}

# Audit 2026-10-05 hardening, CIS account-level gaps: IAM Access Analyzer, an account password policy
# (break-glass IAM users), and public sharing of EBS snapshots and AMIs blocked, in both regions.
run "cis_account_guardrails" {
  command = plan

  assert {
    condition     = output.posture.access_analyzers == { "ap-south-1" = "ACCOUNT", "ap-south-2" = "ACCOUNT" }
    error_message = "An account IAM Access Analyzer runs in ap-south-1 and ap-south-2 (CIS 1.20)."
  }

  assert {
    condition = (
      output.posture.password_policy.minimum_password_length >= 14 &&
      output.posture.password_policy.password_reuse_prevention == 24 &&
      output.posture.password_policy.require_lowercase_characters &&
      output.posture.password_policy.require_uppercase_characters &&
      output.posture.password_policy.require_numbers &&
      output.posture.password_policy.require_symbols
    )
    error_message = "The account password policy needs 14+ characters, all classes, and no reuse of the last 24 (CIS 1.8, 1.9)."
  }

  assert {
    condition     = output.posture.ebs_snapshot_public_access == { "ap-south-1" = "block-all-sharing", "ap-south-2" = "block-all-sharing" }
    error_message = "EBS snapshots cannot be shared publicly in either region."
  }

  assert {
    condition     = output.posture.ami_public_access == { "ap-south-1" = "block-new-sharing", "ap-south-2" = "block-new-sharing" }
    error_message = "AMIs cannot be made public in either region."
  }

  assert {
    condition = length(setsubtract(
      ["DeleteAnalyzer", "DisableSnapshotBlockPublicAccess", "DisableImageBlockPublicAccess"],
      output.posture.alert_rules["detection-tampering"].detail.eventName,
    )) == 0 && contains(output.posture.alert_rules["detection-tampering"].detail.eventSource, "access-analyzer.amazonaws.com")
    error_message = "Switching the new guardrails off alerts like any other tampering."
  }
}

# Known and still open from earlier rounds (audit 2026-10-05): root sign-in and IAM events reach
# EventBridge only in us-east-1. A forward-only rule there sends them to the ap-south-1 bus, where the
# CIS-equivalent rules (CloudWatch.1, 3, 4, 6, 8, 10-14 without the billed CloudWatch Logs copy of
# the trail) page the security topic.
run "global_iam_and_sign_in_events_alert" {
  command = plan

  assert {
    condition = (
      output.posture.global_forward.region == "us-east-1" &&
      output.posture.global_forward.target == "arn:aws:events:ap-south-1:111122223333:event-bus/default" &&
      toset(output.posture.global_forward.pattern.detail["$or"][0].eventSource) == toset(["iam.amazonaws.com", "signin.amazonaws.com"]) &&
      output.posture.global_forward.pattern.detail["$or"][1].userIdentity.type == ["Root"]
    )
    error_message = "us-east-1 forwards IAM, sign-in and root events to the ap-south-1 default bus."
  }

  assert {
    condition     = output.posture.alert_rules["root-activity"].detail.userIdentity.type == ["Root"]
    error_message = "Any root activity alerts (CIS CloudWatch.1)."
  }

  assert {
    condition = (
      output.posture.alert_rules["console-sign-in-problems"].detail.eventName == ["ConsoleLogin"] &&
      output.posture.alert_rules["console-sign-in-problems"].detail["$or"][0].responseElements.ConsoleLogin == ["Failure"] &&
      output.posture.alert_rules["console-sign-in-problems"].detail["$or"][1].additionalEventData.MFAUsed == [{ "anything-but" = "Yes" }]
    )
    error_message = "Failed console sign-ins and IAM user sign-ins without MFA alert (CIS CloudWatch.3, 6)."
  }

  assert {
    condition = length(setsubtract(
      ["PutRolePolicy", "AttachRolePolicy", "CreatePolicyVersion", "CreateAccessKey", "UpdateAssumeRolePolicy", "DeleteAccountPasswordPolicy"],
      output.posture.alert_rules["iam-changes"].detail.eventName,
    )) == 0 && output.posture.alert_rules["iam-changes"].detail.eventSource == ["iam.amazonaws.com"]
    error_message = "IAM policy, credential and password-policy changes alert (CIS CloudWatch.4)."
  }

  assert {
    condition = length(setsubtract(
      ["PutBucketPolicy", "PutBucketAcl", "DeleteBucketPolicy", "PutBucketReplication"],
      output.posture.alert_rules["bucket-policy-changes"].detail.eventName,
    )) == 0
    error_message = "S3 bucket policy changes on any bucket alert (CIS CloudWatch.8)."
  }

  assert {
    condition = length(setsubtract(
      ["AuthorizeSecurityGroupIngress", "CreateNetworkAclEntry", "AttachInternetGateway", "CreateRoute", "ModifyVpcAttribute", "CreateVpcPeeringConnection"],
      output.posture.alert_rules["network-changes"].detail.eventName,
    )) == 0
    error_message = "Security group, NACL, gateway, route table and VPC changes alert (CIS CloudWatch.10-14)."
  }
}

run "global_forwarding_can_be_disabled" {
  command = plan

  variables {
    forward_global_events = false
  }

  assert {
    condition     = output.posture.global_forward == null
    error_message = "No us-east-1 resources when global forwarding is off."
  }
}

run "governance_mode_for_staging" {
  command = plan

  variables {
    env                          = "staging"
    object_lock_mode             = "GOVERNANCE"
    log_retention_days           = 180
    delete_exempt_principal_arns = ["arn:aws:iam::111122223333:role/sos-staging-teardown"]
  }

  assert {
    condition     = output.posture.trail_bucket.object_lock_mode == "GOVERNANCE" && output.posture.trail_bucket.object_lock_days == 180
    error_message = "Staging may use GOVERNANCE mode with the 180-day CERT-In minimum."
  }
}

run "retention_below_cert_in_minimum_rejected" {
  command = plan

  variables {
    log_retention_days = 179
  }

  expect_failures = [var.log_retention_days]
}

run "local_env_rejected" {
  command = plan

  variables {
    env = "local"
  }

  expect_failures = [var.env]
}

run "log_buckets_never_data_event_sources" {
  command = plan

  variables {
    data_event_bucket_name_prefixes = ["sos-test-"]
  }

  expect_failures = [aws_cloudtrail.this]
}

run "alert_recipient_required" {
  command = plan

  variables {
    alert_emails = []
  }

  expect_failures = [var.alert_emails]
}
