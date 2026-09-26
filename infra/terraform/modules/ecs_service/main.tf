# Generic hardened Fargate service / one-off task (docs/10 §6, 07 §13):
# non-root user, read-only root filesystem, writable scratch via ephemeral volumes (Fargate has no tmpfs),
# all Linux capabilities dropped, init process, logs to CloudWatch (400 days, KMS), secrets injected from
# Secrets Manager by the execution role, least-privilege task role, circuit-breaker rollback.

data "aws_region" "current" {}

locals {
  volumes = { for i, p in var.writable_paths : "scratch-${i}" => p }

  container = merge(
    {
      name                   = var.container_name
      image                  = var.image
      essential              = true
      user                   = var.user
      readonlyRootFilesystem = true
      privileged             = false
      stopTimeout            = var.stop_timeout
      linuxParameters = {
        initProcessEnabled = true
        capabilities       = { drop = ["ALL"], add = [] }
      }
      environment = [for k in sort(keys(var.environment)) : { name = k, value = var.environment[k] }]
      secrets     = [for k in sort(keys(var.secrets)) : { name = k, valueFrom = var.secrets[k] }]
      mountPoints = [for v, p in local.volumes : { sourceVolume = v, containerPath = p, readOnly = false }]
      portMappings = var.container_port == null ? [] : [{
        name          = var.port_name
        containerPort = var.container_port
        protocol      = "tcp"
        appProtocol   = "http"
      }]
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          awslogs-group         = aws_cloudwatch_log_group.this.name
          awslogs-region        = data.aws_region.current.region
          awslogs-stream-prefix = var.container_name
          mode                  = "non-blocking"
          max-buffer-size       = "25m"
        }
      }
      ulimits = [{ name = "nofile", softLimit = 65536, hardLimit = 65536 }]
    },
    var.command == null ? {} : { command = var.command },
    var.entry_point == null ? {} : { entryPoint = var.entry_point },
    var.health_check == null ? {} : {
      healthCheck = {
        command     = var.health_check.command
        interval    = var.health_check.interval
        timeout     = var.health_check.timeout
        retries     = var.health_check.retries
        startPeriod = var.health_check.start_period
      }
    },
  )
}

resource "aws_cloudwatch_log_group" "this" {
  name              = "/schoolos/ecs/${var.name}"
  retention_in_days = var.log_retention_days
  kms_key_id        = var.log_kms_key_arn
  tags              = var.tags
}

# --- IAM -----------------------------------------------------------------------

data "aws_iam_policy_document" "assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "execution" {
  name               = "${var.name}-exec"
  assume_role_policy = data.aws_iam_policy_document.assume.json
  tags               = var.tags
}

resource "aws_iam_role_policy_attachment" "execution" {
  role       = aws_iam_role.execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

data "aws_iam_policy_document" "execution_secrets" {
  count = length(var.secret_arns) > 0 ? 1 : 0

  statement {
    sid       = "ReadInjectedSecrets"
    actions   = ["secretsmanager:GetSecretValue"]
    resources = var.secret_arns
  }

  dynamic "statement" {
    for_each = length(var.secrets_kms_key_arns) > 0 ? [1] : []
    content {
      sid       = "DecryptInjectedSecrets"
      actions   = ["kms:Decrypt"]
      resources = var.secrets_kms_key_arns
    }
  }
}

resource "aws_iam_role_policy" "execution_secrets" {
  count = length(var.secret_arns) > 0 ? 1 : 0

  name   = "inject-secrets"
  role   = aws_iam_role.execution.id
  policy = data.aws_iam_policy_document.execution_secrets[0].json
}

resource "aws_iam_role" "task" {
  name               = "${var.name}-task"
  assume_role_policy = data.aws_iam_policy_document.assume.json
  tags               = var.tags
}

resource "aws_iam_role_policy" "task" {
  count = var.attach_task_role_policy ? 1 : 0

  name   = "app"
  role   = aws_iam_role.task.id
  policy = var.task_role_policy_json
}

# --- Network -------------------------------------------------------------------

resource "aws_security_group" "this" {
  name        = "${var.name}-task"
  description = "SchoolOS ${var.name} tasks"
  vpc_id      = var.vpc_id
  tags        = merge(var.tags, { Name = "${var.name}-task" })
}

resource "aws_vpc_security_group_ingress_rule" "from_sg" {
  for_each = var.container_port == null ? {} : var.ingress_from_security_groups

  security_group_id            = aws_security_group.this.id
  description                  = "App port from ${each.key}"
  ip_protocol                  = "tcp"
  from_port                    = var.container_port
  to_port                      = var.container_port
  referenced_security_group_id = each.value
}

# Outbound HTTPS is required for AWS APIs (when no interface endpoints), Cognito JWKS and the
# LLM/embeddings/OCR providers (TB5). An egress domain allowlist (proxy/firewall) is the Stage 1 control.
#trivy:ignore:AVD-AWS-0104
resource "aws_vpc_security_group_egress_rule" "https" {
  count = var.egress_https_anywhere ? 1 : 0

  security_group_id = aws_security_group.this.id
  description       = "HTTPS to AWS APIs and third-party providers"
  ip_protocol       = "tcp"
  from_port         = 443
  to_port           = 443
  cidr_ipv4         = "0.0.0.0/0"
}

resource "aws_vpc_security_group_egress_rule" "vpc" {
  for_each = toset([for p in var.egress_vpc_ports : tostring(p)])

  security_group_id = aws_security_group.this.id
  description       = "TCP ${each.key} inside the VPC"
  ip_protocol       = "tcp"
  from_port         = tonumber(each.key)
  to_port           = tonumber(each.key)
  cidr_ipv4         = var.vpc_cidr
}

# --- Task definition -------------------------------------------------------------

resource "aws_ecs_task_definition" "this" {
  family                   = var.name
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.cpu
  memory                   = var.memory
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.task.arn
  container_definitions    = jsonencode([local.container])

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = var.cpu_architecture
  }

  dynamic "ephemeral_storage" {
    for_each = var.ephemeral_storage_gib == null ? [] : [1]
    content {
      size_in_gib = var.ephemeral_storage_gib
    }
  }

  dynamic "volume" {
    for_each = local.volumes
    content {
      name = volume.key
    }
  }

  tags = var.tags
}

# --- Service ---------------------------------------------------------------------

resource "aws_ecs_service" "this" {
  count = var.create_service ? 1 : 0

  name                   = var.name
  cluster                = var.cluster_arn
  task_definition        = aws_ecs_task_definition.this.arn
  desired_count          = var.desired_count
  launch_type            = "FARGATE"
  platform_version       = "LATEST"
  propagate_tags         = "SERVICE"
  enable_execute_command = var.enable_execute_command

  deployment_minimum_healthy_percent = 100
  deployment_maximum_percent         = 200
  health_check_grace_period_seconds  = var.attach_load_balancer ? 60 : null

  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }

  network_configuration {
    subnets          = var.subnet_ids
    security_groups  = [aws_security_group.this.id]
    assign_public_ip = var.assign_public_ip
  }

  dynamic "load_balancer" {
    for_each = var.attach_load_balancer ? [1] : []
    content {
      target_group_arn = var.load_balancer_target_group_arn
      container_name   = var.container_name
      container_port   = var.container_port
    }
  }

  dynamic "service_connect_configuration" {
    for_each = var.service_connect == null ? [] : [var.service_connect]
    content {
      enabled   = true
      namespace = service_connect_configuration.value.namespace_arn

      dynamic "service" {
        for_each = service_connect_configuration.value.server ? [1] : []
        content {
          port_name      = var.port_name
          discovery_name = service_connect_configuration.value.discovery_name
          client_alias {
            port     = service_connect_configuration.value.client_alias_port
            dns_name = service_connect_configuration.value.discovery_name
          }
        }
      }
    }
  }

  lifecycle {
    # Autoscaling owns the running count once enabled.
    ignore_changes = [desired_count]
  }

  tags = var.tags
}

resource "aws_appautoscaling_target" "this" {
  count = var.create_service && var.autoscaling != null ? 1 : 0

  service_namespace  = "ecs"
  scalable_dimension = "ecs:service:DesiredCount"
  resource_id        = "service/${var.cluster_name}/${aws_ecs_service.this[0].name}"
  min_capacity       = var.autoscaling.min_capacity
  max_capacity       = var.autoscaling.max_capacity
}

resource "aws_appautoscaling_policy" "cpu" {
  count = var.create_service && var.autoscaling != null ? 1 : 0

  name               = "${var.name}-cpu"
  policy_type        = "TargetTrackingScaling"
  service_namespace  = aws_appautoscaling_target.this[0].service_namespace
  scalable_dimension = aws_appautoscaling_target.this[0].scalable_dimension
  resource_id        = aws_appautoscaling_target.this[0].resource_id

  target_tracking_scaling_policy_configuration {
    target_value       = var.autoscaling.cpu_target
    scale_in_cooldown  = 300
    scale_out_cooldown = 60
    predefined_metric_specification {
      predefined_metric_type = "ECSServiceAverageCPUUtilization"
    }
  }
}
