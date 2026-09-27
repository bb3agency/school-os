# ECS capacity provider on an EC2 Auto Scaling group whose Docker daemon's default seccomp profile
# allows Chromium's sandbox (ADR-0025 option A: the shared tier's `pdf` queue; docs/10 §6).
#
# ECS task definitions cannot name a seccomp profile and Fargate forbids user namespaces, so the
# only way to run the sandbox on ECS is a daemon-wide default profile on instances that run
# nothing else. That profile is deploy/dedicated/security/seccomp-worker.json (a byte copy lives in
# files/; apps/api/tests/deploy/test_chromium_sandbox.py keeps them equal).
#
# Controls: ECS-optimized Amazon Linux 2023 (AMI from the public SSM parameter; a new AMI makes a new
# launch-template version and a rolling instance refresh), IMDSv2 only with hop limit 1 and tasks
# blocked from IMDS, no SSH/key pair (SSM Session Manager only), no inbound rules, root volume
# encrypted with the given CMK, instance role limited to the ECS agent on this cluster + SSM core,
# user data without secrets that refuses to start the ECS agent unless dockerd uses the profile.

data "aws_caller_identity" "current" {}
data "aws_partition" "current" {}
data "aws_region" "current" {}

locals {
  partition = data.aws_partition.current.partition
  region    = data.aws_region.current.region
  account   = data.aws_caller_identity.current.account_id
  arm64     = can(regex("^[a-z]+[0-9]+[a-z]*g[a-z]*\\.", var.instance_type))
  # ECS-optimized Amazon Linux 2023, recommended image for the architecture.
  ami_parameter = local.arm64 ? "/aws/service/ecs/optimized-ami/amazon-linux-2023/arm64/recommended/image_id" : "/aws/service/ecs/optimized-ami/amazon-linux-2023/recommended/image_id"

  seccomp_profile = file("${path.module}/files/seccomp-worker.json")
  user_data = templatefile("${path.module}/templates/user-data.sh.tftpl", {
    cluster_name           = var.cluster_name
    attribute_name         = var.instance_attribute.name
    attribute_value        = var.instance_attribute.value
    max_user_namespaces    = var.max_user_namespaces
    seccomp_profile_gz_b64 = base64gzip(local.seccomp_profile)
    seccomp_profile_sha256 = sha256(local.seccomp_profile)
  })

  tags = merge(var.tags, { "schoolos:capacity" = var.name })
}

data "aws_ssm_parameter" "ecs_ami" {
  name = local.ami_parameter
}

# --- Instance role: ECS agent on this cluster + SSM Session Manager --------------------------------

data "aws_iam_policy_document" "assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ec2.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "instance" {
  name               = "${var.name}-instance"
  assume_role_policy = data.aws_iam_policy_document.assume.json
  tags               = local.tags
}

# Least-privilege replacement for AmazonEC2ContainerServiceforEC2Role: no ECR, no CloudWatch Logs
# (the task execution role pulls images and writes logs: ECS_ENABLE_AWSLOGS_EXECUTIONROLE_OVERRIDE),
# no ecs:CreateCluster, and every cluster-scoped action pinned to this cluster.
data "aws_iam_policy_document" "ecs_agent" {
  statement {
    sid = "ClusterMembership"
    actions = [
      "ecs:RegisterContainerInstance",
      "ecs:DeregisterContainerInstance",
      "ecs:SubmitTaskStateChange",
      "ecs:SubmitContainerStateChange",
      "ecs:SubmitAttachmentStateChanges",
    ]
    resources = [var.cluster_arn]
  }

  statement {
    sid = "ContainerInstance"
    actions = [
      "ecs:Poll",
      "ecs:StartTelemetrySession",
      "ecs:UpdateContainerInstancesState",
    ]
    resources = ["arn:${local.partition}:ecs:${local.region}:${local.account}:container-instance/${var.cluster_name}/*"]
  }

  statement {
    sid       = "TagOnRegister"
    actions   = ["ecs:TagResource"]
    resources = ["arn:${local.partition}:ecs:${local.region}:${local.account}:container-instance/${var.cluster_name}/*"]
    condition {
      test     = "StringEquals"
      variable = "ecs:CreateAction"
      values   = ["RegisterContainerInstance"]
    }
  }

  # DiscoverPollEndpoint does not support resource-level permissions.
  statement {
    sid       = "DiscoverPollEndpoint"
    actions   = ["ecs:DiscoverPollEndpoint"]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "ecs_agent" {
  name   = "ecs-agent"
  role   = aws_iam_role.instance.id
  policy = data.aws_iam_policy_document.ecs_agent.json
}

resource "aws_iam_role_policy_attachment" "ssm" {
  role       = aws_iam_role.instance.name
  policy_arn = "arn:${local.partition}:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

resource "aws_iam_instance_profile" "instance" {
  name = "${var.name}-instance"
  role = aws_iam_role.instance.name
  tags = local.tags
}

# --- Network: no inbound; tasks have their own awsvpc ENIs and security groups ---------------------

resource "aws_security_group" "instance" {
  name        = "${var.name}-instance"
  description = "SchoolOS ${var.name} container instances (no inbound)"
  vpc_id      = var.vpc_id
  tags        = merge(local.tags, { Name = "${var.name}-instance" })
}

# The ECS agent, SSM agent and dnf reach AWS endpoints over HTTPS (NAT or interface endpoints).
#trivy:ignore:AVD-AWS-0104
resource "aws_vpc_security_group_egress_rule" "https" {
  security_group_id = aws_security_group.instance.id
  description       = "HTTPS to AWS APIs (ECS, SSM, ECR, package repositories)"
  ip_protocol       = "tcp"
  from_port         = 443
  to_port           = 443
  cidr_ipv4         = "0.0.0.0/0"
}

# --- Encrypted root volumes: the Auto Scaling group launches with its own service-linked role -------

# A suffixed service-linked role only this group uses, so the KMS grant is as narrow as possible and
# the account's default AWSServiceRoleForAutoScaling (which may already exist) is left alone.
resource "aws_iam_service_linked_role" "autoscaling" {
  aws_service_name = "autoscaling.amazonaws.com"
  custom_suffix    = var.name
  description      = "SchoolOS ${var.name}: launches the pdf capacity with CMK-encrypted volumes"
  tags             = local.tags
}

resource "aws_kms_grant" "autoscaling" {
  name              = "${var.name}-asg-ebs"
  key_id            = var.ebs_kms_key_arn
  grantee_principal = aws_iam_service_linked_role.autoscaling.arn
  operations = [
    "Encrypt", "Decrypt", "ReEncryptFrom", "ReEncryptTo", "GenerateDataKey",
    "GenerateDataKeyWithoutPlaintext", "DescribeKey", "CreateGrant",
  ]
}

# --- Launch template + Auto Scaling group ----------------------------------------------------------

resource "aws_launch_template" "this" {
  name_prefix   = "${var.name}-"
  description   = "SchoolOS ${var.name}: ECS AL2023, daemon seccomp profile for the Chromium sandbox (ADR-0025)"
  image_id      = data.aws_ssm_parameter.ecs_ami.value
  instance_type = var.instance_type
  user_data     = base64encode(local.user_data)
  ebs_optimized = true

  # Shutting down (a failed bootstrap does that) terminates the instance; the group replaces it.
  instance_initiated_shutdown_behavior = "terminate"

  iam_instance_profile {
    arn = aws_iam_instance_profile.instance.arn
  }

  metadata_options {
    http_endpoint               = "enabled"
    http_tokens                 = "required"
    http_put_response_hop_limit = 1
    instance_metadata_tags      = "disabled"
  }

  monitoring {
    enabled = true
  }

  network_interfaces {
    associate_public_ip_address = var.associate_public_ip_address
    security_groups             = [aws_security_group.instance.id]
    delete_on_termination       = true
  }

  block_device_mappings {
    device_name = "/dev/xvda"
    ebs {
      volume_type           = "gp3"
      volume_size           = var.root_volume_gb
      encrypted             = true
      kms_key_id            = var.ebs_kms_key_arn
      delete_on_termination = true
    }
  }

  tag_specifications {
    resource_type = "instance"
    tags          = merge(local.tags, { Name = var.name })
  }

  tag_specifications {
    resource_type = "volume"
    tags          = merge(local.tags, { Name = "${var.name}-root" })
  }

  tags = local.tags
}

resource "aws_autoscaling_group" "this" {
  name                    = var.name
  vpc_zone_identifier     = var.subnet_ids
  min_size                = var.min_size
  max_size                = var.max_size
  health_check_type       = "EC2"
  service_linked_role_arn = aws_iam_service_linked_role.autoscaling.arn
  # ECS managed termination protection is off (instance refresh must be able to replace instances);
  # managed draining moves tasks off an instance before it terminates.
  protect_from_scale_in = false
  # ECS managed scaling sets the desired capacity; do not wait for instances during apply.
  wait_for_capacity_timeout = "0"

  launch_template {
    id      = aws_launch_template.this.id
    version = aws_launch_template.this.latest_version
  }

  # A new AMI (SSM parameter) or user data makes a new launch-template version: replace instances
  # one at a time, launching the new one first so PDF rendering keeps running.
  instance_refresh {
    strategy = "Rolling"
    preferences {
      min_healthy_percentage = 100
      max_healthy_percentage = 200
      instance_warmup        = 300
    }
  }

  tag {
    key                 = "AmazonECSManaged"
    value               = "true"
    propagate_at_launch = true
  }

  dynamic "tag" {
    for_each = merge(local.tags, { Name = var.name })
    content {
      key                 = tag.key
      value               = tag.value
      propagate_at_launch = true
    }
  }

  depends_on = [aws_kms_grant.autoscaling, aws_iam_role_policy.ecs_agent, aws_iam_role_policy_attachment.ssm]

  lifecycle {
    ignore_changes = [desired_capacity]
  }
}

resource "aws_ecs_capacity_provider" "this" {
  name = var.name

  auto_scaling_group_provider {
    auto_scaling_group_arn         = aws_autoscaling_group.this.arn
    managed_termination_protection = "DISABLED"
    managed_draining               = "ENABLED"

    managed_scaling {
      status                    = "ENABLED"
      target_capacity           = var.target_capacity
      minimum_scaling_step_size = 1
      maximum_scaling_step_size = 1
      instance_warmup_period    = 300
    }
  }

  tags = local.tags
}
