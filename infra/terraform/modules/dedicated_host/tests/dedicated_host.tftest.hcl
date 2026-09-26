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
