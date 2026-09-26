output "id" {
  description = "Bucket name."
  value       = aws_s3_bucket.this.bucket
}

output "arn" {
  description = "Bucket ARN."
  value       = aws_s3_bucket.this.arn
}

output "bucket_regional_domain_name" {
  description = "Regional domain name."
  value       = aws_s3_bucket.this.bucket_regional_domain_name
}

# Posture outputs (consumed by terraform test assertions and by compositions for summaries).
output "public_access_block" {
  description = "Block Public Access settings."
  value = {
    block_public_acls       = aws_s3_bucket_public_access_block.this.block_public_acls
    block_public_policy     = aws_s3_bucket_public_access_block.this.block_public_policy
    ignore_public_acls      = aws_s3_bucket_public_access_block.this.ignore_public_acls
    restrict_public_buckets = aws_s3_bucket_public_access_block.this.restrict_public_buckets
  }
}

output "sse_algorithm" {
  description = "Default encryption algorithm (aws:kms or AES256)."
  value       = one(one(aws_s3_bucket_server_side_encryption_configuration.this.rule).apply_server_side_encryption_by_default).sse_algorithm
}

output "kms_key_arn" {
  description = "Default encryption key."
  value       = var.kms_key_arn
}

output "versioning_status" {
  description = "Versioning status."
  value       = one(aws_s3_bucket_versioning.this.versioning_configuration).status
}

output "object_lock_enabled" {
  description = "Whether Object Lock is enabled."
  value       = aws_s3_bucket.this.object_lock_enabled
}

output "object_lock_mode" {
  description = "Default Object Lock mode (null when disabled)."
  value       = try(var.object_lock.mode, null)
}

output "object_lock_years" {
  description = "Default Object Lock retention in years (null when days are used)."
  value       = try(var.object_lock.years, null)
}

output "object_lock_days" {
  description = "Default Object Lock retention in days (null when years are used)."
  value       = try(var.object_lock.days, null)
}
