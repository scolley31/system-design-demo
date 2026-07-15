output "sns_topic_arn" {
  value = aws_sns_topic.alerts.arn
}

output "dashboard_name" {
  value = aws_cloudwatch_dashboard.main.dashboard_name
}

output "canary_name" {
  value = aws_synthetics_canary.health.name
}

output "log_group" {
  value = aws_cloudwatch_log_group.app.name
}
