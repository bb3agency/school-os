output "instance_id" {
  description = "EC2 instance ID (connect with: aws ssm start-session --target <id>)."
  value       = aws_instance.host.id
}

output "public_ip" {
  description = "Elastic IP. Point public_host (and the school's custom domain, if any) here with an A record."
  value       = aws_eip.host.public_ip
}

output "files_bucket" {
  description = "Per-school files bucket."
  value       = module.files.id
}

output "generated_secret_arn" {
  description = "ARN of the generated-credentials secret (value never output)."
  value       = aws_secretsmanager_secret.generated.arn
}

output "operator_secret_arn" {
  description = "ARN of the operator-supplied secret; set real values with put-secret-value."
  value       = aws_secretsmanager_secret.operator.arn
}

output "instance_role_arn" {
  description = "Instance role (grant cross-account ECR pull to this ARN if images live in another account)."
  value       = aws_iam_role.host.arn
}

output "data_volume_id" {
  description = "Data EBS volume ID."
  value       = aws_ebs_volume.data.id
}

output "ssm_session_command" {
  description = "Shell access without SSH."
  value       = "aws ssm start-session --region ${data.aws_region.current.region} --target ${aws_instance.host.id}"
}

output "posture" {
  description = "Security posture summary (asserted by tests)."
  value = {
    imdsv2_required      = one(aws_instance.host.metadata_options).http_tokens == "required"
    root_encrypted       = one(aws_instance.host.root_block_device).encrypted
    data_encrypted       = aws_ebs_volume.data.encrypted
    public_ip_on_launch  = aws_instance.host.associate_public_ip_address
    files_bucket_sse     = module.files.sse_algorithm
    files_bucket_private = module.files.public_access_block
  }
}
