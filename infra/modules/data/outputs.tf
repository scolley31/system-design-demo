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
