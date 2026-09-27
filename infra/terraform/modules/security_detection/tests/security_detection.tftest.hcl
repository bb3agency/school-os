# SEC-023: per-region GuardDuty (S3 + malware protection), AWS Config and Security Hub
# (FSBP + CIS), planned against mocks. Non-primary regions forward findings to the primary bus.

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

variables {
  name_prefix        = "sos-test"
  is_primary         = true
  config_role_arn    = "arn:aws:iam::111122223333:role/sos-test-config"
  config_bucket_name = "sos-test-security-evidence-111122223333"
  config_kms_key_arn = "arn:aws:kms:ap-south-1:111122223333:key/00000000-0000-0000-0000-000000000001"
  config_rules = {
    cloudtrail-enabled = "CLOUD_TRAIL_ENABLED"
    root-mfa           = "ROOT_ACCOUNT_MFA_ENABLED"
  }
}

run "guardduty_s3_and_malware_protection" {
  command = plan

  assert {
    condition     = output.posture.guardduty_enabled && output.posture.guardduty_frequency == "FIFTEEN_MINUTES"
    error_message = "GuardDuty must be enabled and publish every 15 minutes."
  }

  assert {
    condition     = output.posture.guardduty_features["S3_DATA_EVENTS"] == "ENABLED" && output.posture.guardduty_features["EBS_MALWARE_PROTECTION"] == "ENABLED"
    error_message = "S3 Protection and Malware Protection (EBS) must be enabled in the primary region."
  }

  assert {
    condition     = output.posture.guardduty_features["EKS_AUDIT_LOGS"] == "DISABLED" && output.posture.guardduty_features["RUNTIME_MONITORING"] == "DISABLED"
    error_message = "Features not in use are set explicitly to DISABLED."
  }

  assert {
    condition     = !output.posture.guardduty_export
    error_message = "No findings export unless configured."
  }
}

run "config_records_everything_encrypted" {
  command = plan

  assert {
    condition     = output.posture.config_recording_all && output.posture.config_global_types && output.posture.config_recording_enabled
    error_message = "The primary region records all resource types, including global (IAM) ones."
  }

  assert {
    condition     = output.posture.config_delivery_kms == var.config_kms_key_arn
    error_message = "Config delivery is KMS-encrypted."
  }

  assert {
    condition     = output.posture.config_rules["root-mfa"] == "ROOT_ACCOUNT_MFA_ENABLED" && length(output.posture.config_rules) == 2
    error_message = "Managed Config rules are created in the primary region."
  }
}

run "securityhub_fsbp_and_cis" {
  command = plan

  assert {
    condition = output.posture.securityhub_standards == sort([
      "arn:aws:securityhub:ap-south-1::standards/aws-foundational-security-best-practices/v/1.0.0",
      "arn:aws:securityhub:ap-south-1::standards/cis-aws-foundations-benchmark/v/3.0.0",
    ])
    error_message = "Security Hub subscribes to FSBP and CIS in this region."
  }

  assert {
    condition     = !output.posture.securityhub_default_stds
    error_message = "Default standards are off; the list above is explicit."
  }

  assert {
    condition     = !output.posture.forwards_to_primary
    error_message = "The primary region does not forward."
  }
}

run "secondary_region_forwards_and_skips_global" {
  command = plan

  variables {
    is_primary               = false
    forward_to_event_bus_arn = "arn:aws:events:ap-south-1:111122223333:event-bus/default"
    tamper_event_sources     = ["guardduty.amazonaws.com"]
    tamper_event_names       = ["DeleteDetector"]
    guardduty_features = {
      S3_DATA_EVENTS         = true
      EBS_MALWARE_PROTECTION = false
    }
  }

  assert {
    condition     = !output.posture.config_global_types && length(output.posture.config_rules) == 0
    error_message = "Secondary regions record no global types and run no account-wide rules."
  }

  assert {
    condition     = output.posture.forwards_to_primary && output.posture.forward_target_event_bus == var.forward_to_event_bus_arn
    error_message = "Secondary regions forward to the primary bus."
  }

  assert {
    condition     = contains(output.posture.forward_event_pattern_obj["$or"][0].source, "aws.guardduty")
    error_message = "GuardDuty findings are forwarded."
  }

  assert {
    condition     = output.posture.guardduty_features["S3_DATA_EVENTS"] == "ENABLED"
    error_message = "S3 Protection stays on in every region."
  }
}

run "s3_protection_cannot_be_disabled" {
  command = plan

  variables {
    guardduty_features = { S3_DATA_EVENTS = false }
  }

  expect_failures = [var.guardduty_features]
}

run "fsbp_and_cis_required" {
  command = plan

  variables {
    securityhub_standards = ["aws-foundational-security-best-practices/v/1.0.0"]
  }

  expect_failures = [var.securityhub_standards]
}
