variable "project" { type = string }
variable "region" { type = string }
variable "alert_email" { type = string }

# 各資源識別子（從其他 module 輸出傳入）
variable "alb_arn_suffix" { type = string }
variable "tg_arn_suffix" { type = string }
variable "asg_name" { type = string }
variable "db_instance_id" { type = string }
variable "redis_cluster_id" { type = string }
variable "api_id" { type = string }
variable "lambda_name" { type = string }
variable "cloudfront_distribution_id" { type = string }
variable "cloudfront_url" { type = string } # canary 探測目標
