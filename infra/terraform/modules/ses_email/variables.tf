variable "name_prefix" {
  description = "Name prefix, e.g. sos-prod."
  type        = string
}

variable "domain" {
  description = "Sending domain verified in SES (Easy DKIM), e.g. schoolos.in or mail.schoolos.in."
  type        = string

  validation {
    condition     = can(regex("^([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\\.)+[a-z]{2,63}$", var.domain))
    error_message = "domain must be a lower-case DNS name, e.g. mail.example.in."
  }
}

variable "route53_zone_id" {
  description = "Hosted zone that holds the domain: the three DKIM CNAMEs are created there. Null = create them by hand from the dkim_records output."
  type        = string
  default     = null
}

variable "reputation_alarms" {
  description = "Create the bounce/complaint reputation alarms (needs alarm_topic_arn)."
  type        = bool
  default     = false
}

variable "alarm_topic_arn" {
  description = "SNS topic for the bounce/complaint reputation alarms."
  type        = string
  default     = null

  validation {
    condition     = !var.reputation_alarms || var.alarm_topic_arn != null
    error_message = "reputation_alarms needs alarm_topic_arn."
  }
}

variable "bounce_rate_alarm" {
  description = "Account bounce rate (0-1) that alarms. SES reviews accounts at 5% and may pause sending at 10%."
  type        = number
  default     = 0.05
}

variable "complaint_rate_alarm" {
  description = "Account complaint rate (0-1) that alarms. SES reviews accounts at 0.1% and may pause sending at 0.5%."
  type        = number
  default     = 0.001
}

variable "tags" {
  description = "Extra tags."
  type        = map(string)
  default     = {}
}
