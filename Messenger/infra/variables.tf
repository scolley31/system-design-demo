variable "project" {
  type    = string
  default = "messenger"
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

# --- etcd（service discovery + shard lease；app/registry.py）---
variable "etcd_instance_type" {
  type    = string
  default = "t4g.micro" # ARM (Graviton)；etcd 只存 nodes/ + shards/ 幾百個 key，micro 足夠
}

# ⚠ demo 只跑 1 台 etcd = 單點故障（SPOF）：它掛掉時 pub/sub 無法 keepalive → lease 過期 →
#   所有 shard 失去 owner，publish 一律 UNAVAILABLE，直到 etcd 回來。
# production = 3 台跨 3 AZ（--initial-cluster 靜態成員，Raft 容忍 1 台故障）；
# 或改用 DynamoDB lock table 做 lease（conditional put + TTL 欄位 + 心跳更新），免維運但要改 registry。
variable "etcd_node_count" {
  type    = number
  default = 1
  validation {
    condition     = var.etcd_node_count == 1
    error_message = "目前 etcd 模組只支援單節點（demo SPOF）；3 節點叢集見 infra/README.md「etcd 單點」一節。"
  }
}

# --- Compute ---
variable "ec2_instance_type" {
  type    = string
  default = "t4g.small" # ARM (Graviton)；gateway / pubsub 共用
}

# gateway ASG（對外 WebSocket 入口，stateless，掛在 ALB 後）
variable "gateway_asg_min_size" {
  type    = number
  default = 1
}

variable "gateway_asg_max_size" {
  type    = number
  default = 4
}

variable "gateway_asg_desired_capacity" {
  type    = number
  default = 2
}

# pubsub ASG（chat shard owner；不掛 ALB，gateway 經 etcd 找到 owner 後直連）
# min 2：一台下線時另一台接手全部 shard，demo failover 才看得到。
variable "pubsub_asg_min_size" {
  type    = number
  default = 2
}

variable "pubsub_asg_max_size" {
  type    = number
  default = 4
}

variable "pubsub_asg_desired_capacity" {
  type    = number
  default = 2
}

# --- App 設定（注入容器 env；對應 app/config.py）---
# chat → shard 的分片數（NUM_SHARDS）。所有節點必須一致；改值 = 重新分片，需整批重啟。
variable "num_shards" {
  type    = number
  default = 64 # 正式版 4096（DESIGN 附錄 A）
}

# etcd lease TTL（LEASE_TTL_S）：pub/sub 硬掛時，shard 最多這麼久後被接手。
variable "lease_ttl_s" {
  type    = number
  default = 5
}

# 群聊人數上限（MAX_PARTICIPANTS；PDF：100 人）
variable "max_participants" {
  type    = number
  default = 100
}

# /api/debug/kill 等除錯端點（ALLOW_DEBUG）。對外環境預設關閉。
variable "allow_debug" {
  type    = bool
  default = false
}

# AUTH_ENABLED：true → WebSocket 連線必須帶 ?token=<Cognito id token>。
# demo 預設 false（scripts/smoke_ws.py 以 ?user_id= 連線）；Cognito 資源照建，切 true 即生效。
variable "auth_enabled" {
  type    = bool
  default = false
}

# --- CI/CD ---
variable "github_repo" {
  type    = string
  default = "scolley31/system-design-demo"
}

# --- Rate limiting（WAF on CloudFront）---
# WebSocket 只有「握手」那一個 request 會被 WAF 計數，連線內的 frame 不算；
# 因此 rate limit 擋的是重連風暴 / HTTP API 濫用，不是訊息量（訊息量在 gateway 端限制）。
variable "waf_rate_limit_api" {
  type    = number
  default = 300 # per-IP / 5 分（/api/*）
}
variable "waf_rate_limit_global" {
  type    = number
  default = 2000 # per-IP / 5 分（全域，含 /ws 握手）
}

# --- Monitoring / Alerting ---
variable "alert_email" {
  type    = string
  default = "scolley31@gmail.com" # SNS 告警通知信箱（建立後需點確認信）
}
