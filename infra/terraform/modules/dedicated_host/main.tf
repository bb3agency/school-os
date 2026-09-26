# Dedicated tier (premium plan): one isolated EC2 host per school in ap-south-1 running the SchoolOS
# containers from deploy/dedicated/compose.yaml. It is a one-tenant install: tenant_id + RLS stay on and
# SOS_DEPLOYMENT_MODE=dedicated disables control-plane routes.
#
# Controls: IMDSv2 only, no SSH/key pair (SSM Session Manager only), SG inbound 80/443 only, encrypted
# gp3 root + separate encrypted data volume (per-school CMK), per-school files bucket, backups to
# ap-south-2, instance role limited to this school's buckets/keys/secrets, no secrets in outputs or state.

data "aws_caller_identity" "current" {}
data "aws_partition" "current" {}
data "aws_region" "current" {}

data "aws_subnet" "this" {
  id = var.subnet_id
}

locals {
  name      = "sos-ded-${var.school_code}"
  partition = data.aws_partition.current.partition
  account   = data.aws_caller_identity.current.account_id
  arch      = can(regex("^[a-z]+[0-9]+[a-z]*g[a-z]*\\.", var.instance_type)) ? "arm64" : "amd64"
  generated_keys = toset([
    "POSTGRES_PASSWORD",
    "SOS_APP_DB_PASSWORD",
    "SOS_MIGRATOR_DB_PASSWORD",
    "SOS_PLATFORM_DB_PASSWORD",
    "SOS_READONLY_DB_PASSWORD",
    "VALKEY_PASSWORD",
    "SOS_SERVICE_TOKEN_KEY",
    "SESSION_SECRET",
  ])
  tags = merge(var.tags, { school_code = var.school_code, deployment_mode = "dedicated" })
}

# Ubuntu 24.04 LTS (unattended-upgrades, SSM agent preinstalled). The AMI is resolved at create time;
# later AMI releases do not replace the host (ignore_changes) - the OS is patched in place.
data "aws_ssm_parameter" "ubuntu" {
  name = "/aws/service/canonical/ubuntu/server/24.04/stable/current/${local.arch}/hvm/ebs-gp3/ami-id"
}

# --- Files bucket ---------------------------------------------------------------------

module "files" {
  source = "../s3_bucket"

  name        = "${local.name}-files-${local.account}"
  kms_key_arn = var.kms_key_arn
  lifecycle_rules = [
    { id = "exports-7d", tags = { "sos-lifecycle" = "export-7d" }, expiration_days = 7 },
    { id = "tenant-export-2d", tags = { "sos-lifecycle" = "tenant-export-2d" }, expiration_days = 2 },
    { id = "import-raw-90d", tags = { "sos-lifecycle" = "import-raw-90d" }, expiration_days = 90 },
    { id = "noncurrent-and-multipart", noncurrent_version_expiration_days = 90, abort_incomplete_multipart_days = 7 },
  ]
  tags = local.tags
}

# --- Secrets (values never in state) ----------------------------------------------------

ephemeral "random_password" "generated" {
  for_each = local.generated_keys

  length  = 48
  special = false
}

resource "aws_secretsmanager_secret" "generated" {
  name        = "sos/dedicated/${var.school_code}/generated"
  description = "Generated credentials for the ${var.school_code} dedicated host (JSON env map)"
  kms_key_id  = var.kms_key_arn
  tags        = local.tags
}

resource "aws_secretsmanager_secret_version" "generated" {
  secret_id                = aws_secretsmanager_secret.generated.id
  secret_string_wo         = jsonencode({ for k in local.generated_keys : k => ephemeral.random_password.generated[k].result })
  secret_string_wo_version = var.generated_secret_version
}

resource "aws_secretsmanager_secret" "operator" {
  name        = "sos/dedicated/${var.school_code}/operator"
  description = "Operator-supplied values for ${var.school_code} (API keys, fleet HMAC key). Set with put-secret-value."
  kms_key_id  = var.kms_key_arn
  tags        = merge(local.tags, { value_source = "operator" })
}

resource "aws_secretsmanager_secret_version" "operator" {
  secret_id                = aws_secretsmanager_secret.operator.id
  secret_string_wo         = jsonencode({ for k in var.operator_secret_keys : k => "__SET_ME__" })
  secret_string_wo_version = 1
}

# --- IAM ------------------------------------------------------------------------------

data "aws_iam_policy_document" "assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ec2.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "host" {
  name               = "${local.name}-host"
  assume_role_policy = data.aws_iam_policy_document.assume.json
  tags               = local.tags
}

resource "aws_iam_role_policy_attachment" "ssm" {
  role       = aws_iam_role.host.name
  policy_arn = "arn:${local.partition}:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

data "aws_iam_policy_document" "host" {
  statement {
    sid       = "FilesBucketList"
    actions   = ["s3:ListBucket"]
    resources = [module.files.arn]
  }

  statement {
    sid = "FilesBucketObjects"
    actions = [
      "s3:GetObject", "s3:PutObject", "s3:DeleteObject", "s3:GetObjectVersion",
      "s3:GetObjectTagging", "s3:PutObjectTagging", "s3:AbortMultipartUpload",
    ]
    resources = ["${module.files.arn}/*"]
  }

  # Backups: write + read for restore drills; no delete (lifecycle expires old dumps).
  statement {
    sid       = "BackupBucket"
    actions   = ["s3:ListBucket", "s3:GetObject", "s3:PutObject", "s3:AbortMultipartUpload"]
    resources = [var.backup_bucket_arn, "${var.backup_bucket_arn}/*"]
  }

  statement {
    sid       = "ReleaseBundles"
    actions   = ["s3:GetObject"]
    resources = ["${var.artifacts_bucket_arn}/dedicated/*"]
  }

  statement {
    sid       = "SchoolKeys"
    actions   = ["kms:Encrypt", "kms:Decrypt", "kms:ReEncrypt*", "kms:GenerateDataKey*", "kms:DescribeKey"]
    resources = [var.kms_key_arn, var.backup_kms_key_arn]
  }

  statement {
    sid       = "ArtifactsDecrypt"
    actions   = ["kms:Decrypt"]
    resources = [var.artifacts_kms_key_arn]
  }

  statement {
    sid       = "OwnSecrets"
    actions   = ["secretsmanager:GetSecretValue", "secretsmanager:DescribeSecret"]
    resources = [aws_secretsmanager_secret.generated.arn, aws_secretsmanager_secret.operator.arn, var.oidc_client_secret_arn]
  }

  statement {
    sid       = "EcrAuth"
    actions   = ["ecr:GetAuthorizationToken"]
    resources = ["*"]
  }

  statement {
    sid       = "EcrPull"
    actions   = ["ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer", "ecr:BatchCheckLayerAvailability"]
    resources = var.ecr_repository_arns
  }

  statement {
    sid       = "BackupMetrics"
    actions   = ["cloudwatch:PutMetricData"]
    resources = ["*"]
    condition {
      test     = "StringEquals"
      variable = "cloudwatch:namespace"
      values   = ["SchoolOS/Dedicated"]
    }
  }
}

resource "aws_iam_role_policy" "host" {
  name   = "schoolos-host"
  role   = aws_iam_role.host.id
  policy = data.aws_iam_policy_document.host.json
}

resource "aws_iam_instance_profile" "host" {
  name = "${local.name}-host"
  role = aws_iam_role.host.name
  tags = local.tags
}

# --- Network ----------------------------------------------------------------------------

resource "aws_security_group" "host" {
  name        = "${local.name}-host"
  description = "Dedicated SchoolOS host: 80/443 inbound only; no SSH"
  vpc_id      = var.vpc_id
  tags        = merge(local.tags, { Name = "${local.name}-host" })
}

# Schools reach their install over the internet; Caddy serves HTTPS and uses :80 for ACME + redirect.
#trivy:ignore:AVD-AWS-0107
resource "aws_vpc_security_group_ingress_rule" "https" {
  security_group_id = aws_security_group.host.id
  description       = "HTTPS (Caddy)"
  ip_protocol       = "tcp"
  from_port         = 443
  to_port           = 443
  cidr_ipv4         = "0.0.0.0/0"
}

#trivy:ignore:AVD-AWS-0107
resource "aws_vpc_security_group_ingress_rule" "http3" {
  security_group_id = aws_security_group.host.id
  description       = "HTTP/3 (QUIC, Caddy)"
  ip_protocol       = "udp"
  from_port         = 443
  to_port           = 443
  cidr_ipv4         = "0.0.0.0/0"
}

#trivy:ignore:AVD-AWS-0107
resource "aws_vpc_security_group_ingress_rule" "http" {
  security_group_id = aws_security_group.host.id
  description       = "HTTP (ACME HTTP-01 + redirect to HTTPS)"
  ip_protocol       = "tcp"
  from_port         = 80
  to_port           = 80
  cidr_ipv4         = "0.0.0.0/0"
}

# Outbound 443: ECR, S3, Secrets Manager, KMS, SSM, ACME, Cognito, LLM/embeddings/OCR APIs, heartbeat.
#trivy:ignore:AVD-AWS-0104
resource "aws_vpc_security_group_egress_rule" "https" {
  security_group_id = aws_security_group.host.id
  description       = "HTTPS egress"
  ip_protocol       = "tcp"
  from_port         = 443
  to_port           = 443
  cidr_ipv4         = "0.0.0.0/0"
}

# Outbound 80: Ubuntu archive/security mirrors (packages are GPG-signed) and ACME OCSP.
#trivy:ignore:AVD-AWS-0104
resource "aws_vpc_security_group_egress_rule" "http" {
  security_group_id = aws_security_group.host.id
  description       = "HTTP egress for signed OS packages"
  ip_protocol       = "tcp"
  from_port         = 80
  to_port           = 80
  cidr_ipv4         = "0.0.0.0/0"
}

# --- Storage + instance ------------------------------------------------------------------

resource "aws_ebs_volume" "data" {
  availability_zone = data.aws_subnet.this.availability_zone
  size              = var.data_volume_gb
  type              = "gp3"
  encrypted         = true
  kms_key_id        = var.kms_key_arn
  final_snapshot    = true
  tags              = merge(local.tags, { Name = "${local.name}-data", "sos-dlm" = local.name })
}

resource "aws_instance" "host" {
  ami                                  = data.aws_ssm_parameter.ubuntu.value
  instance_type                        = var.instance_type
  subnet_id                            = var.subnet_id
  vpc_security_group_ids               = [aws_security_group.host.id]
  iam_instance_profile                 = aws_iam_instance_profile.host.name
  associate_public_ip_address          = false
  ebs_optimized                        = true
  monitoring                           = true
  disable_api_termination              = var.termination_protection
  instance_initiated_shutdown_behavior = "stop"

  metadata_options {
    http_endpoint = "enabled"
    http_tokens   = "required"
    # 2 hops so containers on the Docker bridge can obtain the instance-role credentials.
    http_put_response_hop_limit = 2
    instance_metadata_tags      = "disabled"
  }

  root_block_device {
    volume_type           = "gp3"
    volume_size           = var.root_volume_gb
    encrypted             = true
    kms_key_id            = var.kms_key_arn
    delete_on_termination = true
  }

  user_data = templatefile("${path.module}/templates/cloud-init.yaml.tftpl", {
    school_code            = var.school_code
    region                 = data.aws_region.current.region
    backup_region          = var.backup_region
    public_host            = var.public_host
    custom_domain          = var.custom_domain
    acme_email             = var.acme_email
    release_version        = var.release_version
    ecr_registry           = var.ecr_registry
    files_bucket           = module.files.id
    backup_bucket          = var.backup_bucket_name
    backup_kms_key_arn     = var.backup_kms_key_arn
    kms_key_arn            = var.kms_key_arn
    data_volume_id         = aws_ebs_volume.data.id
    secret_json_ids        = "${aws_secretsmanager_secret.generated.arn} ${aws_secretsmanager_secret.operator.arn}"
    oidc_client_secret_arn = var.oidc_client_secret_arn
    oidc_issuer            = var.oidc_issuer
    oidc_client_id         = var.oidc_client_id
    control_plane_url      = var.control_plane_url
    bundle_s3_uri          = var.bundle_s3_uri
    bundle_sha256          = var.bundle_sha256
    walg_enabled           = var.walg_enabled ? "true" : "false"
  })
  user_data_replace_on_change = false

  tags        = merge(local.tags, { Name = local.name })
  volume_tags = merge(local.tags, { Name = "${local.name}-root" })

  lifecycle {
    # Never replace a school's host because a newer AMI appeared or bootstrap config changed.
    ignore_changes = [ami, user_data]
  }
}

resource "aws_volume_attachment" "data" {
  device_name                    = "/dev/sdf"
  volume_id                      = aws_ebs_volume.data.id
  instance_id                    = aws_instance.host.id
  stop_instance_before_detaching = true
}

resource "aws_eip" "host" {
  domain = "vpc"
  tags   = merge(local.tags, { Name = local.name })
}

resource "aws_eip_association" "host" {
  allocation_id = aws_eip.host.id
  instance_id   = aws_instance.host.id
}

resource "aws_route53_record" "public_host" {
  count = var.route53_zone_id == null ? 0 : 1

  zone_id = var.route53_zone_id
  name    = var.public_host
  type    = "A"
  ttl     = 300
  records = [aws_eip.host.public_ip]
}

# --- Daily EBS snapshots (crash-consistent, complements pg_dump/WAL-G) ------------------------

data "aws_iam_policy_document" "dlm_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["dlm.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "dlm" {
  count = var.ebs_snapshots_enabled ? 1 : 0

  name               = "${local.name}-dlm"
  assume_role_policy = data.aws_iam_policy_document.dlm_assume.json
  tags               = local.tags
}

resource "aws_iam_role_policy_attachment" "dlm" {
  count = var.ebs_snapshots_enabled ? 1 : 0

  role       = aws_iam_role.dlm[0].name
  policy_arn = "arn:${local.partition}:iam::aws:policy/service-role/AWSDataLifecycleManagerServiceRole"
}

resource "aws_dlm_lifecycle_policy" "data" {
  count = var.ebs_snapshots_enabled ? 1 : 0

  description        = "Daily snapshots of ${local.name} data volume"
  execution_role_arn = aws_iam_role.dlm[0].arn
  state              = "ENABLED"

  policy_details {
    resource_types = ["VOLUME"]
    target_tags    = { "sos-dlm" = local.name }

    schedule {
      name      = "daily-0200-ist"
      copy_tags = true
      create_rule {
        interval      = 24
        interval_unit = "HOURS"
        times         = ["20:30"]
      }
      retain_rule {
        count = 7
      }
    }
  }

  tags = local.tags
}
