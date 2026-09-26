output "alb_dns_name" {
  description = "Point DNS for app/admin domains here if not using Route 53."
  value       = module.platform.alb_dns_name
}

output "acm_validation_records" {
  description = "ACM validation records to create at your DNS provider."
  value       = module.platform.acm_validation_records
}

output "ecr_repository_urls" {
  description = "ECR repositories."
  value       = module.platform.ecr_repository_urls
}

output "one_off_tasks" {
  description = "Migration / db-bootstrap task families and network settings."
  value       = module.platform.one_off_tasks
}

output "buckets" {
  description = "Bucket names."
  value       = module.platform.buckets
}

output "oidc" {
  description = "OIDC settings (non-secret)."
  value       = module.platform.oidc
}

output "secret_arns" {
  description = "Secret ARNs (never values)."
  value       = module.platform.secret_arns
}

output "github_actions" {
  description = "Roles for GitHub Actions."
  value       = module.platform.github_actions
}

output "kms_key_arns" {
  description = "CMKs (ap-south-1) and DR backup key (ap-south-2)."
  value       = merge(module.platform.kms_key_arns, { dr_backup = module.kms_dr.key_arns["backup"] })
}

output "nat_public_ips" {
  description = "Egress IPs."
  value       = module.platform.nat_public_ips
}
