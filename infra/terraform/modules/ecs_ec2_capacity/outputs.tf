output "capacity_provider_name" {
  description = "ECS capacity provider (add it to the cluster and name it in the service's strategy)."
  value       = aws_ecs_capacity_provider.this.name
}

output "cpu_architecture" {
  description = "ARM64 or X86_64: the task definitions placed here must use it."
  value       = local.arm64 ? "ARM64" : "X86_64"
}

output "placement_constraint" {
  description = "memberOf expression that pins a service to these instances."
  value       = "attribute:${var.instance_attribute.name} == ${var.instance_attribute.value}"
}

output "autoscaling_group_name" {
  description = "Auto Scaling group (instance refresh, activity history)."
  value       = aws_autoscaling_group.this.name
}

output "instance_role_arn" {
  description = "Container instance role."
  value       = aws_iam_role.instance.arn
}

output "user_data" {
  description = "Rendered user data (asserted by tests; contains no secrets)."
  value       = local.user_data
}

output "posture" {
  description = "Security posture summary (asserted by tests)."
  value = {
    ami_parameter        = local.ami_parameter
    imdsv2_required      = one(aws_launch_template.this.metadata_options).http_tokens == "required"
    imds_hop_limit       = one(aws_launch_template.this.metadata_options).http_put_response_hop_limit
    root_encrypted       = one(one(aws_launch_template.this.block_device_mappings).ebs).encrypted
    root_kms_key_id      = one(one(aws_launch_template.this.block_device_mappings).ebs).kms_key_id
    key_name             = aws_launch_template.this.key_name
    public_ip            = one(aws_launch_template.this.network_interfaces).associate_public_ip_address
    seccomp_profile_hash = sha256(local.seccomp_profile)
  }
}
