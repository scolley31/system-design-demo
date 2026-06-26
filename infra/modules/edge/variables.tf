variable "project" { type = string }
variable "region" { type = string }
variable "vpc_id" { type = string }
variable "private_subnet_ids" { type = list(string) }
variable "alb_listener_arn" { type = string }
variable "ssm_prefix" { type = string }
variable "ec2_role_name" { type = string } # 授予 EC2 對 QR bucket 的 PutObject
