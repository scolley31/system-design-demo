variable "project" { type = string }
variable "private_subnet_ids" { type = list(string) }
variable "rds_sg_id" { type = string }
variable "redis_sg_id" { type = string }

variable "db_engine_version" { type = string }
variable "db_instance_class" { type = string }
variable "db_allocated_storage" { type = number }
variable "db_multi_az" { type = bool }
variable "db_name" { type = string }
variable "db_username" { type = string }

variable "redis_node_type" { type = string }
variable "redis_engine_version" { type = string }
