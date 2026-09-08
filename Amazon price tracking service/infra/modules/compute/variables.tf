variable "project" { type = string }
variable "region" { type = string }
variable "vpc_id" { type = string }
variable "private_subnet_ids" { type = list(string) }
variable "ec2_sg_id" { type = string }
variable "alb_sg_id" { type = string }
variable "instance_type" { type = string }

# api ASG（掛 ALB 後的 web / orchestrator）
variable "asg_min_size" { type = number }
variable "asg_max_size" { type = number }
variable "asg_desired_capacity" { type = number }

# worker ASG（crawler + CDC tailer + notify workers，不掛 ALB）
variable "worker_asg_min_size" { type = number }
variable "worker_asg_max_size" { type = number }
variable "worker_asg_desired_capacity" { type = number }

variable "ssm_prefix" { type = string }
variable "db_secret_arn" { type = string }

# DynamoDB price 表（授予 EC2 讀寫 + Streams 讀取 + 注入 PRICE_TABLE）
variable "price_table_name" { type = string }
variable "price_table_arn" { type = string }
variable "price_stream_arn" { type = string } # 供 IAM / 除錯參考（policy 以 table ARN/stream/* 授權）

# App 設定（注入容器 env；見 app/config.py）
variable "amazon_domain" { type = string }
variable "crawl_rps" { type = number }
variable "ses_from_email" { type = string } # 空字串 = 不附加 SES 權限
variable "suspicious_drop_pct" { type = number }
variable "min_change_pct" { type = number }
