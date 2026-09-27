output "alb_dns_name" {
  description = "Point app_domain and admin_domain here (CNAME/alias) when not using Route 53."
  value       = module.alb.alb_dns_name
}

output "acm_validation_records" {
  description = "ACM DNS validation records (create them if route53_zone_id is null)."
  value       = module.alb.acm_validation_records
}

output "ecr_repository_urls" {
  description = "ECR repositories for CI pushes."
  value       = module.ecr.repository_urls
}

output "cluster_name" {
  description = "ECS cluster."
  value       = module.cluster.name
}

output "one_off_tasks" {
  description = "Task families for `aws ecs run-task` plus the network settings to use."
  value = {
    migrate           = module.migrate.task_definition_family
    db_bootstrap      = module.db_bootstrap.task_definition_family
    subnets           = local.task_subnets
    migrate_sg        = module.migrate.security_group_id
    db_bootstrap_sg   = module.db_bootstrap.security_group_id
    assign_public_ip  = local.assign_public_ip
    migrate_logs      = module.migrate.log_group_name
    db_bootstrap_logs = module.db_bootstrap.log_group_name
  }
}

output "rds_arn" {
  description = "RDS instance ARN (source for cross-region backup replication)."
  value       = module.rds.arn
}

output "buckets" {
  description = "Bucket names."
  value = {
    files     = module.s3.files_bucket
    audit     = module.s3.audit_bucket
    logs      = module.s3.logs_bucket
    artifacts = module.s3.artifacts_bucket
  }
}

output "artifacts_bucket_arn" {
  description = "Artifacts bucket ARN (dedicated hosts read release bundles from dedicated/)."
  value       = module.s3.artifacts_bucket_arn
}

output "kms_key_arns" {
  description = "CMK ARNs by purpose."
  value       = module.kms.key_arns
}

output "oidc" {
  description = "Non-secret OIDC settings for the app."
  value = {
    tenant_issuer        = module.cognito.tenant_issuer
    tenant_client_id     = module.cognito.tenant_client_id
    tenant_hosted_domain = module.cognito.tenant_hosted_domain
    platform_issuer      = module.cognito.platform_issuer
    platform_client_id   = module.cognito.platform_client_id
  }
}

output "secret_arns" {
  description = "Secrets Manager ARNs (values are never output). Operator secrets must be set with put-secret-value."
  value = {
    db                     = module.secrets.db_secret_arns
    generated              = module.secrets.random_secret_arns
    operator_supplied      = module.secrets.operator_secret_arns
    valkey                 = module.redis.secret_arn
    rds_master             = module.rds.master_user_secret_arn
    oidc_tenant_client     = module.cognito.tenant_client_secret_arn
    oidc_platform_client   = module.cognito.platform_client_secret_arn
    note_operator_supplied = "Values start as __SET_ME__; set them before the first deploy."
  }
}

output "github_actions" {
  description = "Role ARNs for GitHub Actions (configure-aws-credentials role-to-assume)."
  value = {
    deploy_role_arn = module.ci.deploy_role_arn
    plan_role_arn   = module.ci.plan_role_arn
    apply_role_arn  = module.ci.apply_role_arn
    deploy_subjects = module.ci.deploy_subjects
  }
}

output "alarm_topic_arn" {
  description = "Alarm SNS topic."
  value       = module.observability.alarm_topic_arn
}

output "nat_public_ips" {
  description = "Egress IPs (for third-party allowlists)."
  value       = module.network.nat_public_ips
}

output "security_posture" {
  description = "Security posture summary (asserted by env tests)."
  value = {
    rds                   = module.rds.posture
    redis                 = module.redis.posture
    ecr                   = module.ecr.posture
    cognito               = module.cognito.posture
    web_container         = module.web.container_definition
    api_container         = module.api.container_definition
    worker_container      = module.worker.container_definition
    beat_container        = module.beat.container_definition
    migrate_container     = module.migrate.container_definition
    audit_signing_key     = module.kms.key_properties["audit-signing"]
    audit_object_lock     = var.audit_object_lock_mode
    audit_object_lock_yrs = var.audit_object_lock_days == null ? var.audit_object_lock_years : null
  }
}
