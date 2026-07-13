variable "project" {
  type    = string
  default = "quake"
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

# --- RDS (config + notification_outbox) ---
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
  default = "quake"
}

variable "db_username" {
  type    = string
  default = "quake"
}

# --- ElastiCache (Redis) ---
# 單一 Redis 同時承載：geo-index（cell→devices）+ per-channel Redis Streams + supersession cache。
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

# worker ASG（消費 Redis Streams，送達 channel；不掛 ALB）
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

# H3 geo-index 解析度（app 端 geo.py 讀 H3_RES；預設 5）
variable "h3_res" {
  type    = number
  default = 5
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
# notification_outbox 保留窗；超過就物理刪除（user_location 由 DynamoDB TTL 處理，不在此清）。
variable "outbox_retention_days" {
  type    = number
  default = 30
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
