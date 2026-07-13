variable "project" { type = string }
variable "region" { type = string }
variable "ssm_prefix" { type = string }
variable "api_id" { type = string }         # 來自 edge：API Gateway HTTP API
variable "integration_id" { type = string } # 來自 edge：ALB VPC Link integration
variable "cloudfront_url" { type = string } # 來自 edge：登入 callback/redirect
