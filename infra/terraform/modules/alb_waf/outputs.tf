output "alb_arn" {
  description = "ALB ARN."
  value       = aws_lb.this.arn
}

output "alb_arn_suffix" {
  description = "ALB ARN suffix (CloudWatch dimension)."
  value       = aws_lb.this.arn_suffix
}

output "alb_dns_name" {
  description = "ALB DNS name (CNAME/alias target when not using Route 53)."
  value       = aws_lb.this.dns_name
}

output "alb_zone_id" {
  description = "ALB hosted zone ID."
  value       = aws_lb.this.zone_id
}

output "security_group_id" {
  description = "ALB security group."
  value       = aws_security_group.alb.id
}

output "web_target_group_arn" {
  description = "Web target group."
  value       = aws_lb_target_group.web.arn
}

output "web_target_group_arn_suffix" {
  description = "Web target group ARN suffix (CloudWatch dimension)."
  value       = aws_lb_target_group.web.arn_suffix
}

output "api_target_group_arn" {
  description = "API target group (fleet heartbeat only; null otherwise)."
  value       = var.expose_fleet_heartbeat ? aws_lb_target_group.api[0].arn : null
}

output "web_acl_arn" {
  description = "WAF web ACL ARN."
  value       = aws_wafv2_web_acl.this.arn
}

output "certificate_arn" {
  description = "ACM certificate ARN."
  value       = aws_acm_certificate.this.arn
}

output "acm_validation_records" {
  description = "DNS records to create at your DNS provider when route53_zone_id is null."
  value = [for o in aws_acm_certificate.this.domain_validation_options : {
    name  = o.resource_record_name
    type  = o.resource_record_type
    value = o.resource_record_value
  }]
}
