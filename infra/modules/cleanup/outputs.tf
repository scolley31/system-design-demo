output "lambda_arn" {
  value = aws_lambda_function.cleanup.arn
}

output "lambda_name" {
  value = aws_lambda_function.cleanup.function_name
}

output "schedule_name" {
  value = aws_scheduler_schedule.cleanup.name
}
