variable "project" { type = string }
variable "region" { type = string }
variable "subnet_id" { type = string }   # 私有子網（第一個 AZ）
variable "subnet_cidr" { type = string } # 用來算固定 private_ip（cidrhost(cidr, 50)）
variable "etcd_sg_id" { type = string }
variable "instance_type" { type = string }
variable "ssm_prefix" { type = string }

# 目前只支援 1（demo SPOF）；production 3 台見 infra/README.md
variable "node_count" {
  type = number
  validation {
    condition     = var.node_count == 1
    error_message = "etcd 模組目前只支援單節點。"
  }
}
