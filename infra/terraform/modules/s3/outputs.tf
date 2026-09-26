output "files_bucket" {
  description = "Files bucket name (SOS_S3_BUCKET_FILES)."
  value       = module.files.id
}

output "files_bucket_arn" {
  description = "Files bucket ARN."
  value       = module.files.arn
}

output "audit_bucket" {
  description = "Audit archive bucket name (SOS_S3_BUCKET_AUDIT)."
  value       = module.audit_archive.id
}

output "audit_bucket_arn" {
  description = "Audit archive bucket ARN."
  value       = module.audit_archive.arn
}

output "logs_bucket" {
  description = "Access-log bucket name."
  value       = module.logs.id
}

output "artifacts_bucket" {
  description = "Release artifacts bucket name."
  value       = module.artifacts.id
}

output "artifacts_bucket_arn" {
  description = "Release artifacts bucket ARN."
  value       = module.artifacts.arn
}
