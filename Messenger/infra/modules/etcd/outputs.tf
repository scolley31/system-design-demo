output "instance_id" {
  value = aws_instance.etcd[0].id
}

output "private_ip" {
  value = local.private_ip
}

output "etcd_url" {
  value = local.etcd_url
}

output "etcd_url_ssm_param" {
  value = aws_ssm_parameter.etcd_url.name
}
