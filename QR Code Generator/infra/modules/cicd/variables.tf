variable "project" { type = string }
variable "region" { type = string }
variable "github_repo" { type = string } # "owner/repo"
variable "ssm_prefix" { type = string }
variable "ecr_repository_arn" { type = string }

# 一個帳號只能有一個 GitHub OIDC provider；若已存在，設 false 並提供 ARN。
variable "create_oidc_provider" {
  type    = bool
  default = true
}
variable "existing_oidc_provider_arn" {
  type    = string
  default = ""
}
