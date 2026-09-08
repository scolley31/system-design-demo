variable "project" { type = string }
variable "vpc_id" { type = string }
variable "private_subnet_ids" { type = list(string) }
variable "rds_sg_id" { type = string }

variable "database_url" {
  type      = string
  sensitive = true
}
variable "outbox_retention_days" { type = number }    # notification_outbox 保留天數
variable "crawl_log_retention_days" { type = number } # crawl_log + 已結案 price_reports 保留天數
variable "schedule" { type = string }                 # EventBridge Scheduler 運算式，例 cron(0 3 * * ? *)
