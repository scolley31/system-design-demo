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

output "asg_name" {
  value = aws_autoscaling_group.app.name
}

output "image_uri_ssm_param" {
  value = aws_ssm_parameter.image_uri.name
}

output "ec2_role_name" {
  value = aws_iam_role.ec2.name
}
