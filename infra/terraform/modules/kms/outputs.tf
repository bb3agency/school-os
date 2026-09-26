output "key_arns" {
  description = "Map of key name to key ARN."
  value       = { for k, v in aws_kms_key.this : k => v.arn }
}

output "key_ids" {
  description = "Map of key name to key ID."
  value       = { for k, v in aws_kms_key.this : k => v.key_id }
}

output "alias_names" {
  description = "Map of key name to alias."
  value       = { for k, v in aws_kms_alias.this : k => v.name }
}
