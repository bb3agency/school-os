output "replica_bucket" {
  description = "Replica bucket name (backup region)."
  value       = module.replica.id
}

output "replica_bucket_arn" {
  description = "Replica bucket ARN."
  value       = local.replica_arn
}

output "replication_role_name" {
  description = "Replication role (trusted by s3.amazonaws.com only)."
  value       = aws_iam_role.replication.name
}

output "posture" {
  description = "Security posture summary (asserted by tests)."
  value = {
    object_lock_enabled = module.replica.object_lock_enabled
    object_lock_mode    = module.replica.object_lock_mode
    object_lock_days    = module.replica.object_lock_days
    versioning          = module.replica.versioning_status
    sse_algorithm       = module.replica.sse_algorithm
    kms_key_arn         = module.replica.kms_key_arn
    public_access_block = module.replica.public_access_block
    replicated_prefix   = var.prefix
    rule_status         = aws_s3_bucket_replication_configuration.this.rule[0].status
    delete_markers      = aws_s3_bucket_replication_configuration.this.rule[0].delete_marker_replication[0].status
    replica_kms_key_id  = aws_s3_bucket_replication_configuration.this.rule[0].destination[0].encryption_configuration[0].replica_kms_key_id
    replica_region      = data.aws_region.replica.region
  }
}
