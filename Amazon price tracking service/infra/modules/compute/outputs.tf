output "ecr_repository_url" {
  value = aws_ecr_repository.app.repository_url
}

output "ecr_repository_arn" {
  value = aws_ecr_repository.app.arn
}

output "alb_arn" {
  value = aws_lb.this.arn
}

output "alb_listener_arn" {
  value = aws_lb_listener.http.arn
}

output "alb_dns_name" {
  value = aws_lb.this.dns_name
}

output "alb_arn_suffix" {
  value = aws_lb.this.arn_suffix
}

output "tg_arn_suffix" {
  value = aws_lb_target_group.app.arn_suffix
}

# monitoring 的 EC2 CPU 告警用 api ASG（對外服務那組）
output "asg_name" {
  value = aws_autoscaling_group.api.name
}

output "worker_asg_name" {
  value = aws_autoscaling_group.worker.name
}

output "image_uri_ssm_param" {
  value = aws_ssm_parameter.image_uri.name
}
