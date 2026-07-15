variable "project" { type = string }
variable "vpc_id" { type = string }
variable "private_subnet_ids" { type = list(string) }
variable "rds_sg_id" { type = string }

variable "database_url" {
  type      = string
  sensitive = true
}
variable "expired_grace_days" { type = number }
variable "deleted_retention_days" { type = number }
variable "schedule" { type = string } # EventBridge Scheduler 運算式，例 cron(0 3 * * ? *)
