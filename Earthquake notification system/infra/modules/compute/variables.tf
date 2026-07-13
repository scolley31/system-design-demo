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

# worker ASG（消費 Redis Streams，不掛 ALB）
variable "worker_asg_min_size" { type = number }
variable "worker_asg_max_size" { type = number }
variable "worker_asg_desired_capacity" { type = number }

variable "ssm_prefix" { type = string }
variable "db_secret_arn" { type = string }

# DynamoDB user_location（授予 EC2 讀寫 + 注入 LOCATION_TABLE）
variable "location_table_name" { type = string }
variable "location_table_arn" { type = string }

# H3 geo-index 解析度（注入 H3_RES）
variable "h3_res" { type = number }
