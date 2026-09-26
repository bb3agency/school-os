# SEC-030 container hardening + SEC-009 secret injection for every Fargate task.

mock_provider "aws" {
  mock_data "aws_iam_policy_document" {
    defaults = { json = "{\"Version\":\"2012-10-17\",\"Statement\":[]}" }
  }
  mock_data "aws_region" {
    defaults = { region = "ap-south-1" }
  }
}

variables {
  name            = "sos-test-api"
  cluster_arn     = "arn:aws:ecs:ap-south-1:111122223333:cluster/sos-test"
  cluster_name    = "sos-test"
  image           = "111122223333.dkr.ecr.ap-south-1.amazonaws.com/schoolos/api:2026.10.1"
  container_port  = 8000
  vpc_id          = "vpc-0123456789abcdef0"
  vpc_cidr        = "10.20.0.0/16"
  subnet_ids      = ["subnet-00000000000000001"]
  log_kms_key_arn = "arn:aws:kms:ap-south-1:111122223333:key/00000000-0000-0000-0000-000000000001"
  ingress_from_security_groups = {
    web = "sg-00000000000000001"
  }
  secrets = {
    SOS_DATABASE_URL = "arn:aws:secretsmanager:ap-south-1:111122223333:secret:sos/test/db/app-AbCdEf:url::"
  }
  secret_arns          = ["arn:aws:secretsmanager:ap-south-1:111122223333:secret:sos/test/db/app-AbCdEf"]
  secrets_kms_key_arns = ["arn:aws:kms:ap-south-1:111122223333:key/00000000-0000-0000-0000-000000000001"]
}

run "container_hardened" {
  command = plan

  assert {
    condition     = output.container_definition.readonlyRootFilesystem && !output.container_definition.privileged
    error_message = "Read-only root filesystem, not privileged."
  }

  assert {
    condition     = output.container_definition.user == "10001:10001"
    error_message = "Runs as the non-root application UID."
  }

  assert {
    condition     = output.container_definition.linuxParameters.capabilities.drop == ["ALL"] && output.container_definition.linuxParameters.initProcessEnabled
    error_message = "All capabilities dropped; init process reaps zombies."
  }

  assert {
    condition     = [for m in output.container_definition.mountPoints : m.containerPath] == ["/tmp"]
    error_message = "/tmp is the only writable path (task-scoped ephemeral volume)."
  }
}

run "secrets_injected_not_plain" {
  command = plan

  assert {
    condition     = length(output.container_definition.secrets) == 1 && output.container_definition.secrets[0].name == "SOS_DATABASE_URL"
    error_message = "Secrets are injected via valueFrom."
  }

  assert {
    condition     = length(aws_iam_role_policy.execution_secrets) == 1
    error_message = "Execution role may read exactly the injected secrets."
  }
}

run "logs_retained_400_days_encrypted" {
  command = plan

  assert {
    condition     = aws_cloudwatch_log_group.this.retention_in_days == 400 && aws_cloudwatch_log_group.this.kms_key_id == var.log_kms_key_arn
    error_message = "Logs kept 400 days and KMS-encrypted."
  }
}

run "no_cidr_ingress_and_rollback" {
  command = plan

  assert {
    condition     = alltrue([for r in aws_vpc_security_group_ingress_rule.from_sg : r.cidr_ipv4 == null])
    error_message = "Task ingress only from referenced security groups."
  }

  assert {
    condition     = one(aws_ecs_service.this[0].deployment_circuit_breaker).rollback
    error_message = "Deployment circuit breaker rolls back failed deploys."
  }

  assert {
    condition     = !aws_ecs_service.this[0].network_configuration[0].assign_public_ip
    error_message = "No public IPs by default."
  }
}

run "one_off_task_has_no_service" {
  command = plan

  variables {
    create_service = false
    container_port = null
  }

  assert {
    condition     = length(aws_ecs_service.this) == 0
    error_message = "One-off tasks create only a task definition."
  }
}

run "root_user_rejected" {
  command = plan

  variables {
    user = "0:0"
  }

  expect_failures = [var.user]
}
