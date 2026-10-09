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

# The origin of presigned URLs for this bucket: the API pins boto3 to virtual-hosted, regional
# addressing (apps/api/app/documents/storage.py, asserted by tests/documents/test_storage_origin.py).
# Known at plan time (name + region), so it can feed FILES_ORIGIN (web CSP img-src/connect-src).
output "browser_origin" {
  description = "https://<bucket>.s3.<region>.amazonaws.com: origin of presigned POST/GET URLs (FILES_ORIGIN)."
  value       = "https://${var.name}.s3.${data.aws_region.current.region}.amazonaws.com"

  precondition {
    condition     = !strcontains(var.name, ".")
    error_message = "Virtual-hosted https presigned URLs need a bucket name without dots (the TLS wildcard covers one label)."
  }
}

output "cors_rules" {
  description = "Rendered CORS rules (empty list when the bucket has no CORS configuration)."
  value = flatten([
    for c in aws_s3_bucket_cors_configuration.this : [
      for r in c.cors_rule : {
        allowed_origins = toset(r.allowed_origins)
        allowed_methods = toset(r.allowed_methods)
        allowed_headers = r.allowed_headers == null ? toset([]) : toset(r.allowed_headers)
        expose_headers  = r.expose_headers == null ? toset([]) : toset(r.expose_headers)
        max_age_seconds = r.max_age_seconds
      }
    ]
  ])
}

output "lifecycle_rules" {
  description = "Rendered lifecycle rules by id: tag filter, expiration and noncurrent-version expiration days (empty map without rules; asserted by tests)."
  value = merge([
    for c in aws_s3_bucket_lifecycle_configuration.this : {
      for r in c.rule : r.id => {
        tags            = merge(flatten([for f in r.filter : [for t in f.tag : { (t.key) = t.value }]])...)
        expiration_days = one([for e in r.expiration : e.days])
        noncurrent_days = one([for n in r.noncurrent_version_expiration : n.noncurrent_days])
      }
    }
  ]...)
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
