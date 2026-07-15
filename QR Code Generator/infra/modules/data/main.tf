# ---------- DB 密碼（隨機，存 Secrets Manager）----------
resource "random_password" "db" {
  length  = 24
  special = false
}

resource "aws_secretsmanager_secret" "db" {
  name_prefix = "${var.project}/db-"
}

resource "aws_secretsmanager_secret_version" "db" {
  secret_id = aws_secretsmanager_secret.db.id
  secret_string = jsonencode({
    username = var.db_username
    password = random_password.db.result
  })
}

# ---------- RDS PostgreSQL ----------
resource "aws_db_subnet_group" "this" {
  name       = "${var.project}-db-subnets"
  subnet_ids = var.private_subnet_ids
}

resource "aws_db_instance" "this" {
  identifier     = "${var.project}-pg"
  engine         = "postgres"
  engine_version = var.db_engine_version
  instance_class = var.db_instance_class

  allocated_storage = var.db_allocated_storage
  storage_type      = "gp3"
  storage_encrypted = true

  db_name  = var.db_name
  username = var.db_username
  password = random_password.db.result

  db_subnet_group_name   = aws_db_subnet_group.this.name
  vpc_security_group_ids = [var.rds_sg_id]
  multi_az               = var.db_multi_az
  publicly_accessible    = false

  backup_retention_period = 7
  skip_final_snapshot     = true
  deletion_protection     = false # demo；production 建議 true

  apply_immediately = true
}

# ---------- ElastiCache Redis ----------
resource "aws_elasticache_subnet_group" "this" {
  name       = "${var.project}-redis-subnets"
  subnet_ids = var.private_subnet_ids
}

resource "aws_elasticache_replication_group" "this" {
  replication_group_id = "${var.project}-redis"
  description          = "${var.project} redirect cache"
  engine               = "redis"
  engine_version       = var.redis_engine_version
  node_type            = var.redis_node_type
  num_cache_clusters   = 1
  port                 = 6379

  subnet_group_name  = aws_elasticache_subnet_group.this.name
  security_group_ids = [var.redis_sg_id]

  at_rest_encryption_enabled = true
  automatic_failover_enabled = false # 單節點 demo；多節點時設 true
}

# ---------- 連線字串寫入 SSM（compute 注入容器）----------
resource "aws_ssm_parameter" "database_url" {
  name  = "/${var.project}/DATABASE_URL"
  type  = "SecureString"
  value = "postgresql+psycopg2://${var.db_username}:${random_password.db.result}@${aws_db_instance.this.address}:5432/${var.db_name}"
}

resource "aws_ssm_parameter" "redis_url" {
  name  = "/${var.project}/REDIS_URL"
  type  = "String"
  value = "redis://${aws_elasticache_replication_group.this.primary_endpoint_address}:6379/0"
}
