output "vpc_id" {
  value = module.network.vpc_id
}

output "private_subnet_ids" {
  value = module.network.private_subnet_ids
}

output "public_subnet_ids" {
  value = module.network.public_subnet_ids
}

output "db_endpoint" {
  value = module.data.db_endpoint
}

output "redis_endpoint" {
  value = module.data.redis_endpoint
}

output "database_url_ssm_param" {
  value = module.data.database_url_ssm
}

output "redis_url_ssm_param" {
  value = module.data.redis_url_ssm
}

# --- compute / edge / cicd ---
output "ecr_repository_url" {
  value = module.compute.ecr_repository_url
}

output "alb_dns_name" {
  value = module.compute.alb_dns_name
}

output "asg_name" {
  value = module.compute.asg_name
}

output "image_uri_ssm_param" {
  value = module.compute.image_uri_ssm_param
}

output "cloudfront_url" {
  description = "對外服務網址（BASE_URL）"
  value       = module.edge.cloudfront_url
}

output "qr_bucket" {
  value = module.edge.qr_bucket
}

output "waf_web_acl_arn" {
  value = module.edge.waf_web_acl_arn
}

output "cognito_user_pool_id" {
  value = module.auth.user_pool_id
}

output "cognito_client_id" {
  value = module.auth.client_id
}

output "cognito_hosted_ui_domain" {
  value = module.auth.hosted_ui_domain
}

output "gha_deploy_role_arn" {
  description = "GitHub Actions OIDC 用的 role ARN（填進 workflow / repo variable）"
  value       = module.cicd.deploy_role_arn
}
