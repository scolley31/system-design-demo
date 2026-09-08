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
# 承載 Redis Streams 通道：crawl / price_changed / notify:sse / notify:email（app 端 queue.py）。
resource "aws_elasticache_subnet_group" "this" {
  name       = "${var.project}-redis-subnets"
  subnet_ids = var.private_subnet_ids
}

resource "aws_elasticache_replication_group" "this" {
  replication_group_id = "${var.project}-redis"
  description          = "${var.project} Redis Streams: crawl / price_changed / notify:sse / notify:email"
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

# ---------- DynamoDB (price) ----------
# 價格歷史是 append-only 時序資料（每個商品每次爬取一筆）→ DynamoDB on-demand。
# PK product_id + SK ts（epoch ms）：查某商品區間用 Query 即可；app 的 DynamoPriceStore 讀
# PRICE_TABLE（見 app/price_store.py）。
# 開啟 DynamoDB Streams（NEW_IMAGE）作為 CDC 來源：worker 的 DynamoStreamTailer（app/cdc.py）
# 直接 tail stream 並在 Postgres cdc_checkpoint 記每個 shard 的位置；不用 Lambda。
# 不設 TTL：歷史價格永久保留（讀路徑靠 price_aggregations 預先彙總）。
resource "aws_dynamodb_table" "price" {
  name             = "${var.project}-price"
  billing_mode     = "PAY_PER_REQUEST"
  hash_key         = "product_id"
  range_key        = "ts"
  stream_enabled   = true
  stream_view_type = "NEW_IMAGE"

  attribute {
    name = "product_id"
    type = "S"
  }

  attribute {
    name = "ts"
    type = "N"
  }
}

# ---------- SES 寄件人 identity（選用）----------
# ses_from_email 留空 → 不建；有值 → 建 email identity（建立後需到該信箱點驗證信）。
# SES sandbox 內收件人也要逐一驗證；正式對外寄信需申請移出 sandbox。
resource "aws_ses_email_identity" "from" {
  count = var.ses_from_email != "" ? 1 : 0
  email = var.ses_from_email
}
