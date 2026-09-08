variable "project" {
  type    = string
  default = "price"
}

variable "region" {
  type    = string
  default = "ap-northeast-1"
}

variable "azs" {
  type    = list(string)
  default = ["ap-northeast-1a", "ap-northeast-1c"]
}

variable "vpc_cidr" {
  type    = string
  default = "10.0.0.0/16"
}

variable "public_subnet_cidrs" {
  type    = list(string)
  default = ["10.0.0.0/24", "10.0.1.0/24"]
}

variable "private_subnet_cidrs" {
  type    = list(string)
  default = ["10.0.10.0/24", "10.0.11.0/24"]
}

# 成本控制：true = 單一 NAT（省錢，犧牲 AZ 容錯）。
variable "single_nat_gateway" {
  type    = bool
  default = true
}

# --- RDS (products / subscriptions / price_reports / price_aggregations / notification_outbox / cdc_checkpoint / crawl_log) ---
variable "db_engine_version" {
  type    = string
  default = "16"
}

variable "db_instance_class" {
  type    = string
  default = "db.t4g.micro"
}

variable "db_allocated_storage" {
  type    = number
  default = 20
}

variable "db_multi_az" {
  type    = bool
  default = false # 成本控制；production 可設 true
}

variable "db_name" {
  type    = string
  default = "price"
}

variable "db_username" {
  type    = string
  default = "price"
}

# --- ElastiCache (Redis) ---
# 單一 Redis 承載 Redis Streams 通道：crawl / price_changed / notify:sse / notify:email（app 端 queue.py）。
variable "redis_node_type" {
  type    = string
  default = "cache.t4g.micro"
}

variable "redis_engine_version" {
  type    = string
  default = "7.1"
}

# --- Compute (Phase 3) ---
variable "ec2_instance_type" {
  type    = string
  default = "t4g.small" # ARM (Graviton)
}

# api ASG（對外 web / orchestrator，掛在 ALB 後）
variable "asg_min_size" {
  type    = number
  default = 1
}

variable "asg_max_size" {
  type    = number
  default = 2
}

variable "asg_desired_capacity" {
  type    = number
  default = 1
}

# worker ASG（crawler + CDC tailer + notify workers；不掛 ALB）
variable "worker_asg_min_size" {
  type    = number
  default = 1
}

variable "worker_asg_max_size" {
  type    = number
  default = 2
}

variable "worker_asg_desired_capacity" {
  type    = number
  default = 1
}

# --- App 設定（注入容器 env；對應 app/config.py）---
# 爬取目標站台（AMAZON_DOMAIN）
variable "amazon_domain" {
  type    = string
  default = "www.amazon.com"
}

# 每 process 對 Amazon 的請求速率上限（CRAWL_RPS；對齊 1 visit/sec/IP）
variable "crawl_rps" {
  type    = number
  default = 1
}

# SES 寄件人（SES_FROM_EMAIL）。空字串 = 不建 SES identity、不給 SES 權限，email channel 停用。
variable "ses_from_email" {
  type    = string
  default = ""
}

# extension 回報價相對已知價下跌超過此比例 → 可疑，先重爬驗證（SUSPICIOUS_DROP_PCT）
variable "suspicious_drop_pct" {
  type    = number
  default = 0.3
}

# 小於此幅度的價格波動不通知，除非跨越訂閱門檻（MIN_CHANGE_PCT）
variable "min_change_pct" {
  type    = number
  default = 0.01
}

# --- CI/CD (Phase 4) ---
variable "github_repo" {
  type    = string
  default = "scolley31/system-design-demo"
}

# --- Rate limiting ---
variable "waf_rate_limit_api" {
  type    = number
  default = 300 # per-IP / 5 分（/api/*）
}
variable "waf_rate_limit_global" {
  type    = number
  default = 2000 # per-IP / 5 分（全域）
}
variable "apigw_throttle_rate" {
  type    = number
  default = 1000 # API GW 整體 req/s
}
variable "apigw_throttle_burst" {
  type    = number
  default = 2000
}

# --- Data cleanup cron ---
# notification_outbox 保留窗；超過就物理刪除。
# （prices 在 DynamoDB 為 append-only 歷史、price_aggregations 是讀路徑彙總，皆不清。）
variable "outbox_retention_days" {
  type    = number
  default = 30
}

# crawl_log 與已結案的 price_reports（confirmed / rejected / accepted）保留窗。
variable "crawl_log_retention_days" {
  type    = number
  default = 7
}
variable "cleanup_schedule" {
  type    = string
  default = "cron(0 3 * * ? *)" # 每日 03:00 UTC
}

# --- Monitoring / Alerting ---
variable "alert_email" {
  type    = string
  default = "scolley31@gmail.com" # SNS 告警通知信箱（建立後需點確認信）
}
