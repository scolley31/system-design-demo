output "user_pool_id" {
  value = aws_cognito_user_pool.this.id
}

output "client_id" {
  value = aws_cognito_user_pool_client.spa.id
}

output "issuer" {
  value = local.issuer
}

output "hosted_ui_domain" {
  value = local.hosted_domain
}
