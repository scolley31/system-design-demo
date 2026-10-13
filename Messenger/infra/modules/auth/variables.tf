variable "project" { type = string }
variable "region" { type = string }
variable "ssm_prefix" { type = string }
variable "cloudfront_url" { type = string } # 來自 edge：登入 callback/redirect
