variable "project" {
  type    = string
  default = "qrcode"
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

# --- RDS ---
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
  default = "qr"
}

variable "db_username" {
  type    = string
  default = "qr"
}

# --- ElastiCache (Redis) ---
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
variable "cleanup_expired_grace_days" {
  type    = number
  default = 30 # 過期後保留天數才物理刪除
}
variable "cleanup_deleted_retention_days" {
  type    = number
  default = 30 # 軟刪除後保留天數才物理刪除
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
