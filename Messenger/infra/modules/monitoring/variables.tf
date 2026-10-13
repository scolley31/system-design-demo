variable "project" { type = string }
variable "region" { type = string }
variable "alert_email" { type = string }

# 各資源識別子（從其他 module 輸出傳入）
variable "alb_arn_suffix" { type = string }
variable "tg_arn_suffix" { type = string }
variable "gateway_asg_name" { type = string }
variable "pubsub_asg_name" { type = string }
variable "etcd_instance_id" { type = string }   # StatusCheckFailed 告警（demo SPOF）
variable "message_table_name" { type = string } # DynamoDB message（throttle 告警）
variable "cloudfront_distribution_id" { type = string }
