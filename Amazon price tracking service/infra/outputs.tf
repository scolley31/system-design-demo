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

output "price_table_name" {
  description = "DynamoDB price 表名（app 的 PRICE_TABLE）"
  value       = module.data.price_table_name
}

output "price_stream_arn" {
  description = "DynamoDB Streams ARN（CDC 來源；app cdc.py 以 describe_table 自行取得，此處供維運檢視）"
  value       = module.data.price_stream_arn
}

output "ses_identity_arn" {
  description = "SES 寄件人 identity ARN（ses_from_email 留空時為空字串）"
  value       = module.data.ses_identity_arn
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

output "worker_asg_name" {
  value = module.compute.worker_asg_name
}

output "image_uri_ssm_param" {
  value = module.compute.image_uri_ssm_param
}

output "cloudfront_url" {
  description = "對外服務網址（BASE_URL）"
  value       = module.edge.cloudfront_url
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

output "cleanup_lambda_name" {
  value = module.cleanup.lambda_name
}

output "cleanup_schedule_name" {
  value = module.cleanup.schedule_name
}

output "alerts_sns_topic_arn" {
  value = module.monitoring.sns_topic_arn
}

output "dashboard_name" {
  value = module.monitoring.dashboard_name
}

output "gha_deploy_role_arn" {
  description = "GitHub Actions OIDC 用的 role ARN（填進 workflow / repo variable）"
  value       = module.cicd.deploy_role_arn
}
