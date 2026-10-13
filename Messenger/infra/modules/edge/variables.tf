variable "project" { type = string }
variable "public_subnet_ids" { type = list(string) }
variable "alb_sg_id" { type = string }
variable "gateway_tg_arn" { type = string } # 來自 compute：gateway target group

# Rate limiting（per-IP WAF）
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
