output "db_secret_arns" {
  description = "Map of db role key to secret ARN (JSON keys: username, password, host, port, dbname, url)."
  value       = { for k, v in aws_secretsmanager_secret.db : k => v.arn }
}

output "random_secret_arns" {
  description = "Map of generated secret name to ARN."
  value       = { for k, v in aws_secretsmanager_secret.generic : k => v.arn }
}

output "operator_secret_arns" {
  description = "Map of operator-supplied secret name to ARN."
  value       = { for k, v in aws_secretsmanager_secret.operator : k => v.arn }
}

output "all_secret_arns" {
  description = "Every secret ARN created by this module."
  value = concat(
    [for v in aws_secretsmanager_secret.db : v.arn],
    [for v in aws_secretsmanager_secret.generic : v.arn],
    [for v in aws_secretsmanager_secret.operator : v.arn],
  )
}
