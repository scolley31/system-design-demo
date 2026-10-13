variable "project" { type = string }
variable "region" { type = string }
variable "vpc_id" { type = string }
variable "private_subnet_ids" { type = list(string) }
variable "gateway_sg_id" { type = string }
variable "pubsub_sg_id" { type = string }
variable "instance_type" { type = string }

# gateway ASG（掛 ALB 後的 WebSocket 入口）
variable "gateway_asg_min_size" { type = number }
variable "gateway_asg_max_size" { type = number }
variable "gateway_asg_desired_capacity" { type = number }

# pubsub ASG（chat shard owner，不掛 ALB）
variable "pubsub_asg_min_size" { type = number }
variable "pubsub_asg_max_size" { type = number }
variable "pubsub_asg_desired_capacity" { type = number }

variable "ssm_prefix" { type = string }
variable "etcd_url_ssm_param" { type = string } # 來自 etcd 模組（/messenger/ETCD_URL；也讓 ASG 晚於 etcd 建立）

# DynamoDB（DYNAMO_TABLE_PREFIX + IAM 授權範圍）
variable "table_prefix" { type = string }
variable "table_arns" { type = map(string) }

# App 設定（注入容器 env；見 app/config.py）
variable "num_shards" { type = number }
variable "lease_ttl_s" { type = number }
variable "max_participants" { type = number }
variable "allow_debug" { type = bool }
variable "auth_enabled" { type = bool }
