# ADR-0025 option A (FR-EXP-002, SEC-030, SEC-011, NFR-SEC-005): the shared tier's pdf capacity runs
# the ECS-optimized AL2023 AMI with IMDSv2 only, no SSH, encrypted root volumes, a least-privilege
# instance role, and a Docker daemon whose default seccomp profile is the worker profile.

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
  mock_data "aws_ssm_parameter" {
    defaults = { value = "ami-0123456789abcdef0" }
  }
  mock_resource "aws_iam_service_linked_role" {
    defaults = { arn = "arn:aws:iam::111122223333:role/aws-service-role/autoscaling.amazonaws.com/AWSServiceRoleForAutoScaling_sos-test-pdf" }
  }
  mock_resource "aws_iam_instance_profile" {
    defaults = { arn = "arn:aws:iam::111122223333:instance-profile/sos-test-pdf-instance" }
  }
  mock_resource "aws_autoscaling_group" {
    defaults = { arn = "arn:aws:autoscaling:ap-south-1:111122223333:autoScalingGroup:00000000-0000-0000-0000-000000000000:autoScalingGroupName/sos-test-pdf" }
  }
}

variables {
  name            = "sos-test-pdf"
  cluster_name    = "sos-test"
  cluster_arn     = "arn:aws:ecs:ap-south-1:111122223333:cluster/sos-test"
  vpc_id          = "vpc-0123456789abcdef0"
  subnet_ids      = ["subnet-00000000000000001", "subnet-00000000000000002"]
  ebs_kms_key_arn = "arn:aws:kms:ap-south-1:111122223333:key/00000000-0000-0000-0000-00000000da7a"
}

run "hardened_instances" {
  command = plan

  assert {
    condition     = output.posture.imdsv2_required && output.posture.imds_hop_limit == 1
    error_message = "IMDSv2 only, hop limit 1 (tasks are also blocked from IMDS by ECS_AWSVPC_BLOCK_IMDS)."
  }

  assert {
    condition     = output.posture.key_name == null
    error_message = "No key pair: SSM Session Manager only."
  }

  assert {
    condition     = output.posture.root_encrypted && output.posture.root_kms_key_id == var.ebs_kms_key_arn
    error_message = "Root volumes are encrypted with the given CMK (SEC-011)."
  }

  assert {
    condition     = output.posture.public_ip == "false"
    error_message = "Instances get no public IP by default."
  }

  assert {
    condition     = output.posture.ami_parameter == "/aws/service/ecs/optimized-ami/amazon-linux-2023/arm64/recommended/image_id" && output.cpu_architecture == "ARM64"
    error_message = "t4g selects the arm64 ECS-optimized Amazon Linux 2023 AMI from its public SSM parameter."
  }

  assert {
    condition     = aws_vpc_security_group_egress_rule.https.from_port == 443 && aws_vpc_security_group_egress_rule.https.to_port == 443
    error_message = "Outbound HTTPS only (the module declares no inbound rule)."
  }

  assert {
    condition     = aws_launch_template.this.instance_initiated_shutdown_behavior == "terminate"
    error_message = "A failed bootstrap powers off and the group replaces the instance."
  }
}

run "daemon_uses_the_worker_seccomp_profile" {
  command = plan

  assert {
    condition = alltrue([
      strcontains(output.user_data, "cfg[\"seccomp-profile\"] = \"/etc/docker/seccomp-worker.json\""),
      strcontains(output.user_data, base64gzip(file("${path.module}/files/seccomp-worker.json"))),
      strcontains(output.user_data, sha256(file("${path.module}/files/seccomp-worker.json"))),
      strcontains(output.user_data, "profile=/etc/docker/seccomp-worker.json"),
      strcontains(output.user_data, "systemctl mask --now ecs.service"),
    ])
    error_message = "User data installs the worker profile as dockerd's default, verifies it, and never starts ECS otherwise."
  }

  assert {
    condition = alltrue([
      strcontains(output.user_data, "ECS_CLUSTER=sos-test\n"),
      strcontains(output.user_data, "ECS_AWSVPC_BLOCK_IMDS=true"),
      strcontains(output.user_data, "ECS_DISABLE_PRIVILEGED=true"),
      strcontains(output.user_data, "ECS_ENABLE_AWSLOGS_EXECUTIONROLE_OVERRIDE=true"),
      strcontains(output.user_data, "ECS_INSTANCE_ATTRIBUTES={\"schoolos.seccomp\":\"chromium-sandbox\"}"),
      strcontains(output.user_data, "user.max_user_namespaces = 15000"),
    ])
    error_message = "ECS agent: this cluster, no IMDS for tasks, no privileged containers, placement attribute, user namespaces on."
  }

  assert {
    condition     = length(output.user_data) < 16384 && !strcontains(lower(output.user_data), "password") && !strcontains(output.user_data, "secretsmanager")
    error_message = "User data fits the 16 KB limit and carries no secrets."
  }

  assert {
    condition     = output.placement_constraint == "attribute:schoolos.seccomp == chromium-sandbox"
    error_message = "Services pin themselves to these instances with memberOf."
  }
}

run "least_privilege_and_managed_capacity" {
  command = plan

  assert {
    condition     = aws_iam_role_policy_attachment.ssm.policy_arn == "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
    error_message = "SSM agent (Session Manager, patching) via the managed core policy."
  }

  assert {
    condition = alltrue([
      for s in data.aws_iam_policy_document.ecs_agent.statement :
      alltrue([for a in s.actions : startswith(a, "ecs:")]) && (s.resources == toset(["*"]) ? toset(s.actions) == toset(["ecs:DiscoverPollEndpoint"]) : true)
    ])
    error_message = "The instance role holds ECS agent actions only (no ECR, logs or CreateCluster); only DiscoverPollEndpoint is unscoped."
  }

  assert {
    condition     = aws_kms_grant.autoscaling.key_id == var.ebs_kms_key_arn && aws_iam_service_linked_role.autoscaling.custom_suffix == "sos-test-pdf"
    error_message = "A suffixed Auto Scaling service-linked role gets a grant on the volume CMK."
  }

  assert {
    condition = (
      aws_ecs_capacity_provider.this.auto_scaling_group_provider[0].managed_draining == "ENABLED"
      && aws_ecs_capacity_provider.this.auto_scaling_group_provider[0].managed_scaling[0].status == "ENABLED"
      && aws_autoscaling_group.this.min_size == 1 && aws_autoscaling_group.this.max_size == 2
    )
    error_message = "ECS managed scaling and draining; one warm instance by default."
  }

  assert {
    condition     = one(aws_autoscaling_group.this.instance_refresh).strategy == "Rolling" && one(one(aws_autoscaling_group.this.instance_refresh).preferences).min_healthy_percentage == 100
    error_message = "New AMIs roll out by instance refresh, launching before terminating."
  }
}

run "x86_instance_types_use_the_x86_ami" {
  command = plan

  variables {
    instance_type = "t3.medium"
  }

  assert {
    condition     = output.posture.ami_parameter == "/aws/service/ecs/optimized-ami/amazon-linux-2023/recommended/image_id" && output.cpu_architecture == "X86_64"
    error_message = "Non-Graviton instance types use the x86_64 AMI."
  }
}

run "name_must_not_use_reserved_prefixes" {
  command = plan

  variables {
    name = "ecs-pdf"
  }

  expect_failures = [var.name]
}
