output "db_endpoint" {
  value = aws_db_instance.this.address
}

output "db_instance_id" {
  value = aws_db_instance.this.identifier
}

output "redis_cluster_id" {
  value = tolist(aws_elasticache_replication_group.this.member_clusters)[0]
}

output "redis_endpoint" {
  value = aws_elasticache_replication_group.this.primary_endpoint_address
}

output "db_secret_arn" {
  value = aws_secretsmanager_secret.db.arn
}

output "database_url_ssm" {
  value = aws_ssm_parameter.database_url.name
}

output "database_url" {
  value     = aws_ssm_parameter.database_url.value
  sensitive = true
}

output "redis_url_ssm" {
  value = aws_ssm_parameter.redis_url.name
}

output "price_table_name" {
  value = aws_dynamodb_table.price.name
}

output "price_table_arn" {
  value = aws_dynamodb_table.price.arn
}

output "price_stream_arn" {
  value = aws_dynamodb_table.price.stream_arn
}

output "ses_identity_arn" {
  value = length(aws_ses_email_identity.from) > 0 ? aws_ses_email_identity.from[0].arn : ""
}
