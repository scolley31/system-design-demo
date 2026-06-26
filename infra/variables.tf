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
