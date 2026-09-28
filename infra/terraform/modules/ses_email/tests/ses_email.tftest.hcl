# Staff invitation email through Amazon SES v2 (US-102, FR-IAM-013, FR-NOT-001, SEC-008): domain
# identity with Easy DKIM (2048-bit), a default configuration set that requires TLS and suppresses
# bounced/complaining addresses, reputation alarms without recipient addresses in alerts.

mock_provider "aws" {
  mock_resource "aws_sesv2_email_identity" {
    defaults = {
      arn = "arn:aws:ses:ap-south-1:111122223333:identity/mail.example.test"
      dkim_signing_attributes = {
        tokens                  = ["tokena", "tokenb", "tokenc"]
        next_signing_key_length = "RSA_2048_BIT"
      }
    }
  }
}

variables {
  name_prefix = "sos-test"
  domain      = "mail.example.test"
}

run "identity_and_configuration_set" {
  command = apply

  assert {
    condition = (
      output.posture.dkim_key_length == "RSA_2048_BIT"
      && output.posture.default_config_set == "sos-test-email"
      && output.posture.tls_policy == "REQUIRE"
      && output.posture.reputation_metrics
      && toset(output.posture.suppressed_reasons) == toset(["BOUNCE", "COMPLAINT"])
    )
    error_message = "Easy DKIM 2048, default configuration set with TLS required, reputation metrics and suppression."
  }

  assert {
    condition     = output.configuration_set_name == "sos-test-email"
    error_message = "The configuration set is named for SOS_EMAIL_SES_CONFIGURATION_SET."
  }

  assert {
    condition = output.dkim_records == [
      { name = "tokena._domainkey.mail.example.test", type = "CNAME", value = "tokena.dkim.amazonses.com" },
      { name = "tokenb._domainkey.mail.example.test", type = "CNAME", value = "tokenb.dkim.amazonses.com" },
      { name = "tokenc._domainkey.mail.example.test", type = "CNAME", value = "tokenc.dkim.amazonses.com" },
    ]
    error_message = "The three DKIM CNAMEs are output for manual DNS."
  }

  assert {
    condition     = output.posture.dkim_records_in_route53 == 0 && length(output.posture.reputation_alarms) == 0
    error_message = "No Route 53 records or alarms unless asked for."
  }
}

run "route53_and_alarms" {
  command = apply

  variables {
    route53_zone_id   = "Z0123456789ABCDEFGHIJ"
    reputation_alarms = true
    alarm_topic_arn   = "arn:aws:sns:ap-south-1:111122223333:sos-test-alarms"
  }

  assert {
    condition     = output.posture.dkim_records_in_route53 == 3 && aws_route53_record.dkim[0].records == toset(["tokena.dkim.amazonses.com"])
    error_message = "DKIM CNAMEs are created in the hosted zone."
  }

  assert {
    condition     = toset(output.posture.reputation_alarms) == toset(["bounce", "complaint"]) && aws_cloudwatch_metric_alarm.reputation["complaint"].threshold == 0.001
    error_message = "Bounce and complaint rate alarms go to the ops topic (no SES event notifications with addresses)."
  }
}

run "domain_is_validated" {
  command = plan

  variables {
    domain = "Not A Domain"
  }

  expect_failures = [var.domain]
}
