variable "project" { type = string }
variable "region" { type = string }
variable "vpc_id" { type = string }
variable "private_subnet_ids" { type = list(string) }
variable "alb_listener_arn" { type = string }
variable "ssm_prefix" { type = string }
variable "ec2_role_name" { type = string } # 授予 EC2 對 QR bucket 的 PutObject

# Rate limiting（per-IP WAF + API GW 整體節流）
variable "waf_rate_limit_api" {
  type        = number
  description = "WAF per-IP 上限（/api/* 路徑，每 5 分鐘）"
  default     = 300
}
variable "waf_rate_limit_global" {
  type        = number
  description = "WAF per-IP 上限（全域，每 5 分鐘）"
  default     = 2000
}
variable "apigw_throttle_rate" {
  type        = number
  description = "API Gateway 整體 steady-state 速率（req/s）"
  default     = 1000
}
variable "apigw_throttle_burst" {
  type        = number
  description = "API Gateway 整體 burst 上限"
  default     = 2000
}
