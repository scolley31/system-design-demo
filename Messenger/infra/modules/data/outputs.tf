# app 的 DYNAMO_TABLE_PREFIX
output "table_prefix" {
  value = "${var.project}-"
}

# { chat = "messenger-chat", ... }
output "table_names" {
  value = { for k, t in aws_dynamodb_table.this : k => t.name }
}

# { chat = "arn:...", ... }（compute 的 IAM policy 用）
output "table_arns" {
  value = { for k, t in aws_dynamodb_table.this : k => t.arn }
}
