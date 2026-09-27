output "key_arns" {
  description = "Map of key name to key ARN."
  value       = { for k, v in aws_kms_key.this : k => v.arn }
}

output "key_ids" {
  description = "Map of key name to key ID."
  value       = { for k, v in aws_kms_key.this : k => v.key_id }
}

output "key_properties" {
  description = "Map of key name to its spec, usage and rotation (known at plan; asserted by stack tests)."
  value = {
    for k, v in var.keys : k => {
      key_spec  = v.key_spec
      key_usage = v.key_usage
      rotation  = local.rotating[k]
    }
  }
}

output "alias_names" {
  description = "Map of key name to alias."
  value       = { for k, v in aws_kms_alias.this : k => v.name }
}
