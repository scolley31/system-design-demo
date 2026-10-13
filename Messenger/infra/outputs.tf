output "vpc_id" {
  value = module.network.vpc_id
}

output "private_subnet_ids" {
  value = module.network.private_subnet_ids
}

output "public_subnet_ids" {
  value = module.network.public_subnet_ids
}

# --- data ---
output "dynamo_table_prefix" {
  description = "DynamoDB 表名前綴（app 的 DYNAMO_TABLE_PREFIX）"
  value       = module.data.table_prefix
}

output "dynamo_table_names" {
  value = module.data.table_names
}

output "dynamo_table_arns" {
  value = module.data.table_arns
}

# --- etcd ---
output "etcd_instance_id" {
  description = "etcd EC2（demo SPOF；aws ssm start-session 進去跑 etcdctl）"
  value       = module.etcd.instance_id
}

output "etcd_url" {
  value = module.etcd.etcd_url
}

# --- compute / edge / cicd ---
output "ecr_repository_url" {
  value = module.compute.ecr_repository_url
}

output "gateway_asg_name" {
  value = module.compute.gateway_asg_name
}

output "pubsub_asg_name" {
  value = module.compute.pubsub_asg_name
}

output "image_uri_ssm_param" {
  value = module.compute.image_uri_ssm_param
}

output "alb_dns_name" {
  description = "直打 ALB 會被回 403（缺 X-Origin-Verify）；對外一律走 cloudfront_url"
  value       = module.edge.alb_dns_name
}

output "cloudfront_url" {
  description = "對外服務網址（/、/api/*、/health）"
  value       = module.edge.cloudfront_url
}

output "ws_url" {
  description = "WebSocket 入口（wss://<cloudfront>/ws）"
  value       = module.edge.ws_url
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
