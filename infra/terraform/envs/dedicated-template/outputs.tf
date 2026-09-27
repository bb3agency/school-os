output "public_ip" {
  description = "Elastic IP: create A records for the platform hostname and the school's custom domain."
  value       = module.host.public_ip
}

output "dns_instructions" {
  description = "DNS records that must exist before Caddy can obtain certificates."
  value = compact([
    "${var.domain}. A ${module.host.public_ip}${var.route53_zone_id == null ? "" : " (managed in Route 53)"}",
    var.custom_domain == "" ? "" : "${var.custom_domain}. A ${module.host.public_ip} (school creates this at its DNS provider)",
  ])
}

output "instance_id" {
  description = "EC2 instance ID."
  value       = module.host.instance_id
}

output "ssm_session_command" {
  description = "Shell access (no SSH)."
  value       = module.host.ssm_session_command
}

output "operator_secret_arn" {
  description = "Set SOS_ANTHROPIC_API_KEY, SOS_HEARTBEAT_KEY_ID and SOS_HEARTBEAT_KEY here (put-secret-value)."
  value       = module.host.operator_secret_arn
}

output "files_bucket" {
  description = "Per-school files bucket."
  value       = module.host.files_bucket
}

output "files_browser_origin" {
  description = "Origin of presigned upload/preview URLs (FILES_ORIGIN, derived the same way by compose.yaml)."
  value       = module.host.files_browser_origin
}

output "backup_bucket" {
  description = "Backup bucket (ap-south-2)."
  value       = module.backup_bucket.id
}

output "kms_key_arns" {
  description = "Per-school CMKs; schedule deletion of data and backup to crypto-shred on decommission (export the audit_signing public key first)."
  value = {
    data          = module.kms.key_arns["data"]
    backup        = module.kms_backup.key_arns["backup"]
    audit_signing = module.host.audit_signing_key_arn
  }
}

output "oidc" {
  description = "Per-school OIDC settings (non-secret)."
  value = {
    issuer        = module.cognito.tenant_issuer
    client_id     = module.cognito.tenant_client_id
    hosted_domain = module.cognito.tenant_hosted_domain
  }
}

output "instance_role_arn" {
  description = "Instance role ARN (for cross-account ECR/artifact access when images live elsewhere)."
  value       = module.host.instance_role_arn
}
