output "ecr_repository_url" {
  value = aws_ecr_repository.app.repository_url
}

output "ecr_repository_arn" {
  value = aws_ecr_repository.app.arn
}

output "gateway_tg_arn" {
  value = aws_lb_target_group.gateway.arn
}

output "gateway_tg_arn_suffix" {
  value = aws_lb_target_group.gateway.arn_suffix
}

output "gateway_asg_name" {
  value = aws_autoscaling_group.gateway.name
}

output "pubsub_asg_name" {
  value = aws_autoscaling_group.pubsub.name
}

output "image_uri_ssm_param" {
  value = aws_ssm_parameter.image_uri.name
}
