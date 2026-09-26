output "task_definition_arn" {
  description = "Task definition ARN (revisioned)."
  value       = aws_ecs_task_definition.this.arn
}

output "task_definition_family" {
  description = "Task definition family (use with `aws ecs run-task --task-definition <family>`)."
  value       = aws_ecs_task_definition.this.family
}

output "service_name" {
  description = "Service name (null for one-off tasks)."
  value       = var.create_service ? aws_ecs_service.this[0].name : null
}

output "service_arn" {
  description = "Service ARN (null for one-off tasks)."
  value       = var.create_service ? aws_ecs_service.this[0].id : null
}

output "security_group_id" {
  description = "Task security group."
  value       = aws_security_group.this.id
}

output "task_role_arn" {
  description = "Task role ARN."
  value       = aws_iam_role.task.arn
}

output "execution_role_arn" {
  description = "Execution role ARN."
  value       = aws_iam_role.execution.arn
}

output "log_group_name" {
  description = "CloudWatch log group."
  value       = aws_cloudwatch_log_group.this.name
}

output "container_definition" {
  description = "Rendered main container definition (asserted by tests; contains no secret values, only ARNs)."
  value       = local.container
}
